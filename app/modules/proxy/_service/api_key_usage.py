from __future__ import annotations

import asyncio
import logging
import sys
import time
from collections.abc import Coroutine, Mapping
from typing import Any, Protocol, cast

import anyio

from app.core.exceptions import ProxyAuthError, ProxyRateLimitError
from app.core.openai.models import CompactResponsePayload
from app.core.utils.request_id import get_request_id
from app.modules.api_keys.service import (
    ApiKeyData,
    ApiKeyInvalidError,
    ApiKeyRateLimitExceededError,
    ApiKeyRequestUsageBudget,
    ApiKeysService,
    ApiKeyUsageReservationData,
)
from app.modules.proxy._service.support import (
    _ApiKeyReservationTouchState,
    _consume_api_key_reservation_heartbeat_result,
    _StreamSettlement,
    _WebSocketRequestState,
)
from app.modules.proxy.repo_bundle import ProxyRepoFactory

logger = logging.getLogger("app.modules.proxy.service")

_API_KEY_RESERVATION_HEARTBEAT_SECONDS = 300.0


def _service_api_keys_service() -> type[ApiKeysService]:
    service_module = sys.modules.get("app.modules.proxy.service")
    if service_module is not None:
        return cast(type[ApiKeysService], getattr(service_module, "ApiKeysService", ApiKeysService))
    return ApiKeysService


def _api_key_reservation_heartbeat_seconds() -> float:
    service_module = sys.modules.get("app.modules.proxy.service")
    if service_module is not None:
        value = getattr(
            service_module,
            "_API_KEY_RESERVATION_HEARTBEAT_SECONDS",
            _API_KEY_RESERVATION_HEARTBEAT_SECONDS,
        )
        try:
            return float(value)
        except (TypeError, ValueError):
            return _API_KEY_RESERVATION_HEARTBEAT_SECONDS
    return _API_KEY_RESERVATION_HEARTBEAT_SECONDS


class _ApiKeyUsageServiceProtocol(Protocol):
    _repo_factory: ProxyRepoFactory
    _background_cleanup_tasks: set[asyncio.Task[None]]


def _normalize_service_tier_value(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if stripped.lower() == "fast":
        return "priority"
    return stripped


def _service_tier_from_response(
    response: CompactResponsePayload | None,
) -> str | None:
    if response is None:
        return None
    extra = response.model_extra
    if not isinstance(extra, Mapping):
        return None
    return _normalize_service_tier_value(extra.get("service_tier"))


class _ApiKeyUsageMixin:
    async def _reserve_websocket_api_key_usage(
        self,
        api_key: ApiKeyData | None,
        *,
        request_model: str | None,
        request_service_tier: str | None,
        request_usage_budget: ApiKeyRequestUsageBudget | None = None,
    ) -> ApiKeyUsageReservationData | None:
        if api_key is None:
            return None

        proxy = cast(_ApiKeyUsageServiceProtocol, self)
        with anyio.CancelScope(shield=True):
            async with proxy._repo_factory() as repos:
                service = _service_api_keys_service()(repos.api_keys)
                try:
                    return await service.enforce_limits_for_request(
                        api_key.id,
                        request_model=request_model,
                        request_service_tier=request_service_tier,
                        request_usage_budget=request_usage_budget,
                    )
                except ApiKeyRateLimitExceededError as exc:
                    message = f"{exc}. Usage resets at {exc.reset_at.isoformat()}Z."
                    raise ProxyRateLimitError(message) from exc
                except ApiKeyInvalidError as exc:
                    raise ProxyAuthError(str(exc)) from exc

    async def _release_websocket_reservation(
        self,
        reservation: ApiKeyUsageReservationData | None,
    ) -> None:
        if reservation is None:
            return
        proxy = cast(_ApiKeyUsageServiceProtocol, self)
        with anyio.CancelScope(shield=True):
            async with proxy._repo_factory() as repos:
                service = _service_api_keys_service()(repos.api_keys)
                await service.release_usage_reservation(reservation.reservation_id)

    async def _release_websocket_request_state_reservation(
        self,
        request_state: _WebSocketRequestState,
    ) -> None:
        self._cancel_request_state_api_key_reservation_heartbeat(request_state)
        await self._release_websocket_reservation(request_state.api_key_reservation)

    async def _maybe_touch_api_key_reservation(
        self,
        *,
        api_key: ApiKeyData | None,
        reservation: ApiKeyUsageReservationData | None,
        last_touch_at: float,
        request_id: str,
        surface: str,
    ) -> float:
        if reservation is None:
            return last_touch_at

        now = time.monotonic()
        if now < last_touch_at + _api_key_reservation_heartbeat_seconds():
            return last_touch_at

        proxy = cast(_ApiKeyUsageServiceProtocol, self)
        with anyio.CancelScope(shield=True):
            try:
                async with proxy._repo_factory() as repos:
                    service = _service_api_keys_service()(repos.api_keys)
                    touched = await service.touch_usage_reservation(reservation.reservation_id)
                    if not touched:
                        return last_touch_at
            except Exception:
                logger.warning(
                    "Failed to touch %s API key reservation key_id=%s request_id=%s",
                    surface,
                    api_key.id if api_key is not None else None,
                    request_id,
                    exc_info=True,
                )
                return last_touch_at
        return now

    async def _run_api_key_reservation_heartbeat(
        self,
        *,
        api_key: ApiKeyData | None,
        reservation: ApiKeyUsageReservationData | None,
        touch_state: _ApiKeyReservationTouchState,
        request_id: str,
        surface: str,
        stop_event: asyncio.Event,
    ) -> None:
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_api_key_reservation_heartbeat_seconds())
                return
            except TimeoutError:
                touch_state.last_touch_at = await self._maybe_touch_api_key_reservation(
                    api_key=api_key,
                    reservation=reservation,
                    last_touch_at=touch_state.last_touch_at,
                    request_id=request_id,
                    surface=surface,
                )

    @staticmethod
    def _cancel_api_key_reservation_heartbeat_task(task: asyncio.Task[None]) -> None:
        task.add_done_callback(_consume_api_key_reservation_heartbeat_result)
        task.cancel()

    def _start_request_state_api_key_reservation_heartbeat(
        self,
        request_state: _WebSocketRequestState,
        *,
        api_key: ApiKeyData | None,
        surface: str,
    ) -> None:
        if request_state.api_key_reservation is None:
            return
        if request_state.api_key_reservation_heartbeat_task is not None:
            return
        stop_event = asyncio.Event()
        request_state.api_key_reservation_heartbeat_stop = stop_event
        request_state.api_key_reservation_heartbeat_task = asyncio.create_task(
            self._run_api_key_reservation_heartbeat(
                api_key=api_key,
                reservation=request_state.api_key_reservation,
                touch_state=_ApiKeyReservationTouchState(
                    last_touch_at=request_state.api_key_reservation_last_touch_at,
                ),
                request_id=request_state.response_id or request_state.request_log_id or request_state.request_id,
                surface=surface,
                stop_event=stop_event,
            )
        )

    def _cancel_request_state_api_key_reservation_heartbeat(
        self,
        request_state: _WebSocketRequestState,
    ) -> None:
        task = request_state.api_key_reservation_heartbeat_task
        stop_event = request_state.api_key_reservation_heartbeat_stop
        request_state.api_key_reservation_heartbeat_task = None
        request_state.api_key_reservation_heartbeat_stop = None
        if stop_event is not None:
            stop_event.set()
        if task is not None and not task.done():
            self._cancel_api_key_reservation_heartbeat_task(task)

    async def _maybe_touch_request_state_api_key_reservation(
        self,
        request_state: _WebSocketRequestState,
        *,
        api_key: ApiKeyData | None,
        surface: str,
    ) -> None:
        request_state.api_key_reservation_last_touch_at = await self._maybe_touch_api_key_reservation(
            api_key=api_key,
            reservation=request_state.api_key_reservation,
            last_touch_at=request_state.api_key_reservation_last_touch_at,
            request_id=request_state.response_id or request_state.request_id,
            surface=surface,
        )

    async def _settle_compact_api_key_usage(
        self,
        *,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        response: CompactResponsePayload | None,
        request_service_tier: str | None,
    ) -> None:
        if api_key is None or api_key_reservation is None:
            return

        reservation_id = api_key_reservation.reservation_id
        usage = response.usage if response is not None else None
        input_tokens = usage.input_tokens if usage else None
        output_tokens = usage.output_tokens if usage else None
        cached_input_tokens = usage.input_tokens_details.cached_tokens if usage and usage.input_tokens_details else 0
        model_name = api_key_reservation.model or (getattr(response, "model", None) or "")
        response_service_tier = _service_tier_from_response(response)
        service_tier = (
            response_service_tier
            if isinstance(response_service_tier, str)
            else request_service_tier
            if isinstance(request_service_tier, str)
            else None
        )

        proxy = cast(_ApiKeyUsageServiceProtocol, self)
        with anyio.CancelScope(shield=True):
            try:
                async with proxy._repo_factory() as repos:
                    api_keys_service = ApiKeysService(repos.api_keys)
                    if response is not None and input_tokens is not None and output_tokens is not None:
                        await api_keys_service.finalize_usage_reservation(
                            reservation_id,
                            model=model_name,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            cached_input_tokens=cached_input_tokens or 0,
                            service_tier=service_tier,
                        )
                    else:
                        await api_keys_service.release_usage_reservation(reservation_id)
            except Exception:
                logger.warning(
                    "Failed to settle compact API key reservation key_id=%s request_id=%s",
                    api_key.id,
                    get_request_id(),
                    exc_info=True,
                )

    async def _settle_stream_api_key_usage(
        self,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        settlement: _StreamSettlement,
        request_id: str,
    ) -> bool:
        """Settle stream reservation. Returns True if settled."""
        if api_key is None or api_key_reservation is None:
            return True

        reservation_id = api_key_reservation.reservation_id
        model_name = api_key_reservation.model or settlement.model or ""

        settled: bool = False
        proxy = cast(_ApiKeyUsageServiceProtocol, self)
        with anyio.CancelScope(shield=True):
            try:
                async with proxy._repo_factory() as repos:
                    api_keys_service = ApiKeysService(repos.api_keys)
                    if (
                        settlement.status == "success"
                        and settlement.input_tokens is not None
                        and settlement.output_tokens is not None
                    ):
                        await api_keys_service.finalize_usage_reservation(
                            reservation_id,
                            model=model_name,
                            input_tokens=settlement.input_tokens,
                            output_tokens=settlement.output_tokens,
                            cached_input_tokens=settlement.cached_input_tokens or 0,
                            service_tier=settlement.service_tier,
                        )
                    else:
                        await api_keys_service.release_usage_reservation(reservation_id)
                settled = True
            except Exception:
                logger.warning(
                    "Failed to settle stream API key reservation key_id=%s request_id=%s",
                    api_key.id,
                    request_id,
                    exc_info=True,
                )
                settled = False

        return settled

    def _schedule_cancel_safe_cleanup(
        self,
        coro: Coroutine[Any, Any, None],
        *,
        action: str,
        request_id: str,
    ) -> None:
        task = asyncio.create_task(coro, name=f"proxy-{action}-{request_id}")
        proxy = cast(_ApiKeyUsageServiceProtocol, self)
        proxy._background_cleanup_tasks.add(task)

        def _cleanup_done(done_task: asyncio.Task[None]) -> None:
            proxy._background_cleanup_tasks.discard(done_task)
            try:
                done_task.result()
            except asyncio.CancelledError:
                logger.warning("%s cleanup task cancelled request_id=%s", action, request_id)
            except Exception as exc:
                logger.warning(
                    "%s cleanup task failed request_id=%s",
                    action,
                    request_id,
                    exc_info=(type(exc), exc, exc.__traceback__),
                )

        task.add_done_callback(_cleanup_done)

    async def _release_unsettled_stream_api_key_usage(
        self,
        *,
        api_key: ApiKeyData,
        api_key_reservation: ApiKeyUsageReservationData,
        request_id: str,
    ) -> None:
        proxy = cast(_ApiKeyUsageServiceProtocol, self)
        with anyio.CancelScope(shield=True):
            try:
                async with proxy._repo_factory() as repos:
                    api_keys_service = ApiKeysService(repos.api_keys)
                    await api_keys_service.release_usage_reservation(
                        api_key_reservation.reservation_id,
                    )
            except Exception:
                logger.warning(
                    "Failed to release stream API key reservation key_id=%s request_id=%s",
                    api_key.id,
                    request_id,
                    exc_info=True,
                )

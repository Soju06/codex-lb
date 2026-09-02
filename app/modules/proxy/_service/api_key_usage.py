from __future__ import annotations

import asyncio
import logging
import sys
import time
from collections.abc import Coroutine, Mapping
from typing import Any, Protocol, cast

import anyio
from anyio.lowlevel import checkpoint_if_cancelled

from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.core.exceptions import ProxyAuthError, ProxyRateLimitError
from app.core.openai.models import CompactResponsePayload
from app.core.utils.request_id import get_request_id
from app.core.utils.shared_future import wait_on_shared_future
from app.db.models import Account
from app.modules.api_keys.service import (
    API_KEY_USAGE_RESERVATION_DEFAULT_INPUT_TOKENS,
    API_KEY_USAGE_RESERVATION_DEFAULT_OUTPUT_TOKENS,
    API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET,
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
    _signal_propagated_responses_service_cleanup_ready,
    _StreamSettlement,
    _WebSocketRequestState,
)
from app.modules.proxy.repo_bundle import ProxyRepoFactory

logger = logging.getLogger("app.modules.proxy.service")

_API_KEY_RESERVATION_HEARTBEAT_SECONDS = 300.0
_STREAM_API_KEY_RELEASE_RETRY_BASE_SECONDS = 0.1
_STREAM_API_KEY_RELEASE_RETRY_MAX_SECONDS = 5.0
_STREAM_API_KEY_RELEASE_RETRY_MAX_CONCURRENCY = 4


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
    _stream_api_key_release_retry_semaphore: asyncio.Semaphore
    _load_balancer: Any

    async def _handle_stream_error(
        self, account: Account, error: Any, code: str, http_status: int | None = None
    ) -> Any: ...


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


def _estimated_lease_tokens_from_request_usage_budget(budget: ApiKeyRequestUsageBudget | None) -> float:
    if budget is None:
        return 0.0
    input_tokens = _bounded_lease_token_estimate(
        budget.input_tokens,
        default=API_KEY_USAGE_RESERVATION_DEFAULT_INPUT_TOKENS,
    )
    output_tokens = _bounded_lease_token_estimate(
        budget.output_tokens,
        default=API_KEY_USAGE_RESERVATION_DEFAULT_OUTPUT_TOKENS,
    )
    return float(input_tokens + output_tokens)


def _bounded_lease_token_estimate(value: int | None, *, default: int) -> int:
    if value is None:
        return default
    return max(0, min(value, API_KEY_USAGE_RESERVATION_MAX_TOKEN_BUDGET))


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
        request_state.api_key_reservation = None
        lifecycle = request_state.deferred_account_backoff_lifecycle
        if lifecycle is not None:
            lifecycle.settlement_confirmed = True
        pending_backoffs = (
            lifecycle.pending_backoffs if lifecycle is not None else request_state.deferred_account_error_backoffs
        )
        try:
            if pending_backoffs:
                await self._drain_deferred_account_error_backoffs(pending_backoffs)
        finally:
            # Backoffs and queued stream-health penalties own independent
            # post-settlement lanes: a failed backoff write must not orphan
            # the deferred health write.
            if request_state.deferred_keyed_stream_health:
                await self._drain_deferred_keyed_stream_health(request_state)

    async def _drain_deferred_account_error_backoffs(
        self,
        pending_backoffs: dict[str, Account],
    ) -> None:
        if not pending_backoffs:
            return
        proxy = cast(_ApiKeyUsageServiceProtocol, self)
        while pending_backoffs:
            account_id, account = pending_backoffs.popitem()
            try:
                await proxy._load_balancer.record_error_backoff(account)
            except BaseException:
                pending_backoffs.setdefault(account_id, account)
                raise

    async def _drain_deferred_keyed_stream_health(
        self,
        request_state: _WebSocketRequestState,
    ) -> None:
        """Apply health writes deferred until reservation settlement.

        Each entry is claimed atomically (popped without an intervening
        await), so the idempotent release and terminal-finalize paths can
        drain the same request state concurrently without applying a penalty
        twice or corrupting the queue.

        Each attempt runs as an owned task awaited through
        ``wait_on_shared_future`` (the ``_await_task_deferring_cancellation``
        pattern, inlined because the cancelled-attempt path must re-queue the
        claimed penalty before re-raising). Caller cancellation therefore
        cannot abandon a
        half-applied penalty for a later drain to replay: every claimed entry
        is drained to completion first — no later path owns the queue once
        terminal ownership is relinquished — and the cancellation re-raises
        only after the queue is empty.

        An attempt that raises is logged and dropped rather than retained or
        re-raised: settlement has already committed, and the
        settlement-ordering contract only requires an unconfirmed settlement
        to leave health unapplied — a failed health write must not abort the
        remaining terminal finalization (request-log write, retirement).
        """
        proxy = cast(_ApiKeyUsageServiceProtocol, self)
        deferred_cancellation: asyncio.CancelledError | None = None
        penalties = request_state.deferred_keyed_stream_health
        # The anyio shield keeps a level-cancelled Starlette scope from
        # re-raising into every ``await``, which would otherwise busy-spin
        # this loop until the owned task completes; the inner wait loop
        # defers edge task cancellation the same way.
        with anyio.CancelScope(shield=True):
            while penalties:
                penalty = penalties.pop(0)
                apply_task = asyncio.create_task(
                    proxy._handle_stream_error(penalty.account, penalty.error, penalty.code)
                )
                while True:
                    try:
                        # wait_on_shared_future keeps repeated waits off the
                        # task's done-callback list (3.14 shield leaks one per
                        # cancelled wait).
                        await wait_on_shared_future(apply_task)
                        break
                    except asyncio.CancelledError as exc:
                        if apply_task.cancelled():
                            penalties.insert(0, penalty)
                            raise
                        deferred_cancellation = deferred_cancellation or exc
                    except Exception:
                        logger.warning(
                            "Deferred keyed stream-health write failed after settlement; dropping penalty "
                            "account_id=%s code=%s request_id=%s",
                            penalty.account.id,
                            penalty.code,
                            get_request_id(),
                            exc_info=True,
                        )
                        break
        if deferred_cancellation is None:
            # The shield also blocks the level cancellation this drain
            # promises to re-raise after the queue empties. Probe without
            # suspending so it surfaces here instead of at an arbitrary
            # later checkpoint.
            try:
                await checkpoint_if_cancelled()
            except asyncio.CancelledError as exc:
                deferred_cancellation = exc
        if deferred_cancellation is not None:
            raise deferred_cancellation

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
        reservation_released = False
        with anyio.CancelScope(shield=True):
            try:
                async with proxy._repo_factory() as repos:
                    api_keys_service = _service_api_keys_service()(repos.api_keys)
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
                reservation_released = True
            except Exception as exc:
                logger.warning(
                    "Failed to settle compact API key reservation key_id=%s request_id=%s",
                    api_key.id,
                    get_request_id(),
                    exc_info=True,
                )
                try:
                    async with proxy._repo_factory() as repos:
                        api_keys_service = _service_api_keys_service()(repos.api_keys)
                        await api_keys_service.release_usage_reservation(reservation_id)
                    reservation_released = True
                except Exception:
                    logger.warning(
                        "Failed to release compact API key reservation after settlement failure "
                        "key_id=%s request_id=%s",
                        api_key.id,
                        get_request_id(),
                        exc_info=True,
                    )
                raise ProxyResponseError(
                    502,
                    openai_error(
                        "usage_settlement_failed",
                        "Compact API key usage could not be settled",
                        error_type="server_error",
                    ),
                    failure_phase="usage_settlement",
                    failure_detail="compact_api_key_usage_persistence_failed",
                    failure_exception_type=type(exc).__name__,
                    reservation_released=reservation_released,
                ) from exc
            finally:
                _signal_propagated_responses_service_cleanup_ready()

    async def settle_image_api_key_usage(
        self,
        api_key: ApiKeyData | None,
        reservation: ApiKeyUsageReservationData | None,
        *,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        cached_input_tokens: int | None,
        request_id: str,
    ) -> bool:
        """Transfer captured image usage to tracked reservation settlement."""
        has_usage = input_tokens is not None or output_tokens is not None
        settlement = _StreamSettlement(
            status="success" if has_usage else "failed",
            model=model,
            input_tokens=int(input_tokens or 0) if has_usage else None,
            output_tokens=int(output_tokens or 0) if has_usage else None,
            cached_input_tokens=int(cached_input_tokens or 0) if has_usage else None,
            service_tier=None,
        )
        return await self._settle_stream_api_key_usage(
            api_key,
            reservation,
            settlement,
            request_id=request_id,
        )

    async def _settle_stream_api_key_usage(
        self,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        settlement: _StreamSettlement,
        request_id: str,
        *,
        wait_for_settlement: bool = False,
    ) -> bool:
        """Settle stream reservation. Returns True if settled."""
        if api_key is None or api_key_reservation is None:
            return True

        reservation_id = api_key_reservation.reservation_id
        model_name = api_key_reservation.model or settlement.model or ""
        proxy = cast(_ApiKeyUsageServiceProtocol, self)

        async def _release_ordering_sensitive_fallback() -> bool:
            fallback_task = asyncio.create_task(
                self._release_unsettled_stream_api_key_usage(
                    api_key=api_key,
                    api_key_reservation=api_key_reservation,
                    request_id=request_id,
                ),
                name=f"proxy-stream-api-key-fallback-{request_id}",
            )
            while not fallback_task.done():
                try:
                    await wait_on_shared_future(fallback_task)
                except asyncio.CancelledError:
                    # Keep waiting for the owned fallback. Cancellation is not
                    # a failed release; retry only when the fallback itself is
                    # unconfirmed.
                    continue
            return fallback_task.result()

        async def _settle_once() -> bool:
            try:
                async with proxy._repo_factory() as repos:
                    api_keys_service = _service_api_keys_service()(repos.api_keys)
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
                return True
            except asyncio.CancelledError:
                if wait_for_settlement:
                    return await _release_ordering_sensitive_fallback()
                raise
            except Exception:
                logger.warning(
                    "Failed to settle stream API key reservation key_id=%s request_id=%s",
                    api_key.id,
                    request_id,
                    exc_info=True,
                )
                if wait_for_settlement:
                    return await _release_ordering_sensitive_fallback()
                return False

        # Detach unconditionally instead of shield-awaiting: for ordinary
        # callers the tracking callback schedules a release when settlement
        # fails or is cancelled; an ordering-sensitive settlement task runs
        # that fallback before the tracked task completes once started, while
        # the tracker still owns cancellation before coroutine startup. The
        # caller's finally-net skips via
        # usage_settlement_transferred, and reservations keep counting toward
        # limits until finalized/released, so a briefly-lagging settlement can
        # only over-restrict, never over-admit. Awaiting the ~5+2N-statement
        # settlement transaction here made every keyed stream's close wait on
        # it. Shutdown drains the task set (drain_persistence_tasks).
        task = asyncio.create_task(_settle_once(), name=f"proxy-stream-api-key-settle-{request_id}")
        settlement.usage_settlement_transferred = True
        self._track_stream_usage_settlement_task(
            task,
            api_key=api_key,
            api_key_reservation=api_key_reservation,
            request_id=request_id,
            # Ordering-sensitive settlement performs one immediate fallback,
            # but the tracker must still own retrying release if both attempts
            # fail after ownership has transferred from the request finalizer.
            release_on_failure=True,
        )
        if wait_for_settlement:
            # Ordering-sensitive callers (websocket account-health paths) must
            # commit the settlement before load-balancer health writes; they
            # opt into waiting while everything else stays detached.
            settlement_committed = False
            with anyio.CancelScope(shield=True):
                while True:
                    try:
                        settlement_committed = await wait_on_shared_future(task)
                        break
                    except asyncio.CancelledError:
                        # Caller cancellation must not race fallback release
                        # against the still-running settlement transaction.
                        if task.cancelled():
                            break
                    except Exception:
                        break
            return settlement_committed
        return True

    def _track_stream_usage_settlement_task(
        self,
        task: asyncio.Task[bool],
        *,
        api_key: ApiKeyData,
        api_key_reservation: ApiKeyUsageReservationData,
        request_id: str,
        release_on_failure: bool = True,
    ) -> None:
        proxy = cast(_ApiKeyUsageServiceProtocol, self)
        proxy._background_cleanup_tasks.add(cast(asyncio.Task[None], task))

        async def _release_after_failed_settlement() -> None:
            await self._release_unsettled_stream_api_key_usage(
                api_key=api_key,
                api_key_reservation=api_key_reservation,
                request_id=request_id,
                retry_persistence_failures=True,
            )

        def _settlement_done(done_task: asyncio.Task[bool]) -> None:
            proxy._background_cleanup_tasks.discard(cast(asyncio.Task[None], done_task))
            try:
                settled = done_task.result()
            except asyncio.CancelledError:
                logger.warning(
                    "Stream API key settlement task cancelled key_id=%s request_id=%s",
                    api_key.id,
                    request_id,
                )
                self._schedule_cancel_safe_cleanup(
                    _release_after_failed_settlement(),
                    action="release_stream_api_key_reservation_after_cancelled_settlement",
                    request_id=request_id,
                )
            except Exception as exc:
                logger.warning(
                    "Stream API key settlement task failed key_id=%s request_id=%s",
                    api_key.id,
                    request_id,
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
            else:
                if not settled and release_on_failure:
                    self._schedule_cancel_safe_cleanup(
                        _release_after_failed_settlement(),
                        action="release_stream_api_key_reservation_after_failed_settlement",
                        request_id=request_id,
                    )

        task.add_done_callback(_settlement_done)

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
        retry_persistence_failures: bool = False,
    ) -> bool:
        proxy = cast(_ApiKeyUsageServiceProtocol, self)
        retry_attempt = 1
        retry_delay_seconds = _STREAM_API_KEY_RELEASE_RETRY_BASE_SECONDS
        while True:
            retry_slot_acquired = False
            try:
                if retry_persistence_failures:
                    await proxy._stream_api_key_release_retry_semaphore.acquire()
                    retry_slot_acquired = True
                with anyio.CancelScope(shield=True):
                    async with proxy._repo_factory() as repos:
                        api_keys_service = _service_api_keys_service()(repos.api_keys)
                        await api_keys_service.release_usage_reservation(
                            api_key_reservation.reservation_id,
                        )
                return True
            except Exception:
                if not retry_persistence_failures:
                    logger.warning(
                        "Failed to release stream API key reservation key_id=%s request_id=%s",
                        api_key.id,
                        request_id,
                        exc_info=True,
                    )
                    return False
                logger.warning(
                    "Failed to release stream API key reservation key_id=%s request_id=%s "
                    "retry_attempt=%d retry_delay_seconds=%.2f",
                    api_key.id,
                    request_id,
                    retry_attempt,
                    retry_delay_seconds,
                    exc_info=True,
                )
            finally:
                if retry_slot_acquired:
                    proxy._stream_api_key_release_retry_semaphore.release()
            await asyncio.sleep(retry_delay_seconds)
            retry_attempt += 1
            retry_delay_seconds = min(
                _STREAM_API_KEY_RELEASE_RETRY_MAX_SECONDS,
                retry_delay_seconds * 2,
            )

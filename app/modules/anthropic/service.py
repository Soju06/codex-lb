from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Literal

import anyio

from app.core.auth.anthropic_credentials import credentials_from_account as anthropic_credentials_from_account
from app.core.auth.anthropic_credentials import resolve_anthropic_credentials
from app.core.clients.anthropic_api_proxy import (
    create_message as core_create_message_api,
)
from app.core.clients.anthropic_api_proxy import (
    stream_messages as core_stream_messages_api,
)
from app.core.clients.anthropic_proxy import (
    AnthropicProxyError,
    parse_sse_data_payload,
)
from app.core.clients.anthropic_proxy import (
    create_message as core_create_message,
)
from app.core.clients.anthropic_proxy import (
    stream_messages as core_stream_messages,
)
from app.core.clients.anthropic_usage import AnthropicUsageFetchError, fetch_usage_snapshot
from app.core.config.settings import get_settings
from app.core.types import JsonValue
from app.core.utils.request_id import ensure_request_id, get_request_id
from app.db.models import Account
from app.modules.anthropic.repository import AnthropicRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyData, ApiKeysService, ApiKeyUsageReservationData

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AnthropicRequestUsage:
    input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None


@dataclass(frozen=True, slots=True)
class AnthropicRequestError:
    code: str | None
    message: str | None


class AnthropicService:
    def __init__(self, repository: AnthropicRepository) -> None:
        self._repository = repository

    async def create_message(
        self,
        payload: dict[str, JsonValue],
        headers: Mapping[str, str],
        *,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        transport: Literal["sdk", "api"] = "sdk",
    ) -> dict[str, JsonValue]:
        settings = get_settings()
        request_id = ensure_request_id(headers.get("x-request-id") or headers.get("request-id"))
        account = await self._repository.ensure_provider_account(
            account_id=settings.anthropic_default_account_id,
            email=settings.anthropic_default_account_email,
            plan_type=settings.anthropic_default_plan_type,
        )
        model = _extract_request_model(payload)
        start = time.monotonic()
        status = "success"
        usage = AnthropicRequestUsage(input_tokens=None, output_tokens=None, cached_input_tokens=None)
        error = AnthropicRequestError(code=None, message=None)

        try:
            response_payload = await _create_message_with_transport(
                transport,
                payload,
                headers,
                account=account,
            )
            model = _extract_response_model(response_payload) or model
            usage = _usage_from_message_payload(response_payload)
            return response_payload
        except AnthropicProxyError as exc:
            status = "error"
            error = _extract_error(exc.payload)
            raise
        finally:
            latency_ms = int((time.monotonic() - start) * 1000)
            await self._persist_request_log(
                account_id=account.id,
                api_key_id=api_key.id if api_key else None,
                request_id=request_id,
                model=model,
                usage=usage,
                latency_ms=latency_ms,
                status=status,
                error=error,
            )
            await self._settle_reservation(
                api_key=api_key,
                reservation=api_key_reservation,
                model=model,
                status=status,
                usage=usage,
            )

    def stream_messages(
        self,
        payload: dict[str, JsonValue],
        headers: Mapping[str, str],
        *,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        transport: Literal["sdk", "api"] = "sdk",
    ) -> AsyncIterator[str]:
        return self._stream_messages(
            payload,
            headers,
            api_key=api_key,
            api_key_reservation=api_key_reservation,
            transport=transport,
        )

    async def refresh_usage_windows(self) -> bool:
        settings = get_settings()
        if not settings.anthropic_usage_refresh_enabled:
            return False

        credentials = await resolve_anthropic_credentials()
        if credentials is None:
            return False

        account = await self._repository.ensure_provider_account(
            account_id=settings.anthropic_default_account_id,
            email=settings.anthropic_default_account_email,
            plan_type=settings.anthropic_default_plan_type,
        )

        try:
            snapshot = await fetch_usage_snapshot(
                bearer_token=credentials.bearer_token,
            )
        except AnthropicUsageFetchError as exc:
            logger.warning(
                "anthropic_usage_refresh_failed status=%s message=%s request_id=%s",
                exc.status_code,
                exc.message,
                get_request_id(),
            )
            return False

        wrote = False
        if snapshot.five_hour is not None:
            await self._repository.add_usage_entry(
                account_id=account.id,
                used_percent=snapshot.five_hour.used_percent,
                window="primary",
                reset_at=snapshot.five_hour.reset_at_epoch,
                window_minutes=snapshot.five_hour.window_minutes,
            )
            wrote = True

        if snapshot.seven_day is not None:
            await self._repository.add_usage_entry(
                account_id=account.id,
                used_percent=snapshot.seven_day.used_percent,
                window="secondary",
                reset_at=snapshot.seven_day.reset_at_epoch,
                window_minutes=snapshot.seven_day.window_minutes,
            )
            wrote = True

        return wrote

    async def _stream_messages(
        self,
        payload: dict[str, JsonValue],
        headers: Mapping[str, str],
        *,
        api_key: ApiKeyData | None,
        api_key_reservation: ApiKeyUsageReservationData | None,
        transport: Literal["sdk", "api"],
    ) -> AsyncIterator[str]:
        settings = get_settings()
        request_id = ensure_request_id(headers.get("x-request-id") or headers.get("request-id"))
        account = await self._repository.ensure_provider_account(
            account_id=settings.anthropic_default_account_id,
            email=settings.anthropic_default_account_email,
            plan_type=settings.anthropic_default_plan_type,
        )
        model = _extract_request_model(payload)
        start = time.monotonic()
        accumulator = _StreamAccumulator(model=model)

        try:
            async for line in _stream_messages_with_transport(
                transport,
                payload,
                headers,
                account=account,
            ):
                event_payload = parse_sse_data_payload(line)
                accumulator.observe(event_payload)
                yield line
            accumulator.mark_stream_end()
        except AnthropicProxyError as exc:
            accumulator.observe(exc.payload)
            accumulator.mark_error_from_payload(exc.payload)
            raise
        finally:
            usage = accumulator.to_usage()
            error = accumulator.to_error()
            status = accumulator.status
            latency_ms = int((time.monotonic() - start) * 1000)
            await self._persist_request_log(
                account_id=account.id,
                api_key_id=api_key.id if api_key else None,
                request_id=request_id,
                model=accumulator.model,
                usage=usage,
                latency_ms=latency_ms,
                status=status,
                error=error,
            )
            await self._settle_reservation(
                api_key=api_key,
                reservation=api_key_reservation,
                model=accumulator.model,
                status=status,
                usage=usage,
            )

    async def _persist_request_log(
        self,
        *,
        account_id: str,
        api_key_id: str | None,
        request_id: str,
        model: str,
        usage: AnthropicRequestUsage,
        latency_ms: int,
        status: str,
        error: AnthropicRequestError,
    ) -> None:
        with anyio.CancelScope(shield=True):
            try:
                await self._repository.add_request_log(
                    account_id=account_id,
                    api_key_id=api_key_id,
                    request_id=request_id,
                    model=model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cached_input_tokens=usage.cached_input_tokens,
                    latency_ms=latency_ms,
                    status=status,
                    error_code=error.code,
                    error_message=error.message,
                )
            except Exception:
                logger.warning(
                    "anthropic_request_log_persist_failed request_id=%s account_id=%s",
                    request_id,
                    account_id,
                    exc_info=True,
                )

    async def _settle_reservation(
        self,
        *,
        api_key: ApiKeyData | None,
        reservation: ApiKeyUsageReservationData | None,
        model: str,
        status: str,
        usage: AnthropicRequestUsage,
    ) -> None:
        if api_key is None or reservation is None:
            return

        with anyio.CancelScope(shield=True):
            try:
                from app.db.session import get_background_session

                async with get_background_session() as session:
                    api_keys_service = ApiKeysService(ApiKeysRepository(session))
                    if status == "success" and usage.input_tokens is not None and usage.output_tokens is not None:
                        await api_keys_service.finalize_usage_reservation(
                            reservation.reservation_id,
                            model=model,
                            input_tokens=usage.input_tokens,
                            output_tokens=usage.output_tokens,
                            cached_input_tokens=usage.cached_input_tokens or 0,
                        )
                    else:
                        await api_keys_service.release_usage_reservation(reservation.reservation_id)
            except Exception:
                logger.warning(
                    "anthropic_reservation_settlement_failed request_id=%s key_id=%s",
                    get_request_id(),
                    api_key.id,
                    exc_info=True,
                )


@dataclass(slots=True)
class _StreamAccumulator:
    model: str
    status: str = "success"
    error_code: str | None = None
    error_message: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    saw_terminal: bool = False

    def observe(self, payload: dict[str, JsonValue] | None) -> None:
        if payload is None:
            return
        payload_type = payload.get("type")
        if not isinstance(payload_type, str):
            return

        if payload_type == "message_start":
            message = payload.get("message")
            if isinstance(message, dict):
                model_value = message.get("model")
                if isinstance(model_value, str) and model_value.strip():
                    self.model = model_value.strip()
                usage = message.get("usage")
                self._apply_usage(usage if isinstance(usage, dict) else None)
            return

        if payload_type == "message_delta":
            usage = payload.get("usage")
            if isinstance(usage, dict):
                self._apply_usage(usage)
            return

        if payload_type == "message_stop":
            self.saw_terminal = True
            return

        if payload_type == "error":
            self.mark_error_from_payload(payload)

    def mark_error_from_payload(self, payload: dict[str, JsonValue]) -> None:
        self.status = "error"
        error = _extract_error(payload)
        self.error_code = error.code
        self.error_message = error.message

    def mark_stream_end(self) -> None:
        if self.status == "success" and not self.saw_terminal:
            self.status = "error"
            self.error_code = "stream_incomplete"
            self.error_message = "Upstream closed stream without message_stop"

    def to_usage(self) -> AnthropicRequestUsage:
        return AnthropicRequestUsage(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cached_input_tokens=self.cached_input_tokens,
        )

    def to_error(self) -> AnthropicRequestError:
        return AnthropicRequestError(code=self.error_code, message=self.error_message)

    def _apply_usage(self, usage: dict[str, JsonValue] | None) -> None:
        if usage is None:
            return
        input_tokens = _as_int(usage.get("input_tokens"))
        output_tokens = _as_int(usage.get("output_tokens"))
        cached_input_tokens = _as_int(usage.get("cache_read_input_tokens"))
        normalized_input_tokens = _normalize_input_tokens_for_log(input_tokens, cached_input_tokens)

        if normalized_input_tokens is not None:
            self.input_tokens = normalized_input_tokens
        if output_tokens is not None:
            self.output_tokens = output_tokens
        if cached_input_tokens is not None:
            self.cached_input_tokens = cached_input_tokens


def _extract_request_model(payload: dict[str, JsonValue]) -> str:
    value = payload.get("model")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "anthropic-unknown"


def _extract_response_model(payload: dict[str, JsonValue]) -> str | None:
    value = payload.get("model")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _usage_from_message_payload(payload: dict[str, JsonValue]) -> AnthropicRequestUsage:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return AnthropicRequestUsage(input_tokens=None, output_tokens=None, cached_input_tokens=None)
    input_tokens = _as_int(usage.get("input_tokens"))
    cached_input_tokens = _as_int(usage.get("cache_read_input_tokens"))
    return AnthropicRequestUsage(
        input_tokens=_normalize_input_tokens_for_log(input_tokens, cached_input_tokens),
        output_tokens=_as_int(usage.get("output_tokens")),
        cached_input_tokens=cached_input_tokens,
    )


def _extract_error(payload: dict[str, JsonValue]) -> AnthropicRequestError:
    error_value = payload.get("error")
    if isinstance(error_value, dict):
        raw_code = error_value.get("code")
        raw_type = error_value.get("type")
        raw_message = error_value.get("message")
        return AnthropicRequestError(
            code=_normalize_error_code(raw_code, raw_type),
            message=raw_message if isinstance(raw_message, str) else None,
        )

    raw_type = payload.get("type")
    return AnthropicRequestError(
        code=_normalize_error_code(None, raw_type),
        message=None,
    )


def _normalize_error_code(raw_code: JsonValue, raw_type: JsonValue) -> str | None:
    if isinstance(raw_code, str) and raw_code.strip():
        return raw_code.strip().lower()
    if isinstance(raw_type, str) and raw_type.strip():
        normalized_type = raw_type.strip().lower()
        if normalized_type in {"rate_limit_error", "overloaded_error"}:
            return "rate_limit_exceeded"
        if normalized_type in {"insufficient_quota", "quota_exceeded", "usage_not_included"}:
            return normalized_type
        if normalized_type == "authentication_error":
            return "invalid_api_key"
        return normalized_type
    return None


def _as_int(value: JsonValue) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _normalize_input_tokens_for_log(input_tokens: int | None, cached_input_tokens: int | None) -> int | None:
    if input_tokens is None:
        return None
    if cached_input_tokens is None:
        return input_tokens
    if cached_input_tokens > input_tokens:
        return input_tokens + cached_input_tokens
    return input_tokens


async def _create_message_with_transport(
    transport: Literal["sdk", "api"],
    payload: dict[str, JsonValue],
    headers: Mapping[str, str],
    *,
    account: Account,
) -> dict[str, JsonValue]:
    if transport == "api":
        credentials = anthropic_credentials_from_account(account)
        return await core_create_message_api(payload, headers, credentials=credentials)
    return await core_create_message(payload, headers)


async def _stream_messages_with_transport(
    transport: Literal["sdk", "api"],
    payload: dict[str, JsonValue],
    headers: Mapping[str, str],
    *,
    account: Account,
) -> AsyncIterator[str]:
    if transport == "api":
        credentials = anthropic_credentials_from_account(account)
        async for line in core_stream_messages_api(payload, headers, credentials=credentials):
            yield line
        return
    async for line in core_stream_messages(payload, headers):
        yield line

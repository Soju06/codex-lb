from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime

import aiohttp
from aiohttp_retry import ExponentialRetry, RetryClient
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.clients.codex import (
    CodexClient,
    CodexTransportError,
    create_codex_session,
    require_route_or_direct_egress_opt_in,
)
from app.core.clients.http import lease_retry_client
from app.core.config.settings import get_settings
from app.core.types import JsonObject
from app.core.upstream_proxy import ResolvedUpstreamRoute
from app.core.utils.request_id import get_request_id

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
RETRY_START_TIMEOUT = 0.5
RETRY_MAX_TIMEOUT = 2.0

logger = logging.getLogger(__name__)


class RateLimitResetCreditPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    status: str
    granted_at: datetime
    expires_at: datetime
    redeemed_at: datetime | None = None


class RateLimitResetCreditsPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    credits: list[RateLimitResetCreditPayload]
    available_count: int | None = None


class _ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str | None = None
    message: str | None = None
    error_description: str | None = None


class _ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    error: _ErrorDetail | str | None = None
    error_description: str | None = None
    message: str | None = None


class RateLimitResetCreditsFetchError(Exception):
    def __init__(self, status_code: int, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code


async def fetch_rate_limit_reset_credits(
    *,
    access_token: str,
    account_id: str | None,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    client: RetryClient | None = None,
    route: ResolvedUpstreamRoute | None = None,
    codex_client: CodexClient | None = None,
    allow_direct_egress: bool = False,
) -> RateLimitResetCreditsPayload:
    settings = get_settings()
    usage_base = base_url or settings.upstream_base_url
    url = _credits_url(usage_base)
    timeout = aiohttp.ClientTimeout(total=timeout_seconds or settings.usage_fetch_timeout_seconds)
    retries = max_retries if max_retries is not None else settings.usage_fetch_max_retries
    headers = _credits_headers(access_token, account_id)
    retry_options = _retry_options(retries + 1)
    require_route_or_direct_egress_opt_in(
        route=route,
        allow_direct_egress=allow_direct_egress,
        operation="rate limit reset credits fetch",
    )

    try:
        if route is not None:
            return await _fetch_via_codex(
                url=url,
                route=route,
                headers=headers,
                timeout_seconds=timeout_seconds or settings.usage_fetch_timeout_seconds,
                retries=retries,
                codex_client=codex_client,
            )
        async with lease_retry_client(client) as retry_client:
            async with retry_client.request(
                "GET",
                url,
                headers=headers,
                timeout=timeout,
                retry_options=retry_options,
            ) as resp:
                data = await _safe_json(resp)
                if resp.status >= 400:
                    raise _fetch_error(data, resp.status)
                return _payload_or_raise(data)
    except (aiohttp.ClientError, asyncio.TimeoutError, CodexTransportError) as exc:
        logger.warning(
            "Rate limit reset credits fetch error request_id=%s error=%s",
            get_request_id(),
            exc,
        )
        raise RateLimitResetCreditsFetchError(0, f"Rate limit reset credits fetch failed: {exc}") from exc


async def _fetch_via_codex(
    *,
    url: str,
    route: ResolvedUpstreamRoute,
    headers: dict[str, str],
    timeout_seconds: float,
    retries: int,
    codex_client: CodexClient | None,
) -> RateLimitResetCreditsPayload:
    attempts = max(1, retries + 1)
    owns_codex_client = codex_client is None
    active_codex_client = codex_client or CodexClient(create_codex_session())
    try:
        for attempt in range(attempts):
            try:
                resp = await active_codex_client.request(
                    "GET",
                    url,
                    route=route,
                    headers=headers,
                    timeout=timeout_seconds,
                )
            except CodexTransportError:
                if attempt < attempts - 1:
                    await asyncio.sleep(_retry_delay_seconds(attempt))
                    continue
                raise

            data = await _safe_codex_json(resp)
            status = _codex_response_status(resp)
            if status in RETRYABLE_STATUS and attempt < attempts - 1:
                await asyncio.sleep(_retry_delay_seconds(attempt))
                continue
            if status >= 400:
                raise _fetch_error(data, status)
            return _payload_or_raise(data)
    finally:
        if owns_codex_client:
            close = getattr(active_codex_client, "close", None)
            if callable(close):
                await close()
    raise RuntimeError("unreachable rate limit reset credits retry state")


def _payload_or_raise(data: JsonObject) -> RateLimitResetCreditsPayload:
    try:
        return RateLimitResetCreditsPayload.model_validate(data)
    except ValidationError as exc:
        logger.warning(
            "Rate limit reset credits fetch invalid payload request_id=%s",
            get_request_id(),
        )
        raise RateLimitResetCreditsFetchError(502, "Invalid rate limit reset credits payload") from exc


def _fetch_error(payload: JsonObject, status: int) -> RateLimitResetCreditsFetchError:
    code = _extract_error_code(payload)
    message = _extract_error_message(payload) or f"Rate limit reset credits fetch failed ({status})"
    logger.warning(
        "Rate limit reset credits fetch failed request_id=%s status=%s code=%s message=%s",
        get_request_id(),
        status,
        code,
        message,
    )
    return RateLimitResetCreditsFetchError(status, message, code=code)


def _credits_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if "/backend-api" not in normalized:
        normalized = f"{normalized}/backend-api"
    return f"{normalized}/wham/rate-limit-reset-credits"


def _credits_headers(access_token: str, account_id: str | None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    request_id = get_request_id()
    if request_id:
        headers["x-request-id"] = request_id
    if account_id and not account_id.startswith(("email_", "local_")):
        headers["chatgpt-account-id"] = account_id
    return headers


async def _safe_json(resp: aiohttp.ClientResponse) -> JsonObject:
    try:
        data = await resp.json(content_type=None)
    except Exception:
        text = await resp.text()
        return {"error": {"message": text.strip()}}
    return data if isinstance(data, dict) else {"error": {"message": str(data)}}


async def _safe_codex_json(response: object) -> JsonObject:
    try:
        json_method = getattr(response, "json", None)
        if callable(json_method):
            data = json_method()
            if asyncio.iscoroutine(data):
                data = await data
            return data if isinstance(data, dict) else {"error": {"message": str(data)}}
    except Exception:
        pass
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return {"error": {"message": content.decode("utf-8", errors="replace").strip()}}
    if isinstance(content, str):
        return {"error": {"message": content.strip()}}
    return {"error": {"message": ""}}


def _codex_response_status(response: object) -> int:
    value = getattr(response, "status_code", getattr(response, "status", None))
    if value is None:
        return 0
    return int(value)


def _extract_error_message(payload: JsonObject) -> str | None:
    envelope = _ErrorEnvelope.model_validate(payload)
    error = envelope.error
    if isinstance(error, _ErrorDetail):
        return error.message or error.error_description
    if isinstance(error, str):
        return envelope.error_description or error
    return envelope.message


def _extract_error_code(payload: JsonObject) -> str | None:
    envelope = _ErrorEnvelope.model_validate(payload)
    error = envelope.error
    if isinstance(error, _ErrorDetail) and isinstance(error.code, str):
        normalized = error.code.strip().lower()
        return normalized or None
    return None


def _retry_options(attempts: int) -> ExponentialRetry:
    return ExponentialRetry(
        attempts=attempts,
        start_timeout=RETRY_START_TIMEOUT,
        max_timeout=RETRY_MAX_TIMEOUT,
        factor=2.0,
        statuses=RETRYABLE_STATUS,
        exceptions={aiohttp.ClientError, asyncio.TimeoutError},
        retry_all_server_errors=False,
    )


def _retry_delay_seconds(attempt: int) -> float:
    return min(RETRY_MAX_TIMEOUT, RETRY_START_TIMEOUT * (2.0**attempt))


class ConsumedCreditPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    status: str | None = None
    redeemed_at: datetime | None = None


class ConsumeCreditResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str | None = None
    credit: ConsumedCreditPayload | None = None
    windows_reset: int | None = None


def _consume_url(base_url: str) -> str:
    return f"{_credits_url(base_url)}/consume"


async def consume_rate_limit_reset_credit(
    *,
    access_token: str,
    account_id: str | None,
    credit_id: str,
    redeem_request_id: str | None = None,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    client: RetryClient | None = None,
    route: ResolvedUpstreamRoute | None = None,
    codex_client: CodexClient | None = None,
    allow_direct_egress: bool = False,
) -> ConsumeCreditResponse:
    settings = get_settings()
    usage_base = base_url or settings.upstream_base_url
    url = _consume_url(usage_base)
    timeout = aiohttp.ClientTimeout(total=timeout_seconds or settings.usage_fetch_timeout_seconds)
    retries = max_retries if max_retries is not None else settings.usage_fetch_max_retries
    headers = _credits_headers(access_token, account_id)
    retry_options = _retry_options(retries + 1)
    body = {
        "credit_id": credit_id,
        "redeem_request_id": redeem_request_id or str(uuid.uuid4()),
    }
    require_route_or_direct_egress_opt_in(
        route=route,
        allow_direct_egress=allow_direct_egress,
        operation="rate limit reset credit consume",
    )

    try:
        if route is not None:
            return await _consume_via_codex(
                url=url,
                route=route,
                headers=headers,
                body=body,
                timeout_seconds=timeout_seconds or settings.usage_fetch_timeout_seconds,
                retries=retries,
                codex_client=codex_client,
            )
        async with lease_retry_client(client) as retry_client:
            async with retry_client.request(
                "POST",
                url,
                headers=headers,
                json=body,
                timeout=timeout,
                retry_options=retry_options,
            ) as resp:
                data = await _safe_json(resp)
                if resp.status >= 400:
                    raise _fetch_error(data, resp.status)
                return _consume_payload_or_raise(data)
    except (aiohttp.ClientError, asyncio.TimeoutError, CodexTransportError) as exc:
        logger.warning(
            "Rate limit reset credit consume error request_id=%s error=%s",
            get_request_id(),
            exc,
        )
        raise RateLimitResetCreditsFetchError(0, f"Consume request failed: {exc}") from exc


async def _consume_via_codex(
    *,
    url: str,
    route: ResolvedUpstreamRoute,
    headers: dict[str, str],
    body: JsonObject,
    timeout_seconds: float,
    retries: int,
    codex_client: CodexClient | None,
) -> ConsumeCreditResponse:
    attempts = max(1, retries + 1)
    owns_codex_client = codex_client is None
    active_codex_client = codex_client or CodexClient(create_codex_session())
    try:
        for attempt in range(attempts):
            try:
                resp = await active_codex_client.request(
                    "POST",
                    url,
                    route=route,
                    headers=headers,
                    json=body,
                    timeout=timeout_seconds,
                )
            except CodexTransportError:
                if attempt < attempts - 1:
                    await asyncio.sleep(_retry_delay_seconds(attempt))
                    continue
                raise

            data = await _safe_codex_json(resp)
            status = _codex_response_status(resp)
            if status in RETRYABLE_STATUS and attempt < attempts - 1:
                await asyncio.sleep(_retry_delay_seconds(attempt))
                continue
            if status >= 400:
                raise _fetch_error(data, status)
            return _consume_payload_or_raise(data)
    finally:
        if owns_codex_client:
            close = getattr(active_codex_client, "close", None)
            if callable(close):
                await close()
    raise RuntimeError("unreachable rate limit reset credit consume retry state")


def _consume_payload_or_raise(data: JsonObject) -> ConsumeCreditResponse:
    try:
        return ConsumeCreditResponse.model_validate(data)
    except ValidationError as exc:
        raise RateLimitResetCreditsFetchError(502, "Invalid consume response payload") from exc

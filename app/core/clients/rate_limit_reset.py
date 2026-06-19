from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from enum import StrEnum

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
from app.core.clients.usage import (
    RETRYABLE_STATUS,
    _extract_error_code,
    _extract_error_message,
    _retry_delay_seconds,
    _retry_options,
    _safe_codex_json,
    _safe_json,
    _usage_headers,
)
from app.core.config.settings import get_settings
from app.core.types import JsonObject
from app.core.upstream_proxy import ResolvedUpstreamRoute
from app.core.utils.request_id import get_request_id

logger = logging.getLogger(__name__)


class ConsumeRateLimitResetCode(StrEnum):
    RESET = "reset"
    NO_CREDIT = "no_credit"
    NOTHING_TO_RESET = "nothing_to_reset"
    ALREADY_REDEEMED = "already_redeemed"


class ConsumeRateLimitResetResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: ConsumeRateLimitResetCode
    windows_reset: int | None = None


class RateLimitResetConsumeError(Exception):
    def __init__(self, status_code: int, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code


class RateLimitResetCredit(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    status: str | None = None
    granted_at: datetime | None = None


class RateLimitResetCreditsPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    credits: list[RateLimitResetCredit] = []
    available_count: int | None = None


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
    headers = _usage_headers(access_token, account_id)
    retry_options = _retry_options(retries + 1)
    require_route_or_direct_egress_opt_in(
        route=route,
        allow_direct_egress=allow_direct_egress,
        operation="rate limit reset credits fetch",
    )

    try:
        if route is not None:
            data = await _fetch_credits_via_codex(
                url=url,
                route=route,
                headers=headers,
                timeout_seconds=timeout_seconds or settings.usage_fetch_timeout_seconds,
                retries=retries,
                codex_client=codex_client,
            )
        else:
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
                        code = _extract_error_code(data)
                        message = (
                            _extract_error_message(data)
                            or f"Rate limit reset credits fetch failed ({resp.status})"
                        )
                        logger.warning(
                            "Rate limit reset credits fetch failed request_id=%s status=%s code=%s message=%s",
                            get_request_id(),
                            resp.status,
                            code,
                            message,
                        )
                        raise RateLimitResetConsumeError(resp.status, message, code=code)
        return _credits_payload_or_raise(data)
    except (aiohttp.ClientError, asyncio.TimeoutError, CodexTransportError) as exc:
        logger.warning(
            "Rate limit reset credits fetch error request_id=%s error=%s",
            get_request_id(),
            exc,
        )
        raise RateLimitResetConsumeError(0, f"Rate limit reset credits fetch failed: {exc}") from exc


def pick_available_reset_credit_id(payload: RateLimitResetCreditsPayload) -> str | None:
    available = [credit for credit in payload.credits if credit.status == "available"]
    if not available:
        return None
    available.sort(key=lambda credit: (credit.granted_at is None, credit.granted_at or datetime.max))
    return available[0].id


async def consume_rate_limit_reset(
    *,
    access_token: str,
    account_id: str | None,
    credit_id: str,
    redeem_request_id: str,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    client: RetryClient | None = None,
    route: ResolvedUpstreamRoute | None = None,
    codex_client: CodexClient | None = None,
    allow_direct_egress: bool = False,
) -> ConsumeRateLimitResetResponse:
    settings = get_settings()
    usage_base = base_url or settings.upstream_base_url
    url = _consume_url(usage_base)
    timeout = aiohttp.ClientTimeout(total=timeout_seconds or settings.usage_fetch_timeout_seconds)
    retries = max_retries if max_retries is not None else settings.usage_fetch_max_retries
    headers = _usage_headers(access_token, account_id)
    retry_options = _retry_options(retries + 1)
    body = {"credit_id": credit_id, "redeem_request_id": redeem_request_id}
    require_route_or_direct_egress_opt_in(
        route=route,
        allow_direct_egress=allow_direct_egress,
        operation="rate limit reset consume",
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
                    code = _extract_error_code(data)
                    message = _extract_error_message(data) or f"Rate limit reset consume failed ({resp.status})"
                    logger.warning(
                        "Rate limit reset consume failed request_id=%s status=%s code=%s message=%s",
                        get_request_id(),
                        resp.status,
                        code,
                        message,
                    )
                    raise RateLimitResetConsumeError(resp.status, message, code=code)
                return _consume_response_or_raise(data, resp.status)
    except (aiohttp.ClientError, asyncio.TimeoutError, CodexTransportError) as exc:
        logger.warning(
            "Rate limit reset consume error request_id=%s error=%s",
            get_request_id(),
            exc,
        )
        raise RateLimitResetConsumeError(0, f"Rate limit reset consume failed: {exc}") from exc


async def _consume_via_codex(
    *,
    url: str,
    route: ResolvedUpstreamRoute,
    headers: dict[str, str],
    body: dict[str, str],
    timeout_seconds: float,
    retries: int,
    codex_client: CodexClient | None,
) -> ConsumeRateLimitResetResponse:
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
            return _consume_response_or_raise(data, status)
    finally:
        if owns_codex_client:
            close = getattr(active_codex_client, "close", None)
            if callable(close):
                await close()
    raise RuntimeError("unreachable rate limit reset consume retry state")


async def _fetch_credits_via_codex(
    *,
    url: str,
    route: ResolvedUpstreamRoute,
    headers: dict[str, str],
    timeout_seconds: float,
    retries: int,
    codex_client: CodexClient | None,
) -> JsonObject:
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
                code = _extract_error_code(data)
                message = (
                    _extract_error_message(data) or f"Rate limit reset credits fetch failed ({status})"
                )
                raise RateLimitResetConsumeError(status, message, code=code)
            return data if isinstance(data, dict) else {"error": {"message": str(data)}}
    finally:
        if owns_codex_client:
            close = getattr(active_codex_client, "close", None)
            if callable(close):
                await close()
    raise RuntimeError("unreachable rate limit reset credits fetch retry state")


def _credits_payload_or_raise(data: JsonObject) -> RateLimitResetCreditsPayload:
    try:
        return RateLimitResetCreditsPayload.model_validate(data)
    except ValidationError as exc:
        logger.warning(
            "Rate limit reset credits invalid payload request_id=%s",
            get_request_id(),
        )
        raise RateLimitResetConsumeError(502, "Invalid rate limit reset credits payload") from exc


def _consume_response_or_raise(data: JsonObject, status: int) -> ConsumeRateLimitResetResponse:
    if status >= 400:
        code = _extract_error_code(data)
        message = _extract_error_message(data) or f"Rate limit reset consume failed ({status})"
        logger.warning(
            "Rate limit reset consume failed request_id=%s status=%s code=%s message=%s",
            get_request_id(),
            status,
            code,
            message,
        )
        raise RateLimitResetConsumeError(status, message, code=code)
    try:
        return ConsumeRateLimitResetResponse.model_validate(data)
    except ValidationError as exc:
        logger.warning(
            "Rate limit reset consume invalid payload request_id=%s",
            get_request_id(),
        )
        raise RateLimitResetConsumeError(502, "Invalid rate limit reset consume payload") from exc


def _credits_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if "/backend-api" not in normalized:
        normalized = f"{normalized}/backend-api"
    return f"{normalized}/wham/rate-limit-reset-credits"


def _consume_url(base_url: str) -> str:
    return f"{_credits_url(base_url)}/consume"


def _codex_response_status(response: object) -> int:
    value = getattr(response, "status_code", getattr(response, "status", None))
    if value is None:
        return 0
    return int(value)
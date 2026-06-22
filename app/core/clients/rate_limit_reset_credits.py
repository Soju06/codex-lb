from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime

import aiohttp
from aiohttp_retry import ExponentialRetry, RetryClient
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.clients.codex import (
    CodexClient,
    CodexTransportError,
    create_codex_session,
    require_route_or_direct_egress_opt_in,
)
from app.core.clients.headers import build_chatgpt_auth_headers
from app.core.clients.http import lease_retry_client
from app.core.clients.usage import (
    _retry_delay_seconds,
    _safe_codex_json,
)
from app.core.config.settings import get_settings
from app.core.types import JsonObject
from app.core.upstream_proxy import ResolvedUpstreamRoute
from app.core.utils.request_id import get_request_id

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
RETRY_START_TIMEOUT = 0.5
RETRY_MAX_TIMEOUT = 2.0

logger = logging.getLogger(__name__)


class ResetCreditFetchError(Exception):
    def __init__(self, status_code: int, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code


class ConsumeResetCreditError(Exception):
    def __init__(self, status_code: int, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code


class ResetCreditItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    reset_type: str | None = None
    status: str | None = None
    granted_at: datetime | None = None
    expires_at: datetime | None = None
    title: str | None = None
    description: str | None = None
    redeem_started_at: datetime | None = None
    redeemed_at: datetime | None = None


class ResetCreditsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    credits: list[ResetCreditItem]
    available_count: int


class ConsumeResetCreditCredit(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    reset_type: str | None = None
    status: str | None = None
    redeemed_at: datetime | None = None


class ConsumeResetCreditResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    code: str
    credit: ConsumeResetCreditCredit
    windows_reset: int


class RateLimitResetCreditsSnapshot(BaseModel):
    """In-memory snapshot of a single account's banked reset credits.

    Carries the upstream ``available_count``, the soonest expiry among the
    available credits, and the full credit list so dashboard endpoints can
    render details and the consume path can re-select at click time.
    """

    model_config = ConfigDict(extra="ignore")

    available_count: int = 0
    nearest_expires_at: datetime | None = None
    credits: list[ResetCreditItem] = Field(default_factory=list)


async def fetch_reset_credits(
    access_token: str,
    account_id: str | None,
    *,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    client: RetryClient | None = None,
    route: ResolvedUpstreamRoute | None = None,
    codex_client: CodexClient | None = None,
    allow_direct_egress: bool = False,
) -> ResetCreditsResponse:
    settings = get_settings()
    usage_base = base_url or settings.upstream_base_url
    url = _reset_credits_url(usage_base)
    timeout = aiohttp.ClientTimeout(total=timeout_seconds or settings.usage_fetch_timeout_seconds)
    retries = max_retries if max_retries is not None else settings.usage_fetch_max_retries
    headers = build_chatgpt_auth_headers(access_token, account_id)
    retry_options = _retry_options(retries + 1)
    require_route_or_direct_egress_opt_in(
        route=route,
        allow_direct_egress=allow_direct_egress,
        operation="reset credits fetch",
    )

    try:
        if route is not None:
            data = await _fetch_reset_credits_via_codex(
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
                        message = _extract_error_message(data) or f"Reset credits fetch failed ({resp.status})"
                        logger.warning(
                            "Reset credits fetch failed request_id=%s status=%s code=%s message=%s",
                            get_request_id(),
                            resp.status,
                            code,
                            message,
                        )
                        raise ResetCreditFetchError(resp.status, message, code=code)
        try:
            return ResetCreditsResponse.model_validate(_success_payload(data))
        except (ValueError, ValidationError) as exc:
            logger.warning("Reset credits fetch invalid payload request_id=%s", get_request_id())
            raise ResetCreditFetchError(502, "Invalid reset credits payload") from exc
    except (aiohttp.ClientError, asyncio.TimeoutError, CodexTransportError) as exc:
        logger.warning("Reset credits fetch error request_id=%s error=%s", get_request_id(), exc)
        raise ResetCreditFetchError(0, f"Reset credits fetch failed: {exc}") from exc


async def consume_reset_credit(
    access_token: str,
    account_id: str | None,
    credit_id: str,
    *,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    client: RetryClient | None = None,
    route: ResolvedUpstreamRoute | None = None,
    codex_client: CodexClient | None = None,
    allow_direct_egress: bool = False,
) -> ConsumeResetCreditResponse:
    settings = get_settings()
    usage_base = base_url or settings.upstream_base_url
    url = _consume_url(usage_base)
    timeout = aiohttp.ClientTimeout(total=timeout_seconds or settings.usage_fetch_timeout_seconds)
    # Consume is non-idempotent, so omitted max_retries must not inherit the
    # fetch retry budget and risk replaying a successful upstream redemption.
    retries = max_retries if max_retries is not None else 0
    headers = build_chatgpt_auth_headers(
        access_token,
        account_id,
        extra={"Content-Type": "application/json"},
    )
    redeem_request_id = str(uuid.uuid4())
    body = {"credit_id": credit_id, "redeem_request_id": redeem_request_id}
    retry_options = _retry_options(retries + 1)
    require_route_or_direct_egress_opt_in(
        route=route,
        allow_direct_egress=allow_direct_egress,
        operation="reset credits consume",
    )

    try:
        if route is not None:
            return await _consume_reset_credit_via_codex(
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
                    message = _extract_error_message(data) or f"Reset credits consume failed ({resp.status})"
                    logger.warning(
                        "Reset credits consume failed request_id=%s status=%s code=%s message=%s",
                        get_request_id(),
                        resp.status,
                        code,
                        message,
                    )
                    raise ConsumeResetCreditError(resp.status, message, code=code)
                try:
                    return ConsumeResetCreditResponse.model_validate(_success_payload(data))
                except (ValueError, ValidationError) as exc:
                    logger.warning("Reset credits consume invalid payload request_id=%s", get_request_id())
                    raise ConsumeResetCreditError(502, "Invalid reset credits consume payload") from exc
    except (aiohttp.ClientError, asyncio.TimeoutError, CodexTransportError) as exc:
        logger.warning("Reset credits consume error request_id=%s error=%s", get_request_id(), exc)
        raise ConsumeResetCreditError(0, f"Reset credits consume failed: {exc}") from exc


def build_snapshot(response: ResetCreditsResponse) -> RateLimitResetCreditsSnapshot:
    """Project an upstream list response into the cached snapshot shape."""
    nearest = _nearest_available_expires_at(response.credits)
    return RateLimitResetCreditsSnapshot(
        available_count=response.available_count,
        nearest_expires_at=nearest,
        credits=list(response.credits),
    )


def _nearest_available_expires_at(credits: list[ResetCreditItem]) -> datetime | None:
    candidates = [
        credit.expires_at for credit in credits if credit.status == "available" and credit.expires_at is not None
    ]
    return min(candidates) if candidates else None


def _reset_credits_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if "/backend-api" not in normalized:
        normalized = f"{normalized}/backend-api"
    return f"{normalized}/wham/rate-limit-reset-credits"


def _consume_url(base_url: str) -> str:
    return f"{_reset_credits_url(base_url)}/consume"


async def _safe_json(resp: aiohttp.ClientResponse) -> JsonObject:
    try:
        data = await resp.json(content_type=None)
    except Exception:
        text = await resp.text()
        return {"error": {"message": text.strip()}}
    return data if isinstance(data, dict) else {"error": {"message": str(data)}}


def _success_payload(payload: JsonObject) -> JsonObject:
    if "error" in payload:
        raise ValueError("success response carried error payload")
    return payload


class _ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    error: dict[str, str | None] | str | None = None
    error_description: str | None = None
    message: str | None = None


def _extract_error_message(payload: JsonObject) -> str | None:
    envelope = _ErrorEnvelope.model_validate(payload)
    error = envelope.error
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message:
            return message
        description = error.get("error_description")
        if isinstance(description, str) and description:
            return description
    if isinstance(error, str) and error:
        return envelope.error_description or error
    return envelope.message


def _extract_error_code(payload: JsonObject) -> str | None:
    envelope = _ErrorEnvelope.model_validate(payload)
    error = envelope.error
    if isinstance(error, dict):
        code = error.get("code")
        if isinstance(code, str):
            normalized = code.strip().lower()
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


async def _fetch_reset_credits_via_codex(
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
                message = _extract_error_message(data) or f"Reset credits fetch failed ({status})"
                raise ResetCreditFetchError(status, message, code=code)
            return data if isinstance(data, dict) else {"error": {"message": str(data)}}
    finally:
        if owns_codex_client:
            close = getattr(active_codex_client, "close", None)
            if callable(close):
                await close()
    raise RuntimeError("unreachable reset credits fetch retry state")


async def _consume_reset_credit_via_codex(
    *,
    url: str,
    route: ResolvedUpstreamRoute,
    headers: dict[str, str],
    body: dict[str, str],
    timeout_seconds: float,
    retries: int,
    codex_client: CodexClient | None,
) -> ConsumeResetCreditResponse:
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
                code = _extract_error_code(data)
                message = _extract_error_message(data) or f"Reset credits consume failed ({status})"
                raise ConsumeResetCreditError(status, message, code=code)
            try:
                return ConsumeResetCreditResponse.model_validate(_success_payload(data))
            except (ValueError, ValidationError) as exc:
                logger.warning("Reset credits consume invalid payload request_id=%s", get_request_id())
                raise ConsumeResetCreditError(502, "Invalid reset credits consume payload") from exc
    finally:
        if owns_codex_client:
            close = getattr(active_codex_client, "close", None)
            if callable(close):
                await close()
    raise RuntimeError("unreachable reset credits consume retry state")


def _codex_response_status(response: object) -> int:
    value = getattr(response, "status_code", getattr(response, "status", None))
    if value is None:
        return 0
    return int(value)

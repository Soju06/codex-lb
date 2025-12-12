from __future__ import annotations

import asyncio
import logging

import aiohttp
from aiohttp_retry import ExponentialRetry, RetryClient
from pydantic import BaseModel, ConfigDict, ValidationError

from app.core.clients.http import get_http_client
from app.core.config.settings import get_settings
from app.core.types import JsonObject
from app.core.usage.models import UsagePayload
from app.core.utils.request_id import get_request_id

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
RETRY_START_TIMEOUT = 0.5
RETRY_MAX_TIMEOUT = 2.0

logger = logging.getLogger(__name__)


class UsageErrorDetail(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: str | None = None
    error_description: str | None = None


class UsageErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    error: UsageErrorDetail | str | None = None
    error_description: str | None = None
    message: str | None = None


class UsageFetchError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


async def fetch_usage(
    *,
    access_token: str,
    account_id: str | None,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
    max_retries: int | None = None,
    client: RetryClient | None = None,
) -> UsagePayload:
    settings = get_settings()
    usage_base = base_url or settings.upstream_base_url
    url = _usage_url(usage_base)
    timeout = aiohttp.ClientTimeout(total=timeout_seconds or settings.usage_fetch_timeout_seconds)
    retries = max_retries if max_retries is not None else settings.usage_fetch_max_retries
    headers = _usage_headers(access_token, account_id)
    retry_client = client or get_http_client().retry_client
    retry_options = _retry_options(retries + 1)

    try:
        async with retry_client.request(
            "GET",
            url,
            headers=headers,
            timeout=timeout,
            retry_options=retry_options,
        ) as resp:
            data = await _safe_json(resp)
            if resp.status >= 400:
                message = _extract_error_message(data) or f"Usage fetch failed ({resp.status})"
                logger.warning(
                    "Usage fetch failed request_id=%s status=%s message=%s",
                    get_request_id(),
                    resp.status,
                    message,
                )
                raise UsageFetchError(resp.status, message)
            try:
                return UsagePayload.model_validate(data)
            except ValidationError as exc:
                logger.warning(
                    "Usage fetch invalid payload request_id=%s",
                    get_request_id(),
                )
                raise UsageFetchError(502, "Invalid usage payload") from exc
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.warning(
            "Usage fetch error request_id=%s error=%s",
            get_request_id(),
            exc,
        )
        raise UsageFetchError(0, f"Usage fetch failed: {exc}") from exc


def _usage_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if "/backend-api" not in normalized:
        normalized = f"{normalized}/backend-api"
    return f"{normalized}/wham/usage"


def _usage_headers(access_token: str, account_id: str | None) -> dict[str, str]:
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


def _extract_error_message(payload: JsonObject) -> str | None:
    envelope = UsageErrorEnvelope.model_validate(payload)
    error = envelope.error
    if isinstance(error, UsageErrorDetail):
        return error.message or error.error_description
    if isinstance(error, str):
        return envelope.error_description or error
    return envelope.message


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

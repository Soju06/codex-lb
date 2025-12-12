from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging

import aiohttp
from pydantic import ValidationError

from app.core.auth import OpenAIAuthClaims, extract_id_token_claims
from app.core.clients.http import get_http_client
from app.core.balancer import PERMANENT_FAILURE_CODES
from app.core.auth.models import OAuthTokenPayload
from app.core.config.settings import get_settings
from app.core.types import JsonObject
from app.core.utils.request_id import get_request_id
from app.core.utils.time import to_utc_naive, utcnow

TOKEN_REFRESH_INTERVAL_DAYS = 8

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TokenRefreshResult:
    access_token: str
    refresh_token: str
    id_token: str
    account_id: str | None
    plan_type: str | None
    email: str | None


class RefreshError(Exception):
    def __init__(self, code: str, message: str, is_permanent: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.is_permanent = is_permanent


def should_refresh(last_refresh: datetime, now: datetime | None = None) -> bool:
    current = to_utc_naive(now) if now is not None else utcnow()
    last = to_utc_naive(last_refresh)
    interval_days = get_settings().token_refresh_interval_days or TOKEN_REFRESH_INTERVAL_DAYS
    return current - last > timedelta(days=interval_days)


def classify_refresh_error(code: str | None) -> bool:
    if not code:
        return False
    return code in PERMANENT_FAILURE_CODES


async def refresh_access_token(
    refresh_token: str,
    *,
    session: aiohttp.ClientSession | None = None,
) -> TokenRefreshResult:
    settings = get_settings()
    url = f"{settings.auth_base_url.rstrip('/')}/oauth/token"
    payload = {
        "grant_type": "refresh_token",
        "client_id": settings.oauth_client_id,
        "refresh_token": refresh_token,
        "scope": settings.oauth_scope,
    }
    timeout = aiohttp.ClientTimeout(total=settings.token_refresh_timeout_seconds)

    client_session = session or get_http_client().session
    headers: dict[str, str] = {}
    request_id = get_request_id()
    if request_id:
        headers["x-request-id"] = request_id
    async with client_session.post(url, json=payload, headers=headers, timeout=timeout) as resp:
        data = await _safe_json(resp)
        try:
            payload_data = OAuthTokenPayload.model_validate(data)
        except ValidationError as exc:
            logger.warning(
                "Token refresh response invalid request_id=%s",
                get_request_id(),
            )
            raise RefreshError("invalid_response", "Refresh response invalid", False) from exc
        if resp.status >= 400:
            logger.warning(
                "Token refresh failed request_id=%s status=%s",
                get_request_id(),
                resp.status,
            )
            raise _refresh_error_from_payload(payload_data, resp.status)

    if not payload_data.access_token or not payload_data.refresh_token or not payload_data.id_token:
        raise RefreshError("invalid_response", "Refresh response missing tokens", False)

    claims = extract_id_token_claims(payload_data.id_token)
    auth_claims = claims.auth or OpenAIAuthClaims()
    account_id = auth_claims.chatgpt_account_id or claims.chatgpt_account_id
    plan_type = auth_claims.chatgpt_plan_type or claims.chatgpt_plan_type
    email = claims.email

    return TokenRefreshResult(
        access_token=payload_data.access_token,
        refresh_token=payload_data.refresh_token,
        id_token=payload_data.id_token,
        account_id=account_id,
        plan_type=plan_type,
        email=email,
    )


async def _safe_json(resp: aiohttp.ClientResponse) -> JsonObject:
    try:
        data = await resp.json(content_type=None)
    except Exception:
        text = await resp.text()
        return {"error": {"message": text.strip()}}
    return data if isinstance(data, dict) else {"error": {"message": str(data)}}


def _refresh_error_from_payload(payload: OAuthTokenPayload, status_code: int) -> RefreshError:
    code = _extract_error_code(payload) or f"http_{status_code}"
    message = _extract_error_message(payload) or f"Token refresh failed ({status_code})"
    return RefreshError(code, message, classify_refresh_error(code))


def _extract_error_code(payload: OAuthTokenPayload) -> str | None:
    error = payload.error
    if isinstance(error, dict):
        code = error.get("code") or error.get("error")
        return code if isinstance(code, str) else None
    if isinstance(error, str):
        return error
    return payload.error_code or payload.code


def _extract_error_message(payload: OAuthTokenPayload) -> str | None:
    error = payload.error
    if isinstance(error, dict):
        message = error.get("message") or error.get("error_description")
        return message if isinstance(message, str) else None
    if isinstance(error, str):
        return payload.error_description or error
    return payload.message

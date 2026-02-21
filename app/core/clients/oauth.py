from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote, urlencode

import aiohttp
from pydantic import ValidationError

from app.core.auth.models import DeviceCodePayload, OAuthTokenPayload
from app.core.clients.http import get_http_client
from app.core.config.settings import get_settings
from app.core.types import JsonObject
from app.core.utils.request_id import get_request_id

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeviceCode:
    verification_url: str
    user_code: str
    device_auth_id: str
    interval_seconds: int
    expires_in_seconds: int


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    refresh_token: str
    id_token: str


class OAuthError(Exception):
    def __init__(self, code: str, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def generate_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(32)
    return verifier, pkce_challenge(verifier)


def build_authorization_url(
    *,
    state: str,
    code_challenge: str,
    base_url: str | None = None,
    client_id: str | None = None,
    redirect_uri: str | None = None,
    scope: str | None = None,
) -> str:
    settings = get_settings()
    auth_base = (base_url or settings.auth_base_url).rstrip("/")
    authorization_scope = scope or _ensure_offline_access(settings.oauth_scope)
    params = {
        "response_type": "code",
        "client_id": client_id or settings.oauth_client_id,
        "redirect_uri": redirect_uri or settings.oauth_redirect_uri,
        "scope": authorization_scope,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": "codex_cli_rs",
    }
    query = urlencode(params, quote_via=quote)
    return f"{auth_base}/oauth/authorize?{query}"


async def exchange_authorization_code(
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str | None = None,
    base_url: str | None = None,
    client_id: str | None = None,
    timeout_seconds: float | None = None,
    session: aiohttp.ClientSession | None = None,
) -> OAuthTokens:
    settings = get_settings()
    url = f"{(base_url or settings.auth_base_url).rstrip('/')}/oauth/token"
    payload = {
        "grant_type": "authorization_code",
        "client_id": client_id or settings.oauth_client_id,
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri or settings.oauth_redirect_uri,
    }
    encoded = urlencode(payload, quote_via=quote)
    timeout = aiohttp.ClientTimeout(total=timeout_seconds or settings.oauth_timeout_seconds)

    client_session = session or get_http_client().session
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    request_id = get_request_id()
    if request_id:
        headers["x-request-id"] = request_id
    async with client_session.post(
        url,
        data=encoded,
        headers=headers,
        timeout=timeout,
    ) as resp:
        data = await _safe_json(resp)
        try:
            payload = OAuthTokenPayload.model_validate(data)
        except ValidationError as exc:
            logger.warning(
                "OAuth token response invalid request_id=%s",
                get_request_id(),
            )
            raise OAuthError("invalid_response", "OAuth response invalid") from exc
        if resp.status >= 400:
            logger.warning(
                "OAuth token request failed request_id=%s status=%s",
                get_request_id(),
                resp.status,
            )
            raise _oauth_error_from_payload(payload, resp.status)

    return _parse_tokens(payload)


async def request_device_code(
    *,
    base_url: str | None = None,
    client_id: str | None = None,
    timeout_seconds: float | None = None,
    session: aiohttp.ClientSession | None = None,
) -> DeviceCode:
    settings = get_settings()
    auth_base = (base_url or settings.auth_base_url).rstrip("/")
    url = f"{auth_base}/api/accounts/deviceauth/usercode"
    payload = {
        "client_id": client_id or settings.oauth_client_id,
    }
    timeout = aiohttp.ClientTimeout(total=timeout_seconds or settings.oauth_timeout_seconds)

    client_session = session or get_http_client().session
    headers: dict[str, str] = {}
    request_id = get_request_id()
    if request_id:
        headers["x-request-id"] = request_id
    async with client_session.post(url, json=payload, headers=headers, timeout=timeout) as resp:
        data = await _safe_json(resp)
        if resp.status >= 400:
            if resp.status == 404:
                raise OAuthError(
                    "device_auth_unavailable",
                    (
                        "Device code login is not enabled for this Codex server. "
                        "Use the browser login or verify the server URL."
                    ),
                    resp.status,
                )
            logger.warning(
                "Device auth request failed request_id=%s status=%s",
                get_request_id(),
                resp.status,
            )
            raise OAuthError(
                "device_auth_failed",
                f"Device code request failed with status {resp.status}",
                resp.status,
            )
        try:
            payload_data = DeviceCodePayload.model_validate(data)
        except ValidationError as exc:
            logger.warning(
                "Device auth response invalid request_id=%s",
                get_request_id(),
            )
            raise OAuthError("invalid_response", "Device auth response invalid") from exc
    verification_url = f"{auth_base}/codex/device"
    user_code = payload_data.user_code
    device_auth_id = payload_data.device_auth_id
    interval = payload_data.interval if payload_data.interval is not None else 0
    expires_in = payload_data.expires_in or 0
    if expires_in <= 0:
        expires_in = _expires_in_seconds(payload_data.expires_at) or 900

    if not user_code or not device_auth_id:
        raise OAuthError("invalid_response", "Device auth response missing fields")

    return DeviceCode(
        verification_url=verification_url,
        user_code=user_code,
        device_auth_id=device_auth_id,
        interval_seconds=interval,
        expires_in_seconds=expires_in,
    )


async def exchange_device_token(
    *,
    device_auth_id: str,
    user_code: str,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
    session: aiohttp.ClientSession | None = None,
) -> OAuthTokens | None:
    settings = get_settings()
    url = f"{(base_url or settings.auth_base_url).rstrip('/')}/api/accounts/deviceauth/token"
    payload = {
        "device_auth_id": device_auth_id,
        "user_code": _normalize_device_user_code(user_code),
    }
    timeout = aiohttp.ClientTimeout(total=timeout_seconds or settings.oauth_timeout_seconds)

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
                "Device token response invalid request_id=%s",
                get_request_id(),
            )
            raise OAuthError("invalid_response", "Device auth response invalid") from exc
        if resp.status in (403, 404):
            return None
        if resp.status >= 400:
            if _is_pending_error(payload_data):
                return None
            logger.warning(
                "Device token request failed request_id=%s status=%s",
                get_request_id(),
                resp.status,
            )
            raise _oauth_error_from_payload(payload_data, resp.status)
        if _is_pending_error(payload_data):
            return None

    if payload_data.authorization_code:
        if not payload_data.code_verifier:
            raise OAuthError("invalid_response", "Device auth response missing code verifier")
        redirect_uri = f"{(base_url or settings.auth_base_url).rstrip('/')}/deviceauth/callback"
        return await exchange_authorization_code(
            code=payload_data.authorization_code,
            code_verifier=payload_data.code_verifier,
            redirect_uri=redirect_uri,
            base_url=base_url,
            client_id=settings.oauth_client_id,
            timeout_seconds=timeout_seconds,
        )

    return _parse_tokens(payload_data)


def _ensure_offline_access(scope: str) -> str:
    if "offline_access" in scope.split():
        return scope
    return f"{scope} offline_access"


def _parse_tokens(payload: OAuthTokenPayload) -> OAuthTokens:
    if not payload.access_token or not payload.refresh_token or not payload.id_token:
        raise OAuthError("invalid_response", "OAuth response missing tokens")
    return OAuthTokens(
        access_token=payload.access_token,
        refresh_token=payload.refresh_token,
        id_token=payload.id_token,
    )


async def _safe_json(resp: aiohttp.ClientResponse) -> JsonObject:
    try:
        data = await resp.json(content_type=None)
    except Exception:
        text = await resp.text()
        return {"error": {"message": text.strip()}}
    return data if isinstance(data, dict) else {"error": {"message": str(data)}}


def _oauth_error_from_payload(payload: OAuthTokenPayload, status_code: int) -> OAuthError:
    code = _extract_error_code(payload) or f"http_{status_code}"
    message = _extract_error_message(payload) or f"OAuth request failed ({status_code})"
    return OAuthError(code, message, status_code)


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


def _is_pending_error(payload: OAuthTokenPayload) -> bool:
    code = _extract_error_code(payload)
    if code in {"authorization_pending", "slow_down"}:
        return True
    status = payload.status
    if status and status.lower() in {"pending", "authorization_pending"}:
        return True
    return False


def _expires_in_seconds(expires_at: str | None) -> int | None:
    if not expires_at:
        return None
    try:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    delta = (parsed - now).total_seconds()
    if delta <= 0:
        return None
    return int(delta)


def _normalize_device_user_code(value: str) -> str:
    return "".join(ch for ch in value.upper() if ch.isalnum())

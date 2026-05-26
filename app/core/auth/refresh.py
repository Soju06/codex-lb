from __future__ import annotations

import contextvars
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import aiohttp
from pydantic import ValidationError

from app.core.auth import OpenAIAuthClaims, extract_id_token_claims
from app.core.auth.models import OAuthTokenPayload
from app.core.balancer import PERMANENT_FAILURE_CODES
from app.core.clients.account_http import lease_account_http_session
from app.core.config.settings import get_settings
from app.core.types import JsonObject
from app.core.utils.request_id import get_request_id
from app.core.utils.time import to_utc_naive, utcnow

TOKEN_REFRESH_INTERVAL_DAYS = 8
# Salt the per-account jitter so the SHA-256 input cannot collide with
# any other use of ``account_id`` we might add later (e.g. a separate
# scheduling axis). The actual value is irrelevant; only its stability
# matters.
_TOKEN_REFRESH_JITTER_SALT = b"codex-lb:token-refresh-jitter:v1"

logger = logging.getLogger(__name__)
_TOKEN_REFRESH_TIMEOUT_OVERRIDE: contextvars.ContextVar[float | None] = contextvars.ContextVar(
    "token_refresh_timeout_override",
    default=None,
)


@dataclass(frozen=True)
class TokenRefreshResult:
    access_token: str
    refresh_token: str
    id_token: str
    account_id: str | None
    plan_type: str | None
    email: str | None


class RefreshError(Exception):
    def __init__(self, code: str, message: str, is_permanent: bool, *, transport_error: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.is_permanent = is_permanent
        self.transport_error = transport_error


def should_refresh(
    last_refresh: datetime,
    now: datetime | None = None,
    *,
    account_id: str | None = None,
) -> bool:
    """Return ``True`` when the access token should be refreshed.

    The effective interval is
    ``interval_days ± jitter_hours``, where the offset is a stable
    deterministic function of ``account_id``. Two accounts onboarded on
    the same day therefore land on different refresh days, which keeps
    upstream OAuth from observing a clustered "fleet refresh" pattern
    against the same ``oauth_client_id``.

    When ``account_id`` is omitted (legacy / defensive callers, tests
    that do not care about the specific account) the un-jittered
    interval is used.
    """

    settings = get_settings()
    current = to_utc_naive(now) if now is not None else utcnow()
    last = to_utc_naive(last_refresh)
    interval_days = settings.token_refresh_interval_days or TOKEN_REFRESH_INTERVAL_DAYS
    base = timedelta(days=interval_days)
    if account_id:
        jitter_hours = float(settings.account_token_refresh_jitter_hours)
        offset_seconds = _refresh_jitter_offset_seconds(account_id, jitter_hours)
        effective = base + timedelta(seconds=offset_seconds)
    else:
        effective = base
    # Defense in depth against a misconfigured ``jitter_hours`` (the
    # setting is bounded ``le=96`` against an 8-day default base, but
    # operators running custom ``token_refresh_interval_days`` values
    # could still produce a negative effective duration). A negative
    # ``effective`` would make ``current - last > effective`` always
    # True, triggering an OAuth refresh on every request. Floor at
    # one hour so a misconfiguration degrades to "refresh hourly"
    # rather than a refresh storm.
    if effective < timedelta(hours=1):
        effective = timedelta(hours=1)
    return current - last > effective


def _refresh_jitter_offset_seconds(account_id: str, jitter_hours: float) -> float:
    """Return a stable offset in ``[-jitter_hours*3600, +jitter_hours*3600]``.

    The offset is derived from ``SHA-256(salt || account_id)`` so it is:

    - Stable across calls for the same ``account_id``
    - Independent across distinct ``account_id``s (no observable
      correlation)
    - Independent of ``last_refresh`` so the next-refresh day is
      predictable per account, which looks like a normal device
      schedule rather than "every cycle is at a new random time".

    A zero or negative ``jitter_hours`` disables the offset entirely.
    """

    if jitter_hours <= 0.0:
        return 0.0
    digest = hashlib.sha256(_TOKEN_REFRESH_JITTER_SALT + account_id.encode("utf-8")).digest()
    # Take 8 bytes of the digest as an unsigned int and map it onto the
    # symmetric window. ``2 * jitter_hours`` is the full window in
    # hours, modulo'd into seconds.
    raw = int.from_bytes(digest[:8], byteorder="big", signed=False)
    full_window_seconds = 2.0 * jitter_hours * 3600.0
    fraction = (raw % 1_000_000_007) / 1_000_000_007  # [0, 1)
    return fraction * full_window_seconds - jitter_hours * 3600.0


def classify_refresh_error(code: str | None) -> bool:
    if not code:
        return False
    return code in PERMANENT_FAILURE_CODES


async def refresh_access_token(
    refresh_token: str,
    *,
    account_id: str | None = None,
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
    timeout = aiohttp.ClientTimeout(total=_effective_token_refresh_timeout(settings.token_refresh_timeout_seconds))

    headers: dict[str, str] = {}
    request_id = get_request_id()
    if request_id:
        headers["x-request-id"] = request_id
    try:
        async with lease_account_http_session(account_id or "", session) as client_session:
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
    except RefreshError:
        raise
    except Exception as exc:
        # Catch-all for transport / read errors.
        message = str(exc) or exc.__class__.__name__
        raise RefreshError(
            "transport_error",
            f"Transport error during token refresh: {message}",
            False,
            transport_error=True,
        ) from exc

    if not payload_data.access_token or not payload_data.refresh_token or not payload_data.id_token:
        raise RefreshError("invalid_response", "Refresh response missing tokens", False)

    claims = extract_id_token_claims(payload_data.id_token)
    auth_claims = claims.auth or OpenAIAuthClaims()
    # Local rename: the function parameter ``account_id`` carries the
    # codex-lb-side account id (used above to resolve the per-account
    # SOCKS5 session). The JWT claim carries the upstream OpenAI
    # ChatGPT account id, which is what ``TokenRefreshResult.account_id``
    # has historically returned. Keep the names distinct so any future
    # logic added below this point can't accidentally pick the wrong
    # one.
    upstream_account_id = auth_claims.chatgpt_account_id or claims.chatgpt_account_id
    plan_type = auth_claims.chatgpt_plan_type or claims.chatgpt_plan_type
    email = claims.email

    return TokenRefreshResult(
        access_token=payload_data.access_token,
        refresh_token=payload_data.refresh_token,
        id_token=payload_data.id_token,
        account_id=upstream_account_id,
        plan_type=plan_type,
        email=email,
    )


def push_token_refresh_timeout_override(timeout_seconds: float | None) -> contextvars.Token[float | None]:
    return _TOKEN_REFRESH_TIMEOUT_OVERRIDE.set(timeout_seconds)


def pop_token_refresh_timeout_override(token: contextvars.Token[float | None]) -> None:
    _TOKEN_REFRESH_TIMEOUT_OVERRIDE.reset(token)


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


def _effective_token_refresh_timeout(configured_timeout_seconds: float) -> float:
    override = _TOKEN_REFRESH_TIMEOUT_OVERRIDE.get()
    if override is None:
        return configured_timeout_seconds
    return max(0.001, min(configured_timeout_seconds, override))


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

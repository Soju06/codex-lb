from __future__ import annotations

import asyncio
import contextvars
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import aiohttp
from pydantic import ValidationError

from app.core.auth import (
    OpenAIAuthClaims,
    clean_account_identity_part,
    extract_id_token_claims,
    normalize_seat_type,
    resolve_seat_identity,
)
from app.core.auth.models import OAuthTokenPayload
from app.core.balancer import PERMANENT_FAILURE_CODES
from app.core.clients.codex import (
    CodexClient,
    CodexTransportError,
    create_codex_session,
    require_route_or_direct_egress_opt_in,
)
from app.core.clients.http import lease_http_session
from app.core.config.settings import AUTH_BASE_URL, OAUTH_CLIENT_ID, OAUTH_SCOPE, get_settings
from app.core.resilience.network_recovery import (
    PROCESS_NETWORK_UNAVAILABLE_CODE,
    is_pre_dispatch_connection_failure,
    is_proxy_endpoint_failure,
    process_network_error_code,
)
from app.core.types import JsonObject
from app.core.upstream_proxy import ResolvedUpstreamRoute
from app.core.utils.request_id import get_request_id
from app.core.utils.time import to_utc_naive, utcnow

TOKEN_REFRESH_INTERVAL_DAYS = 8

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
    workspace_id: str | None = None
    workspace_label: str | None = None
    seat_type: str | None = None
    chatgpt_user_id: str | None = None


class RefreshError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        is_permanent: bool,
        *,
        transport_error: bool = False,
        transport_error_code: str | None = None,
        retryable_same_contract: bool = False,
        failed_session: aiohttp.ClientSession | None = None,
        upstream_proxy_fail_closed_reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.is_permanent = is_permanent
        self.transport_error = transport_error
        self.transport_error_code = transport_error_code
        self.retryable_same_contract = retryable_same_contract
        self.failed_session = failed_session
        self.upstream_proxy_fail_closed_reason = upstream_proxy_fail_closed_reason


# Transient cross-replica refresh-contention codes fall into TWO semantically
# distinct categories that share the SAME external outcome (retryable, never
# cached, no account-health penalty, failover where applicable) but are NOT the
# same internal condition. Conflating them would mask a genuinely degraded state
# behind benign contention, so they are classified separately.
#
# (1) BENIGN CLAIM CONTENTION -- ``refresh_claim_timeout``: a peer replica holds
#     the account's refresh claim (past the wait budget, or admission/budget was
#     exhausted before the exchange could even start). THIS caller NEVER
#     exchanged the token; the account's OAuth credentials are entirely healthy;
#     only its refresh claim is contended. Pure contention -> retry, no penalty.
REFRESH_CLAIM_CONTENTION_CODES: frozenset[str] = frozenset({"refresh_claim_timeout"})

# (2) POST-EXCHANGE PERSIST/STATUS CAS CONFLICT -- ``token_persist_conflict`` /
#     ``status_downgrade_conflict``: raised AFTER the upstream OAuth exchange has
#     already run. ``token_persist_conflict`` in particular means the single-use
#     refresh token was already CONSUMED upstream but the guarded writes could
#     not persist the rotated token (a same-plaintext re-encryption storm the
#     coordinator could not win an atomic compare-and-set window against), so the
#     database may still hold the just-consumed token; ``status_downgrade_conflict``
#     follows a PERMANENT refresh failure whose guarded REAUTH status write lost a
#     compare-and-set. These signal a rare, more-serious internal race than benign
#     contention and are logged/observed DISTINCTLY. They remain transient (a
#     plain retry re-runs the WHOLE refresh -- a fresh upstream re-exchange, never
#     a reuse of the possibly-consumed stored token; see ``refresh_account``), so
#     their external outcome matches benign contention.
REFRESH_PERSIST_CONFLICT_CODES: frozenset[str] = frozenset(
    {
        "token_persist_conflict",
        "status_downgrade_conflict",
    }
)

# Union of both categories. Every code here carries ``transport_error=True`` (it
# is transient and retryable), but crucially the account's OAuth credentials are
# healthy. This union is deliberately DISJOINT from ``code == "transport_error"``,
# which ``refresh_access_token`` raises for a GENUINE OAuth transport failure (the
# OAuth request itself timing out / the upstream connection failing).
TRANSIENT_REFRESH_CONTENTION_CODES: frozenset[str] = REFRESH_CLAIM_CONTENTION_CODES | REFRESH_PERSIST_CONFLICT_CODES


def is_refresh_claim_contention(exc: RefreshError) -> bool:
    """True ONLY for benign cross-replica refresh-CLAIM contention.

    Narrow predicate: matches ``refresh_claim_timeout`` (a peer replica holds the
    account's refresh claim and THIS caller never exchanged the token). Use this
    where the code specifically means "a peer holds the claim, we did not
    exchange". For the failover / skip-penalty EXTERNAL outcome (which treats
    benign contention and post-exchange persist conflicts identically) gate on
    ``is_transient_refresh_contention`` instead.
    """
    return exc.transport_error and exc.code in REFRESH_CLAIM_CONTENTION_CODES


def is_refresh_persist_conflict(exc: RefreshError) -> bool:
    """True for a POST-EXCHANGE guarded-write compare-and-set conflict.

    Matches ``token_persist_conflict`` / ``status_downgrade_conflict`` -- raised
    after the OAuth exchange when the rotated-token or REAUTH-status guarded write
    lost a compare-and-set. Distinct from benign claim contention: for
    ``token_persist_conflict`` the single-use token was already consumed upstream
    but its rotation could not be persisted, so the database may still hold the
    consumed token. This is a rarer, more-serious internal race worth surfacing
    distinctly in logs/metrics, though its external (retryable, unpenalized)
    outcome matches benign contention.
    """
    return exc.transport_error and exc.code in REFRESH_PERSIST_CONFLICT_CODES


def is_transient_refresh_contention(exc: RefreshError) -> bool:
    """True for EITHER benign claim contention OR a post-exchange persist conflict.

    Proxy failover paths gate their "skip the account-health penalty" behavior on
    THIS predicate rather than on the broad ``transport_error`` flag. A GENUINE
    OAuth transport failure (``code == "transport_error"`` -- the OAuth request
    itself timing out or the upstream connection failing) is transient too but IS
    the account/route's fault, so it MUST retain its normal health accounting
    (``record_error`` / ``_handle_stream_error``) and push the broken account into
    transient backoff instead of being reselected immediately. Only the
    claim/persist-CAS codes -- where the account's credentials are healthy -- skip
    the penalty. The two categories are separated by ``is_refresh_claim_contention``
    (benign) and ``is_refresh_persist_conflict`` (post-exchange) for observability;
    both take the same unpenalized retryable failover path here.
    """
    return exc.transport_error and exc.code in TRANSIENT_REFRESH_CONTENTION_CODES


def refresh_contention_kind(exc: RefreshError) -> str | None:
    """Classify a transient refresh contention for DISTINCT observability.

    Returns ``"claim_contention"`` for benign peer-holds-claim contention,
    ``"persist_conflict"`` for a post-exchange guarded-write CAS conflict, or
    ``None`` when ``exc`` is not transient refresh contention. Log/metric call
    sites use this so a rare, more-serious post-exchange persist conflict is
    surfaced distinctly rather than lumped with benign claim contention.
    """
    if not exc.transport_error:
        return None
    if exc.code in REFRESH_CLAIM_CONTENTION_CODES:
        return "claim_contention"
    if exc.code in REFRESH_PERSIST_CONFLICT_CODES:
        return "persist_conflict"
    return None


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
    route: ResolvedUpstreamRoute | None = None,
    codex_client: CodexClient | None = None,
    allow_direct_egress: bool = False,
) -> TokenRefreshResult:
    settings = get_settings()
    url = f"{AUTH_BASE_URL}/oauth/token"
    payload = {
        "grant_type": "refresh_token",
        "client_id": OAUTH_CLIENT_ID,
        "refresh_token": refresh_token,
        "scope": OAUTH_SCOPE,
    }
    timeout = aiohttp.ClientTimeout(total=_effective_token_refresh_timeout(settings.token_refresh_timeout_seconds))

    headers: dict[str, str] = {}
    request_id = get_request_id()
    if request_id:
        headers["x-request-id"] = request_id
    require_route_or_direct_egress_opt_in(
        route=route,
        allow_direct_egress=allow_direct_egress,
        operation="token refresh",
    )
    failed_session: aiohttp.ClientSession | None = None
    try:
        if route is not None:
            owns_codex_client = codex_client is None
            active_codex_client = codex_client or CodexClient(create_codex_session())
            try:
                resp = await active_codex_client.request(
                    "POST",
                    url,
                    route=route,
                    json=payload,
                    headers=headers,
                    timeout=_effective_token_refresh_timeout(settings.token_refresh_timeout_seconds),
                )
                data = await _safe_codex_json(resp)
                status = int(getattr(resp, "status_code", getattr(resp, "status", 0)))
                payload_data = _validate_token_payload(data)
                if status >= 400:
                    logger.warning("Token refresh failed request_id=%s status=%s", get_request_id(), status)
                    raise _refresh_error_from_payload(payload_data, status)
            finally:
                if owns_codex_client:
                    await active_codex_client.close()
        else:
            async with lease_http_session(session) as client_session:
                failed_session = client_session
                async with client_session.post(url, json=payload, headers=headers, timeout=timeout) as resp:
                    data = await _safe_json(resp)
                    payload_data = _validate_token_payload(data)
                    if resp.status >= 400:
                        logger.warning(
                            "Token refresh failed request_id=%s status=%s",
                            get_request_id(),
                            resp.status,
                        )
                        raise _refresh_error_from_payload(payload_data, resp.status)
    except RefreshError:
        raise
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, CodexTransportError) as exc:
        message = str(exc) or exc.__class__.__name__
        transport_error_code = (
            exc.error_code
            if isinstance(exc, CodexTransportError) and exc.error_code is not None
            else process_network_error_code(
                exc,
                fallback="transport_error",
                include_permanent_dns=route is None and not is_proxy_endpoint_failure(exc),
            )
        )
        retryable_same_contract = (
            exc.retryable_same_contract
            if isinstance(exc, CodexTransportError)
            else is_pre_dispatch_connection_failure(exc)
        )
        raise RefreshError(
            "transport_error",
            f"Transport error during token refresh: {message}",
            False,
            transport_error=True,
            transport_error_code=(
                transport_error_code if transport_error_code == PROCESS_NETWORK_UNAVAILABLE_CODE else None
            ),
            # A rotating refresh token may already be consumed once response
            # headers/body reads begin. Only connector-proven pre-dispatch
            # failures can retry the same refresh contract.
            retryable_same_contract=retryable_same_contract,
            failed_session=failed_session if route is None else None,
        ) from exc

    if not payload_data.access_token or not payload_data.refresh_token or not payload_data.id_token:
        raise RefreshError("invalid_response", "Refresh response missing tokens", False)

    claims = extract_id_token_claims(payload_data.id_token)
    auth_claims = claims.auth or OpenAIAuthClaims()
    account_id = auth_claims.chatgpt_account_id or claims.chatgpt_account_id
    plan_type = auth_claims.chatgpt_plan_type or claims.chatgpt_plan_type
    email = claims.email
    workspace_id = clean_account_identity_part(auth_claims.workspace_id or claims.workspace_id)
    workspace_label = clean_account_identity_part(auth_claims.workspace_label or claims.workspace_label)
    seat_type = normalize_seat_type(auth_claims.seat_type or claims.seat_type)
    chatgpt_user_id = resolve_seat_identity(claims, auth_claims)

    return TokenRefreshResult(
        access_token=payload_data.access_token,
        refresh_token=payload_data.refresh_token,
        id_token=payload_data.id_token,
        account_id=account_id,
        plan_type=plan_type,
        email=email,
        workspace_id=workspace_id,
        workspace_label=workspace_label,
        seat_type=seat_type,
        chatgpt_user_id=chatgpt_user_id,
    )


def push_token_refresh_timeout_override(timeout_seconds: float | None) -> contextvars.Token[float | None]:
    return _TOKEN_REFRESH_TIMEOUT_OVERRIDE.set(timeout_seconds)


def pop_token_refresh_timeout_override(token: contextvars.Token[float | None]) -> None:
    _TOKEN_REFRESH_TIMEOUT_OVERRIDE.reset(token)


def get_token_refresh_timeout_override() -> float | None:
    """Caller-scoped refresh budget (seconds); ``None`` when no caller set one.

    Set by the proxy request path around ``AuthManager.ensure_fresh`` so that
    everything the (shielded, caller-outliving) refresh task does — including
    waiting on a foreign cross-replica refresh claim — stays bounded by the
    budget of the request that started it.
    """
    return _TOKEN_REFRESH_TIMEOUT_OVERRIDE.get()


async def _safe_json(resp: aiohttp.ClientResponse) -> JsonObject:
    try:
        data = await resp.json(content_type=None)
    except Exception:
        text = await resp.text()
        return {"error": {"message": text.strip()}}
    return data if isinstance(data, dict) else {"error": {"message": str(data)}}


async def _safe_codex_json(resp: object) -> JsonObject:
    json_method = getattr(resp, "json", None)
    try:
        if callable(json_method):
            data = json_method()
            if asyncio.iscoroutine(data):
                data = await data
        else:
            text_value = getattr(resp, "text", None)
            if not isinstance(text_value, str):
                content = getattr(resp, "content", b"")
                text_value = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)
            import json

            data = json.loads(text_value)
    except Exception:
        text_value = getattr(resp, "text", "")
        return {"error": {"message": str(text_value).strip()}}
    return data if isinstance(data, dict) else {"error": {"message": str(data)}}


def _validate_token_payload(data: JsonObject) -> OAuthTokenPayload:
    try:
        return OAuthTokenPayload.model_validate(data)
    except ValidationError as exc:
        logger.warning("Token refresh response invalid request_id=%s", get_request_id())
        raise RefreshError("invalid_response", "Refresh response invalid", False) from exc


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

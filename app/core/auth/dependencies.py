from __future__ import annotations

import hashlib
import logging
from typing import cast

from fastapi import Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.requests import HTTPConnection

from app.core.auth.api_key_cache import get_api_key_cache
from app.core.auth.dashboard_mode import DashboardAuthMode, get_dashboard_request_auth
from app.core.clients.usage import UsageFetchError, fetch_usage
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.crypto import TokenEncryptor
from app.core.exceptions import (
    DashboardAuthError,
    DashboardForbiddenError,
    ProxyAuthError,
    ProxyUpstreamError,
)
from app.core.request_locality import is_local_request, is_unauthenticated_client_allowed
from app.core.upstream_proxy import UpstreamProxyRouteError, resolve_upstream_route
from app.core.utils.time import utcnow
from app.db.session import get_background_session
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyData, ApiKeyInvalidError, ApiKeysService
from app.modules.dashboard_auth.service import DASHBOARD_SESSION_COOKIE, get_dashboard_session_store

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(description="API key (e.g. sk-clb-…)", auto_error=False)

API_KEY_TOKEN_PREFIX = "sk-clb-"


# --- Error format markers ---


def set_openai_error_format(request: Request) -> None:
    request.state.error_format = "openai"


def set_dashboard_error_format(request: Request) -> None:
    request.state.error_format = "dashboard"


# --- Proxy API key auth ---


async def validate_proxy_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> ApiKeyData | None:
    authorization = None if credentials is None else f"Bearer {credentials.credentials}"
    return await validate_proxy_api_key_authorization(authorization, request=request)


async def validate_proxy_api_key_authorization(
    authorization: str | None,
    *,
    request: HTTPConnection | None = None,
) -> ApiKeyData | None:
    settings = await get_settings_cache().get()
    if not settings.api_key_auth_enabled:
        if request is None or is_local_request(request) or _is_proxy_unauthenticated_client_allowed(request):
            # Trusted clients stay keyless; any bearer they send is ignored.
            return None
        if getattr(settings, "team_mode_enabled", False):
            untrusted_token = _extract_bearer_token(authorization)
            if untrusted_token and untrusted_token.startswith(API_KEY_TOKEN_PREFIX):
                return await _validate_api_key_token(untrusted_token)
        raise ProxyAuthError("Proxy authentication must be configured before remote access is allowed")

    token = _extract_bearer_token(authorization)
    if not token:
        raise ProxyAuthError("Missing API key in Authorization header")

    return await _validate_api_key_token(token)


async def _validate_api_key_token(token: str) -> ApiKeyData:
    """Validate a plain API key token and return the typed key data."""

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    cache = get_api_key_cache()
    cached = cast(ApiKeyData | None, await cache.get(token_hash))
    if cached is not None:
        if cached.expires_at is not None and cached.expires_at <= utcnow():
            await cache.invalidate(token_hash)
        else:
            return cached

    version_before_read = cache.version
    async with get_background_session() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        try:
            validated = await service.validate_key(token)
            await cache.set(token_hash, validated, if_version=version_before_read)
            return validated
        except ApiKeyInvalidError as exc:
            raise ProxyAuthError(str(exc)) from exc


# --- Self-service usage endpoint auth (always requires valid key) ---


async def validate_usage_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> ApiKeyData:
    """Validate API key for self-service usage endpoint.

    Unlike ``validate_proxy_api_key``, this dependency always requires a valid
    Bearer API key, regardless of the global ``api_key_auth_enabled`` setting.
    Raises ProxyAuthError when the key is missing or invalid.
    """
    token = _extract_bearer_token(None if credentials is None else f"Bearer {credentials.credentials}")
    if not token:
        raise ProxyAuthError("Missing API key in Authorization header")

    return await _validate_api_key_token(token)


# --- Dashboard session auth ---


async def validate_dashboard_session(request: Request) -> None:
    settings = await get_settings_cache().get()

    # Team mode opens the network path to untrusted clients, so the dashboard and every
    # admin API behind it must be closed to them first — before the DISABLED /
    # trusted-header short-circuit, which would otherwise let them straight in.
    if getattr(settings, "team_mode_enabled", False) and not _is_trusted_dashboard_client(request):
        raise DashboardForbiddenError(
            "Dashboard is restricted to trusted clients in team mode",
            code="team_mode_untrusted_client",
        )

    request_auth = get_dashboard_request_auth(request)
    if request_auth is not None:
        return

    password_required = bool(settings.password_hash)
    requires_auth = password_required or settings.totp_required_on_login
    if get_dashboard_request_auth_mode() == DashboardAuthMode.TRUSTED_HEADER and not requires_auth:
        raise DashboardAuthError("Reverse proxy authentication is required", code="proxy_auth_required")
    if not requires_auth:
        if not is_local_request(request):
            raise DashboardAuthError(
                "Remote bootstrap is required before dashboard access is allowed",
                code="bootstrap_required",
            )
        return

    if not password_required and settings.totp_required_on_login:
        logger.warning(
            "dashboard_auth_migration_inconsistency password_hash is NULL"
            " while totp_required_on_login=true metric=dashboard_auth_migration_inconsistency"
        )

    session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    state = get_dashboard_session_store().get(session_id)
    if state is None:
        raise DashboardAuthError("Authentication is required")
    if password_required and not state.password_verified:
        raise DashboardAuthError("Authentication is required")
    if settings.totp_required_on_login and not state.totp_verified:
        raise DashboardAuthError("TOTP verification is required for dashboard access", code="totp_required")


def get_dashboard_request_auth_mode() -> DashboardAuthMode:
    return get_settings().dashboard_auth_mode


def _is_trusted_dashboard_client(request: HTTPConnection) -> bool:
    """True for local clients and for the configured unauthenticated CIDR allowlist.

    Both helpers resolve the client IP through ``resolve_request_client_host``, so a
    tailnet peer arriving through the trusted local reverse proxy is judged by its own
    address rather than by the loopback socket peer.
    """
    return is_local_request(request) or _is_proxy_unauthenticated_client_allowed(request)


# Kept under the original private name so existing call sites and the tests that
# monkeypatch it keep working; the implementation now lives beside the other
# client-IP helpers in app/core/request_locality.py.
_is_proxy_unauthenticated_client_allowed = is_unauthenticated_client_allowed


# --- Codex usage caller identity auth ---


async def validate_codex_usage_identity(request: Request) -> ApiKeyData | None:
    token = _extract_bearer_token(request.headers.get("Authorization"))
    if not token:
        raise ProxyAuthError("Missing ChatGPT token in Authorization header")

    raw_account_id = request.headers.get("chatgpt-account-id")
    account_id = raw_account_id.strip() if raw_account_id else ""
    if not account_id:
        if token.startswith("sk-clb-"):
            return await _validate_api_key_token(token)
        raise ProxyAuthError("Missing chatgpt-account-id header")

    async with get_background_session() as session:
        accounts_repo = AccountsRepository(session)
        account = await accounts_repo.get_active_by_chatgpt_account_id(account_id)
        if account is None:
            raise ProxyAuthError("Unknown or inactive chatgpt-account-id")
        try:
            route = await resolve_upstream_route(
                session,
                account_id=account.id,
                operation="usage_identity",
                scope="account",
                encryptor=TokenEncryptor(),
            )
        except UpstreamProxyRouteError as exc:
            raise ProxyUpstreamError("Unable to resolve upstream proxy route for ChatGPT credentials") from exc

    try:
        await fetch_usage(
            access_token=token,
            account_id=account_id,
            route=route,
            allow_direct_egress=route is None,
        )
    except UsageFetchError as exc:
        if exc.status_code == 429:
            from app.core.exceptions import ProxyRateLimitError

            raise ProxyRateLimitError(exc.message) from exc
        if exc.status_code in (401, 403):
            raise ProxyAuthError("Invalid ChatGPT token or chatgpt-account-id") from exc
        raise ProxyUpstreamError("Unable to validate ChatGPT credentials at this time") from exc
    return None


def _extract_bearer_token(authorization: str | None) -> str | None:
    if authorization is None:
        return None
    prefix = "bearer "
    value = authorization.strip()
    if not value.lower().startswith(prefix):
        return None
    token = value[len(prefix) :].strip()
    if not token:
        return None
    return token

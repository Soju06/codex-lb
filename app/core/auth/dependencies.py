from __future__ import annotations

import hashlib
import logging

from fastapi import Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.requests import HTTPConnection

from app.core.auth.api_key_cache import get_api_key_cache
from app.core.auth.dashboard_mode import DashboardAuthMode, get_dashboard_request_auth
from app.core.clients.usage import UsageFetchError, fetch_usage
from app.core.config.settings_cache import get_settings_cache
from app.core.exceptions import DashboardAuthError, ProxyAuthError, ProxyUpstreamError
from app.core.request_locality import is_local_request
from app.core.utils.time import utcnow
from app.db.session import get_background_session
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyData, ApiKeyInvalidError, ApiKeysService
from app.modules.dashboard_auth.service import DASHBOARD_SESSION_COOKIE, get_dashboard_session_store

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(description="API key (e.g. sk-clb-…)", auto_error=False)


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
        if request is not None and not is_local_request(request):
            raise ProxyAuthError("Proxy authentication must be configured before remote access is allowed")
        return None

    token = _extract_bearer_token(authorization)
    if not token:
        raise ProxyAuthError("Missing API key in Authorization header")

    return await _validate_api_key_token(token)


async def _validate_api_key_token(token: str) -> ApiKeyData:
    """Validate a plain API key token and return the typed key data."""

    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    cache = get_api_key_cache()
    cached = await cache.get(token_hash)
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
    request_auth = get_dashboard_request_auth(request)
    if request_auth is not None:
        return

    settings = await get_settings_cache().get()
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
    from app.core.config.settings import get_settings

    return get_settings().dashboard_auth_mode


# --- Codex usage caller identity auth ---


async def validate_codex_usage_identity(request: Request) -> None:
    token = _extract_bearer_token(request.headers.get("Authorization"))
    if not token:
        raise ProxyAuthError("Missing ChatGPT token in Authorization header")

    raw_account_id = request.headers.get("chatgpt-account-id")
    account_id = raw_account_id.strip() if raw_account_id else ""
    if not account_id:
        raise ProxyAuthError("Missing chatgpt-account-id header")

    async with get_background_session() as session:
        accounts_repo = AccountsRepository(session)
        is_authorized = await accounts_repo.exists_active_chatgpt_account_id(account_id)
    if not is_authorized:
        raise ProxyAuthError("Unknown or inactive chatgpt-account-id")

    try:
        await fetch_usage(access_token=token, account_id=account_id)
    except UsageFetchError as exc:
        if exc.status_code == 429:
            from app.core.exceptions import ProxyRateLimitError

            raise ProxyRateLimitError(exc.message) from exc
        if exc.status_code in (401, 403):
            raise ProxyAuthError("Invalid ChatGPT token or chatgpt-account-id") from exc
        raise ProxyUpstreamError("Unable to validate ChatGPT credentials at this time") from exc


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

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.core.clients.usage import UsageFetchError, fetch_usage
from app.core.config.settings_cache import get_settings_cache
from app.core.errors import dashboard_error, openai_error
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyInvalidError, ApiKeyRateLimitExceededError, ApiKeysService
from app.modules.dashboard_auth.service import DASHBOARD_SESSION_COOKIE, get_dashboard_session_store

PUBLIC_PATHS = {"/health"}
PUBLIC_PREFIXES = ("/api/dashboard-auth/",)
PROXY_PREFIXES = ("/v1/", "/backend-api/codex/")
CODEX_USAGE_PATH = "/api/codex/usage"


def add_auth_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def dashboard_auth_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if path in PUBLIC_PATHS or any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
            return await call_next(request)

        if path == CODEX_USAGE_PATH:
            blocked = await _validate_codex_usage_caller_identity(request)
            if blocked is not None:
                return blocked
            return await call_next(request)

        if any(path.startswith(prefix) for prefix in PROXY_PREFIXES):
            blocked = await _validate_proxy_api_key(request)
            if blocked is not None:
                return blocked
            return await call_next(request)

        if path.startswith("/api/"):
            blocked = await _validate_dashboard_session(request)
            if blocked is not None:
                return blocked
            return await call_next(request)

        return await call_next(request)


async def _validate_dashboard_session(request: Request) -> JSONResponse | None:
    settings = await get_settings_cache().get()
    if settings.password_hash is None:
        return None

    session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    state = get_dashboard_session_store().get(session_id)
    if state is None or not state.password_verified:
        return JSONResponse(
            status_code=401,
            content=dashboard_error("authentication_required", "Authentication is required"),
        )
    if settings.totp_required_on_login and not state.totp_verified:
        return JSONResponse(
            status_code=401,
            content=dashboard_error("totp_required", "TOTP verification is required for dashboard access"),
        )
    return None


async def _validate_codex_usage_caller_identity(request: Request) -> JSONResponse | None:
    token = _extract_bearer_token(request.headers.get("Authorization"))
    if not token:
        return _invalid_codex_usage_identity("Missing ChatGPT token in Authorization header")

    raw_account_id = request.headers.get("chatgpt-account-id")
    account_id = raw_account_id.strip() if raw_account_id else ""
    if not account_id:
        return _invalid_codex_usage_identity("Missing chatgpt-account-id header")

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        is_authorized = await accounts_repo.exists_active_chatgpt_account_id(account_id)
    if not is_authorized:
        return _invalid_codex_usage_identity("Unknown or inactive chatgpt-account-id")

    try:
        await fetch_usage(access_token=token, account_id=account_id)
    except UsageFetchError as exc:
        if exc.status_code == 429:
            return JSONResponse(
                status_code=429,
                content=openai_error("rate_limit_exceeded", exc.message, error_type="rate_limit_error"),
            )
        if exc.status_code in (401, 403):
            return _invalid_codex_usage_identity("Invalid ChatGPT token or chatgpt-account-id")
        return JSONResponse(
            status_code=503,
            content=openai_error(
                "upstream_error",
                "Unable to validate ChatGPT credentials at this time",
                error_type="server_error",
            ),
        )

    return None


def _invalid_codex_usage_identity(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content=openai_error("invalid_api_key", message, error_type="authentication_error"),
    )


async def _validate_proxy_api_key(request: Request) -> JSONResponse | None:
    settings = await get_settings_cache().get()
    if not settings.api_key_auth_enabled:
        return None

    token = _extract_bearer_token(request.headers.get("Authorization"))
    if not token:
        return JSONResponse(
            status_code=401,
            content=openai_error(
                "invalid_api_key",
                "Missing API key in Authorization header",
                error_type="authentication_error",
            ),
        )

    async with SessionLocal() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        try:
            api_key = await service.validate_key(token)
        except ApiKeyRateLimitExceededError as exc:
            message = f"API key weekly token limit exceeded. Usage resets at {exc.weekly_reset_at.isoformat()}Z."
            return JSONResponse(
                status_code=429,
                content=openai_error("rate_limit_exceeded", message, error_type="rate_limit_error"),
            )
        except ApiKeyInvalidError as exc:
            return JSONResponse(
                status_code=401,
                content=openai_error("invalid_api_key", str(exc), error_type="authentication_error"),
            )

    request.state.api_key = api_key
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

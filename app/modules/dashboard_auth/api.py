from __future__ import annotations

import logging

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

from app.core.auth.dashboard_mode import (
    DashboardAuthMode,
    get_dashboard_request_auth,
    password_management_enabled,
)
from app.core.auth.dependencies import set_dashboard_error_format
from app.core.bootstrap import (
    ensure_auto_bootstrap_token,
    get_bootstrap_validation_status,
    has_active_bootstrap_token,
    log_bootstrap_token,
)
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.exceptions import (
    DashboardAuthError,
    DashboardBadRequestError,
    DashboardConflictError,
    DashboardRateLimitError,
    DashboardValidationError,
)
from app.core.request_locality import is_local_request
from app.dependencies import DashboardAuthContext, get_dashboard_auth_context
from app.modules.dashboard_auth.schemas import (
    DashboardAuthSessionResponse,
    PasswordChangeRequest,
    PasswordLoginRequest,
    PasswordRemoveRequest,
    PasswordSetupRequest,
    TotpSetupConfirmRequest,
    TotpSetupStartResponse,
    TotpVerifyRequest,
)
from app.modules.dashboard_auth.service import (
    DASHBOARD_SESSION_COOKIE,
    InvalidCredentialsError,
    PasswordAlreadyConfiguredError,
    PasswordNotConfiguredError,
    PasswordSessionRequiredError,
    TotpAlreadyConfiguredError,
    TotpInvalidCodeError,
    TotpInvalidSetupError,
    TotpNotConfiguredError,
    get_dashboard_session_store,
    get_password_rate_limiter,
    get_totp_rate_limiter,
)

router = APIRouter(
    prefix="/api/dashboard-auth",
    tags=["dashboard"],
    dependencies=[Depends(set_dashboard_error_format)],
)

logger = logging.getLogger(__name__)


def _session_client_key(request: Request, *, prefix: str) -> str:
    return f"{prefix}:{request.client.host if request.client else 'unknown'}"


async def _create_dashboard_session(*, password_verified: bool, totp_verified: bool) -> tuple[str, int]:
    settings = await get_settings_cache().get()
    ttl_seconds = settings.dashboard_session_ttl_seconds
    session_id = get_dashboard_session_store().create(
        password_verified=password_verified,
        totp_verified=totp_verified,
        ttl_seconds=ttl_seconds,
    )
    return session_id, ttl_seconds


def _decorate_session_response(
    response: DashboardAuthSessionResponse,
    *,
    request: Request,
    force_authenticated: bool = False,
    password_session_id: str | None = None,
) -> DashboardAuthSessionResponse:
    request_auth = get_dashboard_request_auth(request)
    auth_mode = get_settings().dashboard_auth_mode
    store = get_dashboard_session_store()
    sid = password_session_id or request.cookies.get(DASHBOARD_SESSION_COOKIE)
    session_state = store.get(sid) if sid else None
    has_pwd = session_state is not None and session_state.password_verified
    totp_pending = (
        has_pwd and session_state is not None and response.totp_required_on_login and not session_state.totp_verified
    )
    fully_authorized = has_pwd and not totp_pending and response.password_required

    if request_auth is None:
        update: dict[str, object] = {
            "auth_mode": auth_mode,
            "password_management_enabled": password_management_enabled(auth_mode),
            "password_session_active": fully_authorized,
        }
        if (
            auth_mode == DashboardAuthMode.TRUSTED_HEADER
            and not response.password_required
            and not response.totp_required_on_login
        ):
            update["authenticated"] = False
        return response.model_copy(update=update)

    return response.model_copy(
        update={
            "authenticated": force_authenticated or response.authenticated,
            "totp_required_on_login": totp_pending,
            "auth_mode": request_auth.mode,
            "password_management_enabled": password_management_enabled(request_auth.mode),
            "password_session_active": fully_authorized,
        }
    )


async def _has_active_password_session(request: Request, context: DashboardAuthContext) -> bool:
    settings = await context.repository.get_settings()
    if settings.password_hash is None:
        return False
    session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    return get_dashboard_session_store().is_password_verified(session_id)


async def _validate_password_management_session(request: Request) -> None:
    _ensure_password_management_enabled(request)

    session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    session_state = get_dashboard_session_store().get(session_id)
    if session_state is None or not session_state.password_verified:
        raise DashboardAuthError("Authentication is required")

    settings = await get_settings_cache().get()
    if settings.totp_required_on_login and not session_state.totp_verified:
        raise DashboardAuthError(
            "TOTP verification is required for dashboard access",
            code="totp_required",
        )


def _ensure_password_management_enabled(request: Request) -> None:
    request_auth = get_dashboard_request_auth(request)
    if request_auth is not None and not password_management_enabled(request_auth.mode):
        raise DashboardBadRequestError(
            "Password and TOTP management is disabled while dashboard auth is bypassed",
            code="password_management_disabled",
        )


# bcrypt enforces a hard 72-byte limit on the input password; anything longer
# raises ``ValueError`` from ``bcrypt.hashpw`` and surfaces as a 500 to the
# client. Validate the encoded length here so the API returns a clear 400.
_MAX_PASSWORD_BYTES = 72


def _validate_password_length(password: str) -> None:
    if len(password) < 8:
        raise DashboardValidationError("Password must be at least 8 characters")
    if len(password.encode("utf-8")) > _MAX_PASSWORD_BYTES:
        raise DashboardValidationError(
            f"Password must be at most {_MAX_PASSWORD_BYTES} bytes when encoded as UTF-8. "
            "Note that multi-byte characters (e.g. emoji, non-ASCII letters) count for more than one byte.",
            code="password_too_long",
        )


@router.get("/session", response_model=DashboardAuthSessionResponse)
async def get_dashboard_auth_session(
    request: Request,
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> DashboardAuthSessionResponse:
    session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    response = await context.service.get_session_state(session_id)
    decorated = _decorate_session_response(response, request=request, force_authenticated=True)
    if decorated.auth_mode != DashboardAuthMode.STANDARD:
        return decorated
    if decorated.password_required or is_local_request(request):
        return decorated
    bootstrap_token_configured = await has_active_bootstrap_token()
    return decorated.model_copy(
        update={
            "authenticated": False,
            "bootstrap_required": True,
            "bootstrap_token_configured": bootstrap_token_configured,
        }
    )


@router.post("/password/setup", response_model=DashboardAuthSessionResponse)
async def setup_password(
    request: Request,
    payload: PasswordSetupRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> DashboardAuthSessionResponse | JSONResponse:
    settings = get_settings()
    request_auth = get_dashboard_request_auth(request)
    current_settings = await context.repository.get_settings()
    if settings.dashboard_auth_mode == DashboardAuthMode.DISABLED:
        raise DashboardBadRequestError(
            "Password management is disabled while dashboard auth is bypassed",
            code="password_management_disabled",
        )
    if (
        settings.dashboard_auth_mode == DashboardAuthMode.TRUSTED_HEADER
        and request_auth is None
        and current_settings.password_hash is None
    ):
        raise DashboardAuthError("Reverse proxy authentication is required", code="proxy_auth_required")
    if (
        current_settings.password_hash is None
        and settings.dashboard_auth_mode != DashboardAuthMode.TRUSTED_HEADER
        and not is_local_request(request)
    ):
        submitted_bootstrap_token = (payload.bootstrap_token or "").strip()
        validation_status = await get_bootstrap_validation_status(submitted_bootstrap_token)
        if validation_status == "unavailable":
            raise DashboardAuthError(
                "Remote bootstrap is disabled until CODEX_LB_DASHBOARD_BOOTSTRAP_TOKEN is configured.",
                code="bootstrap_unavailable",
            )
        if validation_status == "password_already_configured":
            raise DashboardConflictError("Password is already configured", code="password_already_configured")
        if validation_status != "valid":
            raise DashboardAuthError("Invalid dashboard bootstrap token.", code="invalid_bootstrap_token")
    password = payload.password.strip()
    _validate_password_length(password)
    try:
        await context.service.setup_password(password)
    except PasswordAlreadyConfiguredError as exc:
        raise DashboardConflictError(str(exc), code="password_already_configured") from exc

    await get_settings_cache().invalidate()
    session_id, session_ttl_seconds = await _create_dashboard_session(password_verified=True, totp_verified=False)
    response = _decorate_session_response(
        await context.service.get_session_state(session_id),
        request=request,
        password_session_id=session_id,
    )
    json_response = JSONResponse(status_code=200, content=response.model_dump(by_alias=True))
    _set_session_cookie(json_response, session_id, request, max_age_seconds=session_ttl_seconds)
    return json_response


@router.post("/password/login", response_model=DashboardAuthSessionResponse)
async def login_password(
    request: Request,
    payload: PasswordLoginRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> DashboardAuthSessionResponse | JSONResponse:
    if get_settings().dashboard_auth_mode == DashboardAuthMode.DISABLED:
        raise DashboardBadRequestError(
            "Password login is disabled while dashboard auth is bypassed",
            code="password_management_disabled",
        )

    settings = await get_settings_cache().get()
    if settings.password_hash is None:
        raise DashboardBadRequestError("Password is not configured", code="password_not_configured")

    limiter = get_password_rate_limiter()
    rate_key = _session_client_key(request, prefix="password_login")
    try:
        await limiter.check_and_increment(rate_key, context.session)
    except DashboardRateLimitError as exc:
        raise DashboardRateLimitError(
            f"Too many attempts. Try again in {exc.retry_after} seconds.",
            retry_after=exc.retry_after,
            code="password_rate_limited",
        ) from exc

    try:
        await context.service.verify_password(
            payload.password, actor_ip=request.client.host if request.client else None
        )
    except InvalidCredentialsError as exc:
        raise DashboardAuthError(str(exc), code="invalid_credentials") from exc
    except PasswordNotConfiguredError as exc:
        await limiter.clear_for_key(rate_key, context.session)
        raise DashboardBadRequestError(str(exc), code="password_not_configured") from exc

    await limiter.clear_for_key(rate_key, context.session)

    session_id, session_ttl_seconds = await _create_dashboard_session(password_verified=True, totp_verified=False)
    response = _decorate_session_response(
        await context.service.get_session_state(session_id),
        request=request,
        password_session_id=session_id,
    )
    json_response = JSONResponse(status_code=200, content=response.model_dump(by_alias=True))
    _set_session_cookie(json_response, session_id, request, max_age_seconds=session_ttl_seconds)
    return json_response


@router.post("/password/change")
async def change_password(
    request: Request,
    payload: PasswordChangeRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> JSONResponse:
    await _validate_password_management_session(request)

    new_password = payload.new_password.strip()
    _validate_password_length(new_password)

    try:
        await context.service.change_password(payload.current_password, new_password)
    except PasswordNotConfiguredError as exc:
        raise DashboardBadRequestError(str(exc), code="password_not_configured") from exc
    except InvalidCredentialsError as exc:
        raise DashboardAuthError(str(exc), code="invalid_credentials") from exc

    await get_settings_cache().invalidate()
    return JSONResponse(status_code=200, content={"status": "ok"})


@router.delete("/password")
async def remove_password(
    request: Request,
    payload: PasswordRemoveRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> JSONResponse:
    await _validate_password_management_session(request)

    try:
        await context.service.remove_password(payload.password)
    except PasswordNotConfiguredError as exc:
        raise DashboardBadRequestError(str(exc), code="password_not_configured") from exc
    except InvalidCredentialsError as exc:
        raise DashboardAuthError(str(exc), code="invalid_credentials") from exc

    await get_settings_cache().invalidate()
    bootstrap_token = await ensure_auto_bootstrap_token()
    if bootstrap_token:
        log_bootstrap_token(logger, bootstrap_token, reason="password-removed")
    response = JSONResponse(status_code=200, content={"status": "ok"})
    response.delete_cookie(key=DASHBOARD_SESSION_COOKIE, path="/")
    return response


@router.post("/totp/setup/start", response_model=TotpSetupStartResponse)
async def start_totp_setup(
    request: Request,
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> TotpSetupStartResponse:
    _ensure_password_management_enabled(request)
    if not await _has_active_password_session(request, context):
        raise DashboardAuthError("Authentication is required")
    session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    try:
        return await context.service.start_totp_setup(session_id=session_id)
    except PasswordSessionRequiredError as exc:
        raise DashboardAuthError(str(exc)) from exc
    except TotpAlreadyConfiguredError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_setup") from exc


@router.post("/totp/setup/confirm")
async def confirm_totp_setup(
    request: Request,
    payload: TotpSetupConfirmRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> JSONResponse:
    _ensure_password_management_enabled(request)
    if not await _has_active_password_session(request, context):
        raise DashboardAuthError("Authentication is required")

    limiter = get_totp_rate_limiter()
    rate_key = _session_client_key(request, prefix="totp_setup_confirm")
    try:
        await limiter.check_and_increment(rate_key, context.session)
    except DashboardRateLimitError as exc:
        raise DashboardRateLimitError(
            f"Too many attempts. Try again in {exc.retry_after} seconds.",
            retry_after=exc.retry_after,
            code="totp_rate_limited",
        ) from exc

    try:
        session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
        await context.service.confirm_totp_setup(
            session_id=session_id,
            secret=payload.secret,
            code=payload.code,
            actor_ip=request.client.host if request.client else None,
        )
    except PasswordSessionRequiredError as exc:
        raise DashboardAuthError(str(exc)) from exc
    except TotpInvalidCodeError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_code") from exc
    except TotpInvalidSetupError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_setup") from exc
    except TotpAlreadyConfiguredError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_setup") from exc

    await limiter.clear_for_key(rate_key, context.session)
    await get_settings_cache().invalidate()
    return JSONResponse(status_code=200, content={"status": "ok"})


@router.post("/totp/verify", response_model=DashboardAuthSessionResponse)
async def verify_totp(
    request: Request,
    payload: TotpVerifyRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> DashboardAuthSessionResponse | JSONResponse:
    _ensure_password_management_enabled(request)
    limiter = get_totp_rate_limiter()
    rate_key = _session_client_key(request, prefix="totp_verify")
    current_session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    try:
        await context.service.ensure_active_password_session(current_session_id)
    except PasswordSessionRequiredError as exc:
        raise DashboardAuthError(str(exc)) from exc
    try:
        await limiter.check_and_increment(rate_key, context.session)
    except DashboardRateLimitError as exc:
        raise DashboardRateLimitError(
            f"Too many attempts. Try again in {exc.retry_after} seconds.",
            retry_after=exc.retry_after,
            code="totp_rate_limited",
        ) from exc
    try:
        session_ttl_seconds = (await get_settings_cache().get()).dashboard_session_ttl_seconds
        session_id, applied_ttl_seconds = await context.service.verify_totp(
            session_id=current_session_id,
            code=payload.code,
            ttl_seconds=session_ttl_seconds,
            actor_ip=request.client.host if request.client else None,
        )
    except PasswordSessionRequiredError as exc:
        raise DashboardAuthError(str(exc)) from exc
    except TotpInvalidCodeError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_code") from exc
    except TotpNotConfiguredError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_code") from exc

    await limiter.clear_for_key(rate_key, context.session)
    response = _decorate_session_response(
        await context.service.get_session_state(session_id),
        request=request,
        password_session_id=session_id,
    )
    json_response = JSONResponse(status_code=200, content=response.model_dump(by_alias=True))
    _set_session_cookie(json_response, session_id, request, max_age_seconds=applied_ttl_seconds)
    return json_response


@router.post("/totp/disable")
async def disable_totp(
    request: Request,
    payload: TotpVerifyRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> JSONResponse:
    _ensure_password_management_enabled(request)
    limiter = get_totp_rate_limiter()
    rate_key = _session_client_key(request, prefix="totp_disable")
    session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    try:
        await context.service.ensure_totp_verified_session(session_id)
    except PasswordSessionRequiredError as exc:
        raise DashboardAuthError(str(exc)) from exc
    try:
        await limiter.check_and_increment(rate_key, context.session)
    except DashboardRateLimitError as exc:
        raise DashboardRateLimitError(
            f"Too many attempts. Try again in {exc.retry_after} seconds.",
            retry_after=exc.retry_after,
            code="totp_rate_limited",
        ) from exc
    try:
        await context.service.disable_totp(
            session_id=session_id,
            code=payload.code,
            actor_ip=request.client.host if request.client else None,
        )
    except PasswordSessionRequiredError as exc:
        raise DashboardAuthError(str(exc)) from exc
    except TotpInvalidCodeError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_code") from exc
    except TotpNotConfiguredError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_code") from exc

    await limiter.clear_for_key(rate_key, context.session)
    await get_settings_cache().invalidate()
    return JSONResponse(status_code=200, content={"status": "ok"})


@router.post("/logout")
async def logout_dashboard(
    request: Request,
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> JSONResponse:
    session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    context.service.logout(session_id)
    response = JSONResponse(status_code=200, content={"status": "ok"})
    response.delete_cookie(key=DASHBOARD_SESSION_COOKIE, path="/")
    return response


def _set_session_cookie(response: JSONResponse, session_id: str, request: Request, *, max_age_seconds: int) -> None:
    response.set_cookie(
        key=DASHBOARD_SESSION_COOKIE,
        value=session_id,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=max_age_seconds,
        path="/",
    )

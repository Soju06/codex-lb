from __future__ import annotations

import logging
from secrets import compare_digest

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

from app.core.auth.dependencies import set_dashboard_error_format
from app.core.bootstrap import (
    ensure_auto_bootstrap_token,
    get_bootstrap_validation_status,
    has_active_bootstrap_token,
    log_bootstrap_token,
)
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


async def _has_active_password_session(request: Request, context: DashboardAuthContext) -> bool:
    settings = await context.repository.get_settings()
    if settings.password_hash is None:
        return False
    session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    return get_dashboard_session_store().is_password_verified(session_id)


async def _validate_password_management_session(request: Request) -> None:
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


@router.get("/session", response_model=DashboardAuthSessionResponse)
async def get_dashboard_auth_session(
    request: Request,
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> DashboardAuthSessionResponse:
    session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    response = await context.service.get_session_state(session_id)
    if response.password_required or is_local_request(request):
        return response
    bootstrap_token_configured = await has_active_bootstrap_token()
    return response.model_copy(
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
    current_settings = await context.repository.get_settings()
    if current_settings.password_hash is None and not is_local_request(request):
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
    if len(password) < 8:
        raise DashboardValidationError("Password must be at least 8 characters")
    try:
        await context.service.setup_password(password)
    except PasswordAlreadyConfiguredError as exc:
        raise DashboardConflictError(str(exc), code="password_already_configured") from exc

    await get_settings_cache().invalidate()
    session_id = get_dashboard_session_store().create(password_verified=True, totp_verified=False)
    response = await context.service.get_session_state(session_id)
    json_response = JSONResponse(status_code=200, content=response.model_dump(by_alias=True))
    _set_session_cookie(json_response, session_id, request)
    return json_response


@router.post("/password/login", response_model=DashboardAuthSessionResponse)
async def login_password(
    request: Request,
    payload: PasswordLoginRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> DashboardAuthSessionResponse | JSONResponse:
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

    session_id = get_dashboard_session_store().create(password_verified=True, totp_verified=False)
    response = await context.service.get_session_state(session_id)
    json_response = JSONResponse(status_code=200, content=response.model_dump(by_alias=True))
    _set_session_cookie(json_response, session_id, request)
    return json_response


@router.post("/password/change")
async def change_password(
    request: Request,
    payload: PasswordChangeRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> JSONResponse:
    await _validate_password_management_session(request)

    new_password = payload.new_password.strip()
    if len(new_password) < 8:
        raise DashboardValidationError("Password must be at least 8 characters")

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
        session_id = await context.service.verify_totp(
            session_id=current_session_id,
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
    response = await context.service.get_session_state(session_id)
    json_response = JSONResponse(status_code=200, content=response.model_dump(by_alias=True))
    _set_session_cookie(json_response, session_id, request)
    return json_response


@router.post("/totp/disable")
async def disable_totp(
    request: Request,
    payload: TotpVerifyRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> JSONResponse:
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


def _set_session_cookie(response: JSONResponse, session_id: str, request: Request) -> None:
    response.set_cookie(
        key=DASHBOARD_SESSION_COOKIE,
        value=session_id,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        max_age=12 * 60 * 60,
        path="/",
    )

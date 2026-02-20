from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

from app.core.auth.dependencies import set_dashboard_error_format
from app.core.config.settings_cache import get_settings_cache
from app.core.exceptions import (
    DashboardAuthError,
    DashboardBadRequestError,
    DashboardConflictError,
    DashboardRateLimitError,
    DashboardValidationError,
)
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
    return await context.service.get_session_state(session_id)


@router.post("/password/setup", response_model=DashboardAuthSessionResponse)
async def setup_password(
    request: Request,
    payload: PasswordSetupRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> DashboardAuthSessionResponse | JSONResponse:
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
    limiter = get_password_rate_limiter()
    rate_key = _session_client_key(request, prefix="password_login")
    retry_after = limiter.check(rate_key)
    if retry_after is not None:
        raise DashboardRateLimitError(
            f"Too many attempts. Try again in {retry_after} seconds.",
            retry_after=retry_after,
            code="password_rate_limited",
        )

    try:
        await context.service.verify_password(payload.password)
        limiter.reset(rate_key)
    except InvalidCredentialsError as exc:
        limiter.record_failure(rate_key)
        raise DashboardAuthError(str(exc), code="invalid_credentials") from exc
    except PasswordNotConfiguredError as exc:
        raise DashboardBadRequestError(str(exc), code="password_not_configured") from exc

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
    retry_after = limiter.check(rate_key)
    if retry_after is not None:
        raise DashboardRateLimitError(
            f"Too many attempts. Try again in {retry_after} seconds.",
            retry_after=retry_after,
            code="totp_rate_limited",
        )

    try:
        session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
        await context.service.confirm_totp_setup(session_id=session_id, secret=payload.secret, code=payload.code)
        limiter.reset(rate_key)
    except PasswordSessionRequiredError as exc:
        raise DashboardAuthError(str(exc)) from exc
    except TotpInvalidCodeError as exc:
        limiter.record_failure(rate_key)
        raise DashboardBadRequestError(str(exc), code="invalid_totp_code") from exc
    except TotpInvalidSetupError as exc:
        limiter.record_failure(rate_key)
        raise DashboardBadRequestError(str(exc), code="invalid_totp_setup") from exc
    except TotpAlreadyConfiguredError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_setup") from exc

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
    retry_after = limiter.check(rate_key)
    if retry_after is not None:
        raise DashboardRateLimitError(
            f"Too many attempts. Try again in {retry_after} seconds.",
            retry_after=retry_after,
            code="totp_rate_limited",
        )
    try:
        current_session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
        session_id = await context.service.verify_totp(session_id=current_session_id, code=payload.code)
        limiter.reset(rate_key)
    except PasswordSessionRequiredError as exc:
        raise DashboardAuthError(str(exc)) from exc
    except TotpInvalidCodeError as exc:
        limiter.record_failure(rate_key)
        raise DashboardBadRequestError(str(exc), code="invalid_totp_code") from exc
    except TotpNotConfiguredError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_code") from exc

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
    retry_after = limiter.check(rate_key)
    if retry_after is not None:
        raise DashboardRateLimitError(
            f"Too many attempts. Try again in {retry_after} seconds.",
            retry_after=retry_after,
            code="totp_rate_limited",
        )
    try:
        session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
        await context.service.disable_totp(session_id=session_id, code=payload.code)
        limiter.reset(rate_key)
    except PasswordSessionRequiredError as exc:
        raise DashboardAuthError(str(exc)) from exc
    except TotpInvalidCodeError as exc:
        limiter.record_failure(rate_key)
        raise DashboardBadRequestError(str(exc), code="invalid_totp_code") from exc
    except TotpNotConfiguredError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_code") from exc

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

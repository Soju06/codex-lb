from __future__ import annotations

import hmac
import ipaddress

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

from app.core.config.settings import get_settings
from app.core.errors import dashboard_error
from app.dependencies import DashboardAuthContext, get_dashboard_auth_context
from app.modules.dashboard_auth.schemas import (
    DashboardAuthSessionResponse,
    TotpSetupConfirmRequest,
    TotpSetupStartResponse,
    TotpVerifyRequest,
)
from app.modules.dashboard_auth.service import (
    DASHBOARD_SESSION_COOKIE,
    TotpAlreadyConfiguredError,
    TotpInvalidCodeError,
    TotpNotConfiguredError,
    get_dashboard_session_store,
    get_totp_rate_limiter,
)

router = APIRouter(prefix="/api/dashboard-auth", tags=["dashboard"])

_SETUP_TOKEN_HEADER = "X-Codex-LB-Setup-Token"


def _is_loopback_host(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def _require_setup_access(request: Request) -> JSONResponse | None:
    client_host = request.client.host if request.client else ""
    if client_host and _is_loopback_host(client_host):
        return None

    token = get_settings().dashboard_setup_token
    if not token:
        return JSONResponse(
            status_code=403,
            content=dashboard_error(
                "dashboard_setup_token_required",
                "Remote dashboard setup is disabled. Set CODEX_LB_DASHBOARD_SETUP_TOKEN to enable it.",
            ),
        )

    provided = request.headers.get(_SETUP_TOKEN_HEADER, "")
    if not provided or not hmac.compare_digest(provided, token):
        return JSONResponse(
            status_code=403,
            content=dashboard_error(
                "dashboard_setup_forbidden",
                "Invalid dashboard setup token.",
            ),
        )
    return None


@router.get("/session", response_model=DashboardAuthSessionResponse)
async def get_dashboard_auth_session(
    request: Request,
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> DashboardAuthSessionResponse:
    session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    return await context.service.get_session_state(session_id)


@router.post("/totp/setup/start", response_model=TotpSetupStartResponse)
async def start_totp_setup(
    request: Request,
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> TotpSetupStartResponse | JSONResponse:
    denied = _require_setup_access(request)
    if denied is not None:
        return denied
    try:
        return await context.service.start_totp_setup()
    except TotpAlreadyConfiguredError as exc:
        return JSONResponse(
            status_code=400,
            content=dashboard_error("invalid_totp_setup", str(exc)),
        )


@router.post("/totp/setup/confirm")
async def confirm_totp_setup(
    request: Request,
    payload: TotpSetupConfirmRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> JSONResponse:
    denied = _require_setup_access(request)
    if denied is not None:
        return denied

    limiter = get_totp_rate_limiter()
    rate_key = f"totp_setup_confirm:{request.client.host if request.client else 'unknown'}"
    retry_after = limiter.check(rate_key)
    if retry_after is not None:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry_after)},
            content=dashboard_error(
                "totp_rate_limited",
                f"Too many attempts. Try again in {retry_after} seconds.",
            ),
        )

    try:
        await context.service.confirm_totp_setup(payload.secret, payload.code)
        limiter.reset(rate_key)
    except TotpInvalidCodeError as exc:
        limiter.record_failure(rate_key)
        return JSONResponse(
            status_code=400,
            content=dashboard_error("invalid_totp_code", str(exc)),
        )
    except TotpAlreadyConfiguredError as exc:
        return JSONResponse(
            status_code=400,
            content=dashboard_error("invalid_totp_code", str(exc)),
        )
    return JSONResponse(status_code=200, content={"status": "ok"})


@router.post("/totp/verify", response_model=DashboardAuthSessionResponse)
async def verify_totp(
    request: Request,
    payload: TotpVerifyRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> DashboardAuthSessionResponse | JSONResponse:
    limiter = get_totp_rate_limiter()
    rate_key = f"totp_verify:{request.client.host if request.client else 'unknown'}"
    retry_after = limiter.check(rate_key)
    if retry_after is not None:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry_after)},
            content=dashboard_error(
                "totp_rate_limited",
                f"Too many attempts. Try again in {retry_after} seconds.",
            ),
        )
    try:
        session_id = await context.service.verify_totp(payload.code)
        limiter.reset(rate_key)
    except TotpInvalidCodeError as exc:
        limiter.record_failure(rate_key)
        return JSONResponse(
            status_code=400,
            content=dashboard_error("invalid_totp_code", str(exc)),
        )
    except TotpNotConfiguredError as exc:
        return JSONResponse(
            status_code=400,
            content=dashboard_error("invalid_totp_code", str(exc)),
        )

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
    session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    if not get_dashboard_session_store().is_totp_verified(session_id):
        return JSONResponse(
            status_code=401,
            content=dashboard_error("totp_required", "TOTP verification is required to perform this action"),
        )

    limiter = get_totp_rate_limiter()
    rate_key = f"totp_disable:{request.client.host if request.client else 'unknown'}"
    retry_after = limiter.check(rate_key)
    if retry_after is not None:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry_after)},
            content=dashboard_error(
                "totp_rate_limited",
                f"Too many attempts. Try again in {retry_after} seconds.",
            ),
        )
    try:
        await context.service.disable_totp(payload.code)
        limiter.reset(rate_key)
    except TotpInvalidCodeError as exc:
        limiter.record_failure(rate_key)
        return JSONResponse(
            status_code=400,
            content=dashboard_error("invalid_totp_code", str(exc)),
        )
    except TotpNotConfiguredError as exc:
        return JSONResponse(
            status_code=400,
            content=dashboard_error("invalid_totp_code", str(exc)),
        )
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

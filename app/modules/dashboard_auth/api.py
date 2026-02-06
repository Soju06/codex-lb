from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

from app.core.errors import dashboard_error
from app.dependencies import DashboardAuthContext, get_dashboard_auth_context
from app.modules.dashboard_auth.schemas import (
    DashboardAuthSessionResponse,
    TotpSetupConfirmRequest,
    TotpSetupStartResponse,
    TotpVerifyRequest,
)
from app.modules.dashboard_auth.service import DASHBOARD_SESSION_COOKIE

router = APIRouter(prefix="/api/dashboard-auth", tags=["dashboard"])


@router.get("/session", response_model=DashboardAuthSessionResponse)
async def get_dashboard_auth_session(
    request: Request,
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> DashboardAuthSessionResponse:
    session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
    return await context.service.get_session_state(session_id)


@router.post("/totp/setup/start", response_model=TotpSetupStartResponse)
async def start_totp_setup(
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> TotpSetupStartResponse | JSONResponse:
    try:
        return await context.service.start_totp_setup()
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content=dashboard_error("invalid_totp_setup", str(exc)),
        )


@router.post("/totp/setup/confirm")
async def confirm_totp_setup(
    payload: TotpSetupConfirmRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> JSONResponse:
    try:
        await context.service.confirm_totp_setup(payload.secret, payload.code)
    except ValueError as exc:
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
    try:
        session_id = await context.service.verify_totp(payload.code)
    except ValueError as exc:
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
    payload: TotpVerifyRequest = Body(...),
    context: DashboardAuthContext = Depends(get_dashboard_auth_context),
) -> JSONResponse:
    try:
        await context.service.disable_totp(payload.code)
    except ValueError as exc:
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

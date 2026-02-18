from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.core.clients.oauth import OAuthError
from app.core.errors import dashboard_error
from app.dependencies import OauthContext, get_oauth_context
from app.modules.oauth.schemas import (
    OauthCompleteRequest,
    OauthCompleteResponse,
    OauthStartRequest,
    OauthStartResponse,
    OauthStatusResponse,
)

router = APIRouter(
    prefix="/api/oauth",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.post("/start", response_model=OauthStartResponse)
async def start_oauth(
    request: OauthStartRequest,
    context: OauthContext = Depends(get_oauth_context),
) -> OauthStartResponse | JSONResponse:
    try:
        return await context.service.start_oauth(request)
    except OAuthError as exc:
        return JSONResponse(
            status_code=502,
            content=dashboard_error(exc.code, exc.message),
        )
    except NotImplementedError:
        return JSONResponse(
            status_code=501,
            content=dashboard_error("not_implemented", "OAuth start is not implemented"),
        )


@router.get("/status", response_model=OauthStatusResponse)
async def oauth_status(
    context: OauthContext = Depends(get_oauth_context),
) -> OauthStatusResponse | JSONResponse:
    return await context.service.oauth_status()


@router.post("/complete", response_model=OauthCompleteResponse)
async def complete_oauth(
    request: OauthCompleteRequest | None = Body(default=None),
    context: OauthContext = Depends(get_oauth_context),
) -> OauthCompleteResponse | JSONResponse:
    try:
        return await context.service.complete_oauth(request)
    except NotImplementedError:
        return JSONResponse(
            status_code=501,
            content=dashboard_error("not_implemented", "OAuth complete is not implemented"),
        )

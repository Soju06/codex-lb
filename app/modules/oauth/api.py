from __future__ import annotations

import hashlib

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

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
from app.modules.oauth.service import OAUTH_SCOPE_COOKIE

router = APIRouter(prefix="/api/oauth", tags=["dashboard"])


def _fallback_scope_key(request: Request) -> str:
    client_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "")
    digest = hashlib.sha256(f"{client_ip}|{user_agent}".encode("utf-8")).hexdigest()
    return f"anon_{digest[:24]}"


def _scope_key_for_start(request: Request) -> tuple[str, bool]:
    scope_key = request.cookies.get(OAUTH_SCOPE_COOKIE)
    if scope_key:
        return scope_key, False
    return _fallback_scope_key(request), True


def _scope_key_from_request(request: Request) -> str:
    return request.cookies.get(OAUTH_SCOPE_COOKIE) or _fallback_scope_key(request)


def _maybe_set_scope_cookie(response: JSONResponse, request: Request, scope_key: str, *, force: bool = False) -> None:
    if not force and request.cookies.get(OAUTH_SCOPE_COOKIE):
        return
    response.set_cookie(
        key=OAUTH_SCOPE_COOKIE,
        value=scope_key,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=30 * 24 * 60 * 60,
        path="/",
    )


@router.post("/start", response_model=OauthStartResponse)
async def start_oauth(
    http_request: Request,
    request: OauthStartRequest,
    context: OauthContext = Depends(get_oauth_context),
) -> OauthStartResponse | JSONResponse:
    scope_key, set_cookie = _scope_key_for_start(http_request)
    try:
        result = await context.service.start_oauth(request, scope_key=scope_key)
        response = JSONResponse(
            status_code=200,
            content=result.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
        _maybe_set_scope_cookie(response, http_request, scope_key, force=set_cookie)
        return response
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
    request: Request,
    context: OauthContext = Depends(get_oauth_context),
) -> OauthStatusResponse | JSONResponse:
    scope_key = _scope_key_from_request(request)
    result = await context.service.oauth_status(scope_key=scope_key)
    response = JSONResponse(
        status_code=200,
        content=result.model_dump(mode="json", by_alias=True, exclude_none=True),
    )
    _maybe_set_scope_cookie(response, request, scope_key, force=False)
    return response


@router.post("/complete", response_model=OauthCompleteResponse)
async def complete_oauth(
    http_request: Request,
    request: OauthCompleteRequest | None = Body(default=None),
    context: OauthContext = Depends(get_oauth_context),
) -> OauthCompleteResponse | JSONResponse:
    scope_key = _scope_key_from_request(http_request)
    try:
        result = await context.service.complete_oauth(request, scope_key=scope_key)
        response = JSONResponse(
            status_code=200,
            content=result.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
        _maybe_set_scope_cookie(response, http_request, scope_key, force=False)
        return response
    except NotImplementedError:
        return JSONResponse(
            status_code=501,
            content=dashboard_error("not_implemented", "OAuth complete is not implemented"),
        )

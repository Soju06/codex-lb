from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.core.audit.service import AuditService
from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.core.clients.account_proxy_probe import ProxyProbeError
from app.core.clients.oauth import OAuthError
from app.core.errors import dashboard_error
from app.core.exceptions import DashboardConflictError
from app.dependencies import OauthContext, get_oauth_context
from app.modules.accounts.repository import AccountIdentityConflictError
from app.modules.oauth.schemas import (
    ManualCallbackRequest,
    ManualCallbackResponse,
    OauthCompleteRequest,
    OauthCompleteResponse,
    OauthStartRequest,
    OauthStartResponse,
    OauthStatusResponse,
)
from app.modules.oauth.service import OauthProxyExpectationError

router = APIRouter(
    prefix="/api/oauth",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.post("/start", response_model=OauthStartResponse)
async def start_oauth(
    http_request: Request,
    request: OauthStartRequest,
    context: OauthContext = Depends(get_oauth_context),
) -> OauthStartResponse | JSONResponse:
    try:
        return await context.service.start_oauth(request, callback_host=http_request.url.hostname)
    except OauthProxyExpectationError as exc:
        return JSONResponse(
            status_code=422,
            content=dashboard_error("validation_error", str(exc)),
        )
    except ValidationError:
        return JSONResponse(
            status_code=422,
            content=dashboard_error("validation_error", "Invalid proxy configuration"),
        )
    except ProxyProbeError as exc:
        return _proxy_probe_error_response(exc)
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
    flow_id: str | None = Query(default=None, alias="flowId"),
    context: OauthContext = Depends(get_oauth_context),
) -> OauthStatusResponse | JSONResponse:
    return await context.service.oauth_status(flow_id=flow_id)


@router.post("/reset")
async def reset_oauth(
    context: OauthContext = Depends(get_oauth_context),
) -> dict[str, str]:
    await context.service.reset_oauth()
    return {"status": "reset"}


@router.post("/complete", response_model=OauthCompleteResponse)
async def complete_oauth(
    http_request: Request,
    request: OauthCompleteRequest | None = Body(default=None),
    context: OauthContext = Depends(get_oauth_context),
) -> OauthCompleteResponse | JSONResponse:
    try:
        response = await context.service.complete_oauth(request, accounts_service=context.accounts_service)
    except ValidationError:
        return JSONResponse(
            status_code=422,
            content=dashboard_error("validation_error", "Invalid proxy configuration"),
        )
    except ProxyProbeError as exc:
        return _proxy_probe_error_response(exc)
    except AccountIdentityConflictError as exc:
        raise DashboardConflictError(str(exc), code="duplicate_identity_conflict") from exc
    except NotImplementedError:
        return JSONResponse(
            status_code=501,
            content=dashboard_error("not_implemented", "OAuth complete is not implemented"),
        )

    if response.status == "success" and response.account_id and request is not None and request.proxy_host:
        # Symmetric with import-with-proxy: emit a typed audit row so
        # operators can correlate the OAuth attempt with the proxy
        # configuration that landed on the account.
        AuditService.log_async(
            "account_proxy_set",
            actor_ip=http_request.client.host if http_request.client else None,
            details={
                "account_id": response.account_id,
                "host": request.proxy_host,
                "port": request.proxy_port,
                "label": request.proxy_label,
                "remote_dns": request.proxy_remote_dns,
                "has_password": request.proxy_password is not None and bool(request.proxy_password.strip()),
                "source": "oauth",
            },
        )
    return response


@router.post("/manual-callback", response_model=ManualCallbackResponse)
async def manual_callback(
    request: ManualCallbackRequest,
    context: OauthContext = Depends(get_oauth_context),
) -> ManualCallbackResponse | JSONResponse:
    try:
        return await context.service.manual_callback(request.callback_url, flow_id=request.flow_id)
    except ProxyProbeError as exc:
        return _proxy_probe_error_response(exc)
    except Exception:
        return JSONResponse(
            status_code=500,
            content=dashboard_error("manual_callback_failed", "Manual OAuth callback failed"),
        )


def _proxy_probe_error_response(exc: ProxyProbeError) -> JSONResponse:
    envelope = dashboard_error(
        "proxy_probe_failed",
        f"Proxy validation failed: {exc.reason.value}",
    )
    envelope["error"]["reason"] = exc.reason.value  # type: ignore[typeddict-unknown-key]
    return JSONResponse(status_code=422, content=envelope)

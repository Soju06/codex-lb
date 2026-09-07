from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Security
from fastapi.responses import PlainTextResponse

from app.core.auth.dependencies import set_dashboard_error_format, validate_usage_api_key
from app.dependencies import KeyDashboardContext, get_key_dashboard_context
from app.modules.api_keys.service import ApiKeyData
from app.modules.key_dashboard.install import InstallPlatform, build_install_script
from app.modules.key_dashboard.schemas import KeyDashboardProfile, KeyDashboardRequestLogsResponse

router = APIRouter(
    prefix="/api/key-dashboard",
    tags=["key-dashboard"],
    dependencies=[Depends(set_dashboard_error_format)],
)


@router.get("/install-script", response_class=PlainTextResponse)
async def get_key_dashboard_install_script(
    request: Request,
    platform: InstallPlatform = Query(),
    api_key: ApiKeyData = Security(validate_usage_api_key),
) -> PlainTextResponse:
    # Validation above authenticates this exact header; never accept a key selector.
    credential = request.headers["authorization"].partition(" ")[2]
    model = api_key.enforced_model or (api_key.allowed_models[0] if api_key.allowed_models else None)
    script = build_install_script(
        platform=platform,
        api_key=credential,
        base_url=f"{str(request.base_url).rstrip('/')}/backend-api/codex",
        model=model,
    )
    extension = "ps1" if platform == "windows" else "sh"
    return PlainTextResponse(
        script,
        headers={
            "Cache-Control": "private, no-store",
            "Vary": "Authorization",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="codex-lb-{platform}.{extension}"',
        },
    )


@router.get("/profile", response_model=KeyDashboardProfile)
async def get_key_dashboard_profile(
    context: KeyDashboardContext = Depends(get_key_dashboard_context),
    api_key: ApiKeyData = Security(validate_usage_api_key),
) -> KeyDashboardProfile:
    return context.service.get_profile(api_key)


@router.get("/request-logs", response_model=KeyDashboardRequestLogsResponse)
async def get_key_dashboard_request_logs(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    context: KeyDashboardContext = Depends(get_key_dashboard_context),
    api_key: ApiKeyData = Security(validate_usage_api_key),
) -> KeyDashboardRequestLogsResponse:
    return await context.service.list_recent_requests(
        api_key_id=api_key.id,
        limit=limit,
        offset=offset,
    )

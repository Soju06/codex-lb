from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Security

from app.core.auth.dependencies import set_dashboard_error_format, validate_usage_api_key
from app.dependencies import KeyDashboardContext, get_key_dashboard_context
from app.modules.api_keys.service import ApiKeyData
from app.modules.key_dashboard.schemas import KeyDashboardProfile, KeyDashboardRequestLogsResponse

router = APIRouter(
    prefix="/api/key-dashboard",
    tags=["key-dashboard"],
    dependencies=[Depends(set_dashboard_error_format)],
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

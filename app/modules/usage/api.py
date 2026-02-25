from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.auth.dependencies import (
    DashboardPrincipal,
    get_dashboard_principal,
    set_dashboard_error_format,
    validate_dashboard_session,
)
from app.dependencies import UsageContext, get_usage_context
from app.modules.usage.schemas import UsageHistoryResponse, UsageSummaryResponse, UsageWindowResponse

router = APIRouter(
    prefix="/api/usage",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.get("/summary", response_model=UsageSummaryResponse)
async def get_usage_summary(
    owner_user_id: str | None = Query(default=None, alias="ownerUserId"),
    principal: DashboardPrincipal = Depends(get_dashboard_principal),
    context: UsageContext = Depends(get_usage_context),
) -> UsageSummaryResponse:
    return await context.service.get_usage_summary(owner_user_id=(owner_user_id if principal.is_admin else principal.user_id))


@router.get("/history", response_model=UsageHistoryResponse)
async def get_usage_history(
    hours: int = Query(24, ge=1, le=168),
    owner_user_id: str | None = Query(default=None, alias="ownerUserId"),
    principal: DashboardPrincipal = Depends(get_dashboard_principal),
    context: UsageContext = Depends(get_usage_context),
) -> UsageHistoryResponse:
    return await context.service.get_usage_history(
        hours,
        owner_user_id=(owner_user_id if principal.is_admin else principal.user_id),
    )


@router.get("/window", response_model=UsageWindowResponse)
async def get_usage_window(
    window: str = Query("primary", pattern="^(primary|secondary)$"),
    owner_user_id: str | None = Query(default=None, alias="ownerUserId"),
    principal: DashboardPrincipal = Depends(get_dashboard_principal),
    context: UsageContext = Depends(get_usage_context),
) -> UsageWindowResponse:
    return await context.service.get_usage_window(
        window,
        owner_user_id=(owner_user_id if principal.is_admin else principal.user_id),
    )

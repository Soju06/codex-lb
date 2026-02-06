from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.dependencies import DashboardContext, get_dashboard_context
from app.modules.dashboard.schemas import DashboardOverviewResponse

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/overview", response_model=DashboardOverviewResponse)
async def get_overview(
    request_limit: int = Query(25, ge=1, le=1000, alias="requestLimit"),
    request_offset: int = Query(0, ge=0, alias="requestOffset"),
    context: DashboardContext = Depends(get_dashboard_context),
) -> DashboardOverviewResponse:
    return await context.service.get_overview(
        request_limit=request_limit,
        request_offset=request_offset,
    )

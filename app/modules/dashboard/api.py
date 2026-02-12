from __future__ import annotations

from fastapi import APIRouter, Depends

from app.dependencies import DashboardContext, get_dashboard_context
from app.modules.dashboard.schemas import DashboardOverviewResponse

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/overview", response_model=DashboardOverviewResponse)
async def get_overview(
    context: DashboardContext = Depends(get_dashboard_context),
) -> DashboardOverviewResponse:
    return await context.service.get_overview()

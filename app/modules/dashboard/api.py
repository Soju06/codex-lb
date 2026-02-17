from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.openai.model_registry import get_model_registry
from app.dependencies import DashboardContext, get_dashboard_context
from app.modules.dashboard.schemas import DashboardOverviewResponse

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/dashboard/overview", response_model=DashboardOverviewResponse)
async def get_overview(
    context: DashboardContext = Depends(get_dashboard_context),
) -> DashboardOverviewResponse:
    return await context.service.get_overview()


@router.get("/models")
async def list_models() -> dict:
    registry = get_model_registry()
    snapshot = registry.get_snapshot()
    if snapshot is None:
        return {"models": []}
    models = [
        {"id": slug, "name": model.display_name or slug}
        for slug, model in snapshot.models.items()
        if model.supported_in_api
    ]
    return {"models": models}

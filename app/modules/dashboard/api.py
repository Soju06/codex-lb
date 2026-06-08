from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.core.openai.model_registry import get_model_registry, is_public_model
from app.dependencies import DashboardContext, get_dashboard_context
from app.modules.agent_provider_runtime.model_catalog import list_agent_provider_models
from app.modules.dashboard.schemas import (
    DashboardOverviewResponse,
    DashboardOverviewTimeframeKey,
    DashboardProjectionsResponse,
)

router = APIRouter(
    prefix="/api",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.get("/dashboard/overview", response_model=DashboardOverviewResponse)
async def get_overview(
    timeframe: DashboardOverviewTimeframeKey = Query("7d"),
    context: DashboardContext = Depends(get_dashboard_context),
) -> DashboardOverviewResponse:
    return await context.service.get_overview(timeframe)


@router.get("/dashboard/projections", response_model=DashboardProjectionsResponse)
async def get_projections(
    context: DashboardContext = Depends(get_dashboard_context),
) -> DashboardProjectionsResponse:
    return await context.service.get_projections()


@router.get("/models")
async def list_models() -> dict:
    registry = get_model_registry()
    models_by_slug = registry.get_models_with_fallback()
    models = [
        {"id": slug, "name": model.display_name or slug, "provider": "codex"}
        for slug, model in models_by_slug.items()
        if is_public_model(model, None)
    ]
    models.extend(
        {
            "id": model.model_id,
            "name": model.display_name,
            "provider": model.provider_id,
            "protocol": model.protocol,
            "lifecycle": model.lifecycle,
        }
        for model in list_agent_provider_models()
    )
    return {"models": models}

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.core.openai.model_registry import get_model_registry, is_public_model
from app.dependencies import DashboardContext, get_dashboard_context
from app.modules.dashboard.schemas import DashboardOverviewResponse, DashboardOverviewTimeframeKey

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


@router.get("/models")
async def list_models() -> dict:
    registry = get_model_registry()
    models_by_slug = registry.get_models_with_fallback()
    if not models_by_slug:
        return {"models": []}
    allowed_efforts = {"minimal", "low", "medium", "high", "xhigh"}

    def _normalize_effort(value: str | None) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower()
        if normalized in allowed_efforts:
            return normalized
        return None

    models = [
        {
            "id": slug,
            "name": model.display_name or slug,
            "supportedReasoningEfforts": list(
                dict.fromkeys(
                    effort
                    for effort in (_normalize_effort(level.effort) for level in model.supported_reasoning_levels)
                    if effort is not None
                )
            ),
            "defaultReasoningEffort": _normalize_effort(model.default_reasoning_level),
        }
        for slug, model in models_by_slug.items()
        if is_public_model(model, None)
    ]
    return {"models": models}

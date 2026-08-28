from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.core.openai.model_registry import get_model_registry, is_public_model
from app.db.session import detach_session_objects, get_background_session
from app.dependencies import DashboardContext, get_dashboard_context
from app.modules.dashboard.schemas import (
    DashboardOverviewResponse,
    DashboardOverviewTimeframeKey,
    DashboardProjectionsResponse,
)
from app.modules.model_sources.catalog import source_models_to_upstream_models
from app.modules.model_sources.repository import ModelSourcesRepository

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
    if not models_by_slug:
        return {"models": []}
    allowed_efforts = {"minimal", "low", "medium", "high", "xhigh", "max", "ultra"}

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
            "sourceOnly": False,
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
    # The API-key "allowed models" picker must offer OpenAI-compatible source
    # models too, or source-scoped allowlists cannot be configured in the UI.
    seen_slugs = set(models_by_slug)
    async with get_background_session() as session:
        sources = await ModelSourcesRepository(session).list_enabled_sources()
        detach_session_objects(session)
    for source_model in source_models_to_upstream_models(sources):
        if source_model.slug in seen_slugs:
            continue
        seen_slugs.add(source_model.slug)
        models.append(
            {
                "id": source_model.slug,
                "name": source_model.display_name or source_model.slug,
                "sourceOnly": True,
            }
        )
    return {"models": models}

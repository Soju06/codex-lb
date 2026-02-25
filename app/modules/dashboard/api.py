from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.auth.dependencies import (
    DashboardPrincipal,
    get_dashboard_principal,
    set_dashboard_error_format,
    validate_dashboard_session,
)
from app.core.openai.model_registry import get_model_registry, is_public_model
from app.dependencies import DashboardContext, get_dashboard_context
from app.modules.dashboard.schemas import DashboardOverviewResponse

router = APIRouter(
    prefix="/api",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.get("/dashboard/overview", response_model=DashboardOverviewResponse)
async def get_overview(
    owner_user_id: str | None = Query(default=None, alias="ownerUserId"),
    principal: DashboardPrincipal = Depends(get_dashboard_principal),
    context: DashboardContext = Depends(get_dashboard_context),
) -> DashboardOverviewResponse:
    return await context.service.get_overview(owner_user_id=(owner_user_id if principal.is_admin else principal.user_id))


@router.get("/models")
async def list_models() -> dict:
    registry = get_model_registry()
    snapshot = registry.get_snapshot()
    if snapshot is None:
        return {"models": []}
    models = [
        {"id": slug, "name": model.display_name or slug}
        for slug, model in snapshot.models.items()
        if is_public_model(model, None)
    ]
    return {"models": models}

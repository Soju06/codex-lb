from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.core.exceptions import DashboardBadRequestError, DashboardNotFoundError
from app.dependencies import ModelOverridesContext, get_model_overrides_context
from app.modules.model_overrides.schemas import (
    ModelOverrideCreateRequest,
    ModelOverrideEntry,
    ModelOverridesResponse,
    ModelOverrideUpdateRequest,
)
from app.modules.model_overrides.service import (
    ModelOverrideConflictError,
    ModelOverrideCreateData,
    ModelOverrideNotFoundError,
    ModelOverrideUpdateData,
)

router = APIRouter(
    prefix="/api/model-overrides",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


def _to_entry(data) -> ModelOverrideEntry:
    return ModelOverrideEntry(
        id=data.id,
        match_type=data.match_type,
        match_value=data.match_value,
        forced_model=data.forced_model,
        forced_reasoning_effort=data.forced_reasoning_effort,
        enabled=data.enabled,
        note=data.note,
        created_at=data.created_at,
        updated_at=data.updated_at,
    )


@router.get("", response_model=ModelOverridesResponse)
async def list_model_overrides(
    context: ModelOverridesContext = Depends(get_model_overrides_context),
) -> ModelOverridesResponse:
    items = await context.service.list_overrides()
    return ModelOverridesResponse(items=[_to_entry(item) for item in items])


@router.post("", response_model=ModelOverrideEntry)
async def create_model_override(
    payload: ModelOverrideCreateRequest = Body(...),
    context: ModelOverridesContext = Depends(get_model_overrides_context),
) -> ModelOverrideEntry:
    try:
        created = await context.service.create_override(
            ModelOverrideCreateData(
                match_type=payload.match_type,
                match_value=payload.match_value,
                forced_model=payload.forced_model,
                forced_reasoning_effort=payload.forced_reasoning_effort,
                enabled=payload.enabled,
                note=payload.note,
            )
        )
    except (ValueError, ModelOverrideConflictError) as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_model_override") from exc
    return _to_entry(created)


@router.put("/{override_id}", response_model=ModelOverrideEntry)
async def update_model_override(
    override_id: int,
    payload: ModelOverrideUpdateRequest = Body(...),
    context: ModelOverridesContext = Depends(get_model_overrides_context),
) -> ModelOverrideEntry:
    try:
        updated = await context.service.update_override(
            override_id,
            ModelOverrideUpdateData(
                match_value=payload.match_value,
                forced_model=payload.forced_model,
                forced_reasoning_effort=payload.forced_reasoning_effort,
                enabled=payload.enabled,
                note=payload.note,
            ),
        )
    except ModelOverrideNotFoundError as exc:
        raise DashboardNotFoundError(str(exc), code="model_override_not_found") from exc
    except (ValueError, ModelOverrideConflictError) as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_model_override") from exc
    return _to_entry(updated)


@router.delete("/{override_id}", status_code=204)
async def delete_model_override(
    override_id: int,
    context: ModelOverridesContext = Depends(get_model_overrides_context),
) -> None:
    try:
        await context.service.delete_override(override_id)
    except ModelOverrideNotFoundError as exc:
        raise DashboardNotFoundError(str(exc), code="model_override_not_found") from exc


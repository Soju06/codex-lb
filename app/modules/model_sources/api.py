from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request, Response

from app.core.audit.service import AuditService
from app.core.auth.dependencies import (
    require_dashboard_write_access,
    set_dashboard_error_format,
    validate_dashboard_session,
)
from app.core.exceptions import DashboardBadRequestError, DashboardNotFoundError
from app.dependencies import ModelSourcesContext, get_model_sources_context
from app.modules.model_sources.schemas import (
    ModelSourceCreateRequest,
    ModelSourceResponse,
    ModelSourcesResponse,
    ModelSourceUpdateRequest,
)
from app.modules.model_sources.service import ModelSourceNotFoundError, ModelSourceValidationError

router = APIRouter(
    prefix="/api/model-sources",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.get("/", response_model=ModelSourcesResponse)
async def list_model_sources(
    context: ModelSourcesContext = Depends(get_model_sources_context),
) -> ModelSourcesResponse:
    return ModelSourcesResponse(sources=await context.service.list_sources())


@router.post("/", response_model=ModelSourceResponse)
async def create_model_source(
    request: Request,
    payload: ModelSourceCreateRequest = Body(...),
    _write_access=Depends(require_dashboard_write_access),
    context: ModelSourcesContext = Depends(get_model_sources_context),
) -> ModelSourceResponse:
    try:
        created = await context.service.create_source(payload)
    except ModelSourceValidationError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_model_source_payload") from exc
    AuditService.log_async(
        "model_source_created",
        actor_ip=request.client.host if request.client else None,
        details={"source_id": created.id},
    )
    return created


@router.patch("/{source_id}", response_model=ModelSourceResponse)
async def update_model_source(
    request: Request,
    source_id: str,
    payload: ModelSourceUpdateRequest = Body(...),
    _write_access=Depends(require_dashboard_write_access),
    context: ModelSourcesContext = Depends(get_model_sources_context),
) -> ModelSourceResponse:
    try:
        updated = await context.service.update_source(source_id, payload)
    except ModelSourceNotFoundError as exc:
        raise DashboardNotFoundError(str(exc)) from exc
    except ModelSourceValidationError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_model_source_payload") from exc
    AuditService.log_async(
        "model_source_updated",
        actor_ip=request.client.host if request.client else None,
        details={"source_id": updated.id},
    )
    return updated


@router.delete("/{source_id}")
async def delete_model_source(
    request: Request,
    source_id: str,
    _write_access=Depends(require_dashboard_write_access),
    context: ModelSourcesContext = Depends(get_model_sources_context),
) -> Response:
    try:
        await context.service.delete_source(source_id)
    except ModelSourceNotFoundError as exc:
        raise DashboardNotFoundError(str(exc)) from exc
    AuditService.log_async(
        "model_source_deleted",
        actor_ip=request.client.host if request.client else None,
        details={"source_id": source_id},
    )
    return Response(status_code=204)

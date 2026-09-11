from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request, Response
from sqlalchemy.orm.exc import StaleDataError

from app.core.audit.service import AuditActor, AuditService, AuditTarget
from app.core.auth.dashboard_access import DashboardPrincipal
from app.core.auth.dependencies import (
    require_dashboard_write_access,
    set_dashboard_error_format,
    validate_dashboard_session,
)
from app.core.config.settings_cache import get_settings_cache
from app.core.exceptions import DashboardBadRequestError, DashboardNotFoundError, DashboardSettingsConflictError
from app.core.utils.time import utcnow
from app.dependencies import ModelSourcesContext, get_model_sources_context
from app.modules.model_sources.schemas import (
    ModelSourceCreateRequest,
    ModelSourceResponse,
    ModelSourcesResponse,
    ModelSourceUpdateRequest,
)
from app.modules.model_sources.service import ModelSourceNotFoundError, ModelSourceValidationError
from app.modules.settings.repository import SettingsRepository
from app.modules.settings.subscription_overflow import DRAIN_WINDOW

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
    principal: DashboardPrincipal = Depends(require_dashboard_write_access),
    context: ModelSourcesContext = Depends(get_model_sources_context),
) -> ModelSourceResponse:
    try:
        created = await context.service.create_source(payload)
    except ModelSourceValidationError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_model_source_payload") from exc
    AuditService.log_async(
        "model_source_created",
        actor_ip=request.client.host if request.client else None,
        actor=AuditActor.from_principal(principal),
        target=AuditTarget("model_source", created.id),
        details={"source_id": created.id},
    )
    return created


@router.patch("/{source_id}", response_model=ModelSourceResponse)
async def update_model_source(
    request: Request,
    source_id: str,
    payload: ModelSourceUpdateRequest = Body(...),
    principal: DashboardPrincipal = Depends(require_dashboard_write_access),
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
        actor=AuditActor.from_principal(principal),
        target=AuditTarget("model_source", updated.id),
        details={"source_id": updated.id},
    )
    return updated


@router.delete("/{source_id}")
async def delete_model_source(
    request: Request,
    source_id: str,
    principal: DashboardPrincipal = Depends(require_dashboard_write_access),
    context: ModelSourcesContext = Depends(get_model_sources_context),
) -> Response:
    # Deleting the designated subscription-overflow source is a kill switch
    # (#2123 design §8.8): clear the designation and arm the drain deadline in
    # the same transaction as the delete (``ModelSourcesRepository.delete``
    # issues the single commit), so a designated-but-deleted source can never
    # persist. When the source is missing nothing is committed.
    now = utcnow()
    overflow_cleared = await SettingsRepository(context.session).clear_subscription_overflow_source_if_matches(
        source_id,
        drain_until=now + DRAIN_WINDOW,
    )
    try:
        await context.service.delete_source(source_id)
    except ModelSourceNotFoundError as exc:
        raise DashboardNotFoundError(str(exc)) from exc
    except StaleDataError as exc:
        # The settings row is version-checked; a concurrent settings save
        # between the clear and the commit must not silently drop either write.
        await context.session.rollback()
        raise DashboardSettingsConflictError(
            "Settings were modified while deleting the model source; retry",
        ) from exc
    if overflow_cleared:
        # After the commit: drops the per-replica settings cache and bumps the
        # cross-replica ``settings`` namespace so every replica stops reading
        # the deleted designation.
        await get_settings_cache().invalidate()
    AuditService.log_async(
        "model_source_deleted",
        actor_ip=request.client.host if request.client else None,
        actor=AuditActor.from_principal(principal),
        target=AuditTarget("model_source", source_id),
        details={"source_id": source_id, "subscription_overflow_cleared": overflow_cleared},
    )
    return Response(status_code=204)

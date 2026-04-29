from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.core.exceptions import DashboardBadRequestError, DashboardConflictError, DashboardNotFoundError
from app.dependencies import PeerFallbackTargetsContext, get_peer_fallback_targets_context
from app.modules.peer_fallback_targets.schemas import (
    PeerFallbackTargetCreateRequest,
    PeerFallbackTargetDeleteResponse,
    PeerFallbackTargetResponse,
    PeerFallbackTargetsResponse,
    PeerFallbackTargetUpdateRequest,
)
from app.modules.peer_fallback_targets.service import (
    PeerFallbackTargetAlreadyExistsError,
    PeerFallbackTargetCreateData,
    PeerFallbackTargetData,
    PeerFallbackTargetNotFoundError,
    PeerFallbackTargetUpdateData,
    PeerFallbackTargetValidationError,
)

router = APIRouter(
    prefix="/api/peer-fallback-targets",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.get("", response_model=PeerFallbackTargetsResponse)
async def list_peer_fallback_targets(
    context: PeerFallbackTargetsContext = Depends(get_peer_fallback_targets_context),
) -> PeerFallbackTargetsResponse:
    targets = await context.service.list_targets()
    return PeerFallbackTargetsResponse(targets=[_target_response(target) for target in targets])


@router.post("", response_model=PeerFallbackTargetResponse)
async def create_peer_fallback_target(
    payload: PeerFallbackTargetCreateRequest = Body(...),
    context: PeerFallbackTargetsContext = Depends(get_peer_fallback_targets_context),
) -> PeerFallbackTargetResponse:
    try:
        target = await context.service.create_target(
            PeerFallbackTargetCreateData(base_url=payload.base_url, enabled=payload.enabled)
        )
    except PeerFallbackTargetValidationError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_peer_fallback_target") from exc
    except PeerFallbackTargetAlreadyExistsError as exc:
        raise DashboardConflictError(str(exc), code="peer_fallback_target_exists") from exc
    return _target_response(target)


@router.patch("/{target_id}", response_model=PeerFallbackTargetResponse)
async def update_peer_fallback_target(
    target_id: str,
    payload: PeerFallbackTargetUpdateRequest = Body(...),
    context: PeerFallbackTargetsContext = Depends(get_peer_fallback_targets_context),
) -> PeerFallbackTargetResponse:
    try:
        target = await context.service.update_target(
            target_id,
            PeerFallbackTargetUpdateData(base_url=payload.base_url, enabled=payload.enabled),
        )
    except PeerFallbackTargetValidationError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_peer_fallback_target") from exc
    except PeerFallbackTargetAlreadyExistsError as exc:
        raise DashboardConflictError(str(exc), code="peer_fallback_target_exists") from exc
    except PeerFallbackTargetNotFoundError as exc:
        raise DashboardNotFoundError(str(exc), code="peer_fallback_target_not_found") from exc
    return _target_response(target)


@router.delete("/{target_id}", response_model=PeerFallbackTargetDeleteResponse)
async def delete_peer_fallback_target(
    target_id: str,
    context: PeerFallbackTargetsContext = Depends(get_peer_fallback_targets_context),
) -> PeerFallbackTargetDeleteResponse:
    try:
        await context.service.delete_target(target_id)
    except PeerFallbackTargetNotFoundError as exc:
        raise DashboardNotFoundError(str(exc), code="peer_fallback_target_not_found") from exc
    return PeerFallbackTargetDeleteResponse(status="deleted")


def _target_response(target: PeerFallbackTargetData) -> PeerFallbackTargetResponse:
    return PeerFallbackTargetResponse(
        id=target.id,
        base_url=target.base_url,
        enabled=target.enabled,
        created_at=target.created_at,
        updated_at=target.updated_at,
    )

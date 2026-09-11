"""Group-to-role rules (``security:write``, step-up).

``security:write`` is a step-up permission, so every mutation here already
requires a credential re-verified in the last five minutes. Every route,
reads included, additionally needs a caller the audit log can name
(``principal.user_id``), like every account mutation: these rules decide who
signs in, and an install with no account behind the request has nobody to
attribute a change to.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query, Request, Response

from app.core.auth.dashboard_access import DashboardPrincipal, InsufficientDelegationError, Permission, RoleKind
from app.core.auth.dependencies import require_dashboard_permission, set_dashboard_error_format
from app.core.exceptions import (
    DashboardConflictError,
    DashboardNotFoundError,
    DashboardPermissionError,
    DashboardValidationError,
)
from app.db.models import DashboardRoleMapping, DashboardRoleRecord
from app.dependencies import RoleMappingsContext, get_role_mappings_context
from app.modules.dashboard_roles.service import RoleNotAssignableError
from app.modules.role_mappings.schemas import (
    AssignableRoleResponse,
    RoleMappingCreateRequest,
    RoleMappingOrderRequest,
    RoleMappingResponse,
    RoleMappingUpdateRequest,
)
from app.modules.role_mappings.service import (
    MappingExistsError,
    MappingLimitReachedError,
    MappingNotFoundError,
    MappingProviderNotFoundError,
    OrderStaleError,
    UnknownClaimError,
)

router = APIRouter(
    prefix="/api/role-mappings",
    tags=["dashboard"],
    dependencies=[
        Depends(require_dashboard_permission(Permission.SECURITY_WRITE)),
        Depends(set_dashboard_error_format),
    ],
)


async def _require_account(
    principal: DashboardPrincipal = Depends(require_dashboard_permission(Permission.SECURITY_WRITE)),
) -> DashboardPrincipal:
    if principal.user_id is None:
        raise DashboardConflictError(
            "Set a dashboard password and sign in before changing sign-in rules", code="admin_account_required"
        )
    return principal


def _response(mapping: DashboardRoleMapping) -> RoleMappingResponse:
    return RoleMappingResponse(
        id=mapping.id,
        provider=mapping.provider,
        provider_key=mapping.provider_key,
        claim_name=mapping.claim_name,
        claim_value=mapping.claim_value,
        role_id=mapping.role_id,
        priority=mapping.priority,
        created_at=mapping.created_at,
        updated_at=mapping.updated_at,
    )


def _role_response(role: DashboardRoleRecord) -> AssignableRoleResponse:
    return AssignableRoleResponse(
        id=role.id,
        slug=role.slug,
        name=role.name,
        description=role.description,
        kind=role.kind,
        locked=RoleKind(role.kind) is RoleKind.PRESET,
    )


def _mapped(exc: Exception) -> Exception:
    """Translate a service refusal into the dashboard error envelope."""

    if isinstance(exc, MappingNotFoundError):
        return DashboardNotFoundError(str(exc), code="mapping_not_found")
    if isinstance(exc, MappingProviderNotFoundError):
        return DashboardNotFoundError(str(exc), code="provider_not_found")
    if isinstance(exc, UnknownClaimError):
        return DashboardValidationError(str(exc), code="unknown_claim")
    if isinstance(exc, MappingExistsError):
        return DashboardConflictError(str(exc), code="mapping_exists")
    if isinstance(exc, MappingLimitReachedError):
        return DashboardConflictError(str(exc), code="mapping_limit_reached")
    if isinstance(exc, OrderStaleError):
        return DashboardConflictError(str(exc), code="order_stale")
    if isinstance(exc, RoleNotAssignableError):
        return DashboardValidationError(str(exc), code="role_not_assignable")
    if isinstance(exc, InsufficientDelegationError):
        return DashboardPermissionError(str(exc), code="insufficient_delegation")
    return exc


_REFUSALS = (
    MappingNotFoundError,
    MappingProviderNotFoundError,
    UnknownClaimError,
    MappingExistsError,
    MappingLimitReachedError,
    OrderStaleError,
    RoleNotAssignableError,
    InsufficientDelegationError,
)


def _client_host(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("", response_model=list[RoleMappingResponse])
async def list_mappings(
    provider: str | None = Query(default=None, max_length=32),
    provider_key: str | None = Query(default=None, alias="providerKey", max_length=64),
    _principal: DashboardPrincipal = Depends(_require_account),
    context: RoleMappingsContext = Depends(get_role_mappings_context),
) -> list[RoleMappingResponse]:
    mappings = await context.service.list_mappings(provider=provider, provider_key=provider_key)
    return [_response(mapping) for mapping in mappings]


@router.get("/assignable-roles", response_model=list[AssignableRoleResponse])
async def list_assignable_roles(
    principal: DashboardPrincipal = Depends(_require_account),
    context: RoleMappingsContext = Depends(get_role_mappings_context),
) -> list[AssignableRoleResponse]:
    """The roles this caller may hand out here.

    The rules cards need to name a role, and the roles list itself is
    ``users:manage`` — a different permission from the one that gates these
    rules. Without this a ``security:write`` custom role could edit the rules
    but never see, or choose, the role any of them gives.
    """

    return [_role_response(role) for role in await context.service.assignable_roles(principal)]


@router.post("", response_model=RoleMappingResponse, status_code=201)
async def create_mapping(
    request: Request,
    payload: RoleMappingCreateRequest = Body(...),
    principal: DashboardPrincipal = Depends(_require_account),
    context: RoleMappingsContext = Depends(get_role_mappings_context),
) -> RoleMappingResponse:
    try:
        mapping = await context.service.create_mapping(principal, payload, actor_ip=_client_host(request))
    except _REFUSALS as exc:
        raise _mapped(exc) from exc
    return _response(mapping)


@router.put("/order", response_model=list[RoleMappingResponse])
async def reorder_mappings(
    request: Request,
    payload: RoleMappingOrderRequest = Body(...),
    principal: DashboardPrincipal = Depends(_require_account),
    context: RoleMappingsContext = Depends(get_role_mappings_context),
) -> list[RoleMappingResponse]:
    try:
        mappings = await context.service.reorder_mappings(principal, payload, actor_ip=_client_host(request))
    except _REFUSALS as exc:
        raise _mapped(exc) from exc
    return [_response(mapping) for mapping in mappings]


@router.patch("/{mapping_id}", response_model=RoleMappingResponse)
async def update_mapping(
    mapping_id: str,
    request: Request,
    payload: RoleMappingUpdateRequest = Body(...),
    principal: DashboardPrincipal = Depends(_require_account),
    context: RoleMappingsContext = Depends(get_role_mappings_context),
) -> RoleMappingResponse:
    try:
        mapping = await context.service.update_mapping(principal, mapping_id, payload, actor_ip=_client_host(request))
    except _REFUSALS as exc:
        raise _mapped(exc) from exc
    return _response(mapping)


@router.delete("/{mapping_id}", status_code=204)
async def delete_mapping(
    mapping_id: str,
    request: Request,
    principal: DashboardPrincipal = Depends(_require_account),
    context: RoleMappingsContext = Depends(get_role_mappings_context),
) -> Response:
    try:
        await context.service.delete_mapping(principal, mapping_id, actor_ip=_client_host(request))
    except _REFUSALS as exc:
        raise _mapped(exc) from exc
    return Response(status_code=204)

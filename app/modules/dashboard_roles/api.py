"""Read-only roles API. Reading roles is part of managing users (``users:manage``);
writing roles (``roles:manage``) arrives with the custom-role editor."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.auth.dashboard_access import Permission, RoleKind
from app.core.auth.dependencies import require_dashboard_permission, set_dashboard_error_format
from app.dependencies import DashboardRolesContext, get_dashboard_roles_context
from app.modules.dashboard_roles.schemas import DashboardRoleResponse, PermissionDescriptorResponse, RoleGrantResponse
from app.modules.dashboard_roles.service import (
    permission_descriptors,
    resolve_role_grants,
    role_assignable_to_users,
)

router = APIRouter(
    prefix="/api/dashboard-roles",
    tags=["dashboard"],
    dependencies=[Depends(require_dashboard_permission(Permission.USERS_MANAGE)), Depends(set_dashboard_error_format)],
)


@router.get("", response_model=list[DashboardRoleResponse])
async def list_roles(
    context: DashboardRolesContext = Depends(get_dashboard_roles_context),
) -> list[DashboardRoleResponse]:
    users_by_role = await context.repository.users_count_by_role()
    responses: list[DashboardRoleResponse] = []
    for role in await context.repository.list_roles():
        is_preset = RoleKind(role.kind) is RoleKind.PRESET
        responses.append(
            DashboardRoleResponse(
                id=role.id,
                slug=role.slug,
                name=role.name,
                description=role.description,
                kind=role.kind,
                locked=is_preset,
                # Code is the truth for presets: the row's flag only mirrors it.
                assignable_to_users=role_assignable_to_users(role),
                grants=[
                    RoleGrantResponse(permission=permission.value, scope=scope.value)
                    for permission, scope in sorted(resolve_role_grants(role).items(), key=lambda item: item[0].value)
                ],
                users_count=users_by_role.get(role.id, 0),
            )
        )
    return responses


@router.get("/permissions", response_model=list[PermissionDescriptorResponse])
async def list_permissions() -> list[PermissionDescriptorResponse]:
    return [
        PermissionDescriptorResponse(
            permission=descriptor.permission.value,
            description=descriptor.description,
            implies=[dependency.value for dependency in descriptor.implies],
            own_supported=descriptor.own_supported,
            privileged=descriptor.privileged,
        )
        for descriptor in permission_descriptors()
    ]

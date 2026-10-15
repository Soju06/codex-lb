from __future__ import annotations

from app.modules.shared.schemas import DashboardModel


class RoleGrantResponse(DashboardModel):
    permission: str
    scope: str


class DashboardRoleResponse(DashboardModel):
    id: str
    slug: str
    name: str
    description: str | None = None
    kind: str
    #: Presets are defined by the product and cannot be edited by anyone.
    locked: bool
    assignable_to_users: bool
    grants: list[RoleGrantResponse]
    users_count: int


class PermissionDescriptorResponse(DashboardModel):
    permission: str
    description: str
    implies: list[str]
    own_supported: bool
    privileged: bool

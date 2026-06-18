from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.core.auth.dashboard_mode import DashboardAuthMode


class DashboardRole(StrEnum):
    ADMIN = "admin"
    GUEST = "guest"


class DashboardPermission(StrEnum):
    READ = "read"
    WRITE = "write"


@dataclass(frozen=True, slots=True)
class DashboardPrincipal:
    role: DashboardRole
    permissions: frozenset[DashboardPermission]
    auth_mode: DashboardAuthMode
    actor: str | None = None

    def can(self, permission: DashboardPermission) -> bool:
        return permission in self.permissions


ADMIN_PERMISSIONS = frozenset({DashboardPermission.READ, DashboardPermission.WRITE})
GUEST_PERMISSIONS = frozenset({DashboardPermission.READ})


def admin_principal(*, auth_mode: DashboardAuthMode, actor: str | None = None) -> DashboardPrincipal:
    return DashboardPrincipal(
        role=DashboardRole.ADMIN,
        permissions=ADMIN_PERMISSIONS,
        auth_mode=auth_mode,
        actor=actor,
    )


def guest_principal() -> DashboardPrincipal:
    return DashboardPrincipal(
        role=DashboardRole.GUEST,
        permissions=GUEST_PERMISSIONS,
        auth_mode=DashboardAuthMode.STANDARD,
    )

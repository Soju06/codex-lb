"""Resolve a role row into the grant table the authorization layer consumes."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from types import MappingProxyType

from app.core.auth.dashboard_access import (
    OWN_SCOPED_PERMISSIONS,
    PERMISSION_IMPLIES,
    PRESET_ROLE_GRANTS,
    PRIVILEGED_PERMISSIONS,
    Grants,
    Permission,
    PresetRoleSlug,
    RoleKind,
    Scope,
)
from app.db.models import DashboardRoleGrant, DashboardRoleRecord

logger = logging.getLogger(__name__)


def grants_from_rows(rows: Iterable[DashboardRoleGrant]) -> Grants:
    """Build a grant table from custom-role rows.

    Unknown permission or scope strings are skipped, not raised: during a
    rolling upgrade a newer replica may have written a permission this
    replica's vocabulary does not know yet, and that must not break every
    request for holders of the role.
    """

    grants: dict[Permission, Scope] = {}
    for row in rows:
        try:
            permission = Permission(row.permission)
            scope = Scope(row.scope)
        except ValueError:
            logger.warning(
                "dashboard_role_grant_ignored role_id=%s permission=%s scope=%s",
                row.role_id,
                row.permission,
                row.scope,
            )
            continue
        grants[permission] = scope
    return MappingProxyType(grants)


def resolve_role_grants(role: DashboardRoleRecord) -> Grants:
    """Preset roles resolve from code; custom roles from their stored grant rows.

    An unknown ``kind`` or an unknown preset slug raises: preset rows are only
    written by the migration, so such a row is a provisioning error and must
    not fall back to a quiet default.
    """

    if RoleKind(role.kind) is RoleKind.PRESET:
        return PRESET_ROLE_GRANTS[PresetRoleSlug(role.slug)]
    return grants_from_rows(role.grants)


#: One plain sentence per permission for role pickers and the editor grid.
#: The grant tables themselves stay in ``dashboard_access``; this only describes them.
PERMISSION_DESCRIPTIONS: dict[Permission, str] = {
    Permission.DASHBOARD_READ: "Read the dashboard overview, reports, request logs and the model catalog.",
    Permission.ACCOUNTS_READ: "See upstream accounts and their usage windows.",
    Permission.ACCOUNTS_WRITE: "Add, edit, pause and remove upstream accounts and their routing.",
    Permission.ACCOUNTS_EXPORT: "Export upstream account credentials.",
    Permission.API_KEYS_READ: "See API keys, their policies and their usage.",
    Permission.API_KEYS_WRITE: "Create, edit, rotate and delete API keys.",
    Permission.API_KEYS_ASSIGN: "Assign upstream accounts, model sources and owners to API keys.",
    Permission.OPS_WRITE: "Change operational settings such as model sources, automations and the quota planner.",
    Permission.SECURITY_WRITE: "Change security settings: guest access, firewall, upstream proxy credentials.",
    Permission.USERS_MANAGE: "Invite, edit, disable and remove dashboard accounts.",
    Permission.ROLES_MANAGE: "Create, edit and delete custom roles.",
    Permission.CONVERSATIONS_READ: "Read conversation contents and archives.",
    Permission.AUDIT_READ: "Read the audit log.",
}


@dataclass(frozen=True, slots=True)
class PermissionDescriptor:
    permission: Permission
    description: str
    implies: tuple[Permission, ...]
    own_supported: bool
    privileged: bool


def permission_descriptors() -> list[PermissionDescriptor]:
    """The permission vocabulary with its rules, derived from the single code source."""

    return [
        PermissionDescriptor(
            permission=permission,
            description=PERMISSION_DESCRIPTIONS[permission],
            implies=tuple(sorted(PERMISSION_IMPLIES.get(permission, frozenset()), key=lambda dep: dep.value)),
            own_supported=permission in OWN_SCOPED_PERMISSIONS,
            privileged=permission in PRIVILEGED_PERMISSIONS,
        )
        for permission in Permission
    ]

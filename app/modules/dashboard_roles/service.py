"""Resolve a role row into the grant table the authorization layer consumes."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from types import MappingProxyType

from app.core.auth.dashboard_access import (
    PRESET_ROLE_GRANTS,
    Grants,
    Permission,
    PresetRoleSlug,
    Scope,
)
from app.db.models import DashboardRoleGrant, DashboardRoleRecord
from app.modules.dashboard_roles.seed import ROLE_KIND_PRESET

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
    """Preset roles resolve from code; custom roles from their stored grant rows."""

    if role.kind == ROLE_KIND_PRESET:
        return PRESET_ROLE_GRANTS[PresetRoleSlug(role.slug)]
    return grants_from_rows(role.grants)

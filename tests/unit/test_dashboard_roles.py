from __future__ import annotations

import uuid

import pytest

from app.core.auth.dashboard_access import (
    ADMIN_GRANTS,
    ASSIGNABLE_PRESET_ROLES,
    GUEST_GRANTS,
    MEMBER_GRANTS,
    OPERATOR_GRANTS,
    OWN_SCOPED_PERMISSIONS,
    PRESET_ROLE_GRANTS,
    PRESET_ROLE_IDS,
    PRESET_ROLE_NAMES,
    ROLE_GRANTS,
    VIEWER_GRANTS,
    DashboardRole,
    Permission,
    PresetRoleSlug,
    Scope,
    legacy_permissions,
    preset_role_id,
    validate_grants,
)
from app.db.models import DashboardRoleGrant, DashboardRoleRecord
from app.modules.dashboard_roles.seed import ROLE_KIND_CUSTOM, ROLE_KIND_PRESET, preset_role_rows
from app.modules.dashboard_roles.service import grants_from_rows, resolve_role_grants

pytestmark = pytest.mark.unit


def test_every_preset_has_grants_id_and_name() -> None:
    assert set(PRESET_ROLE_GRANTS) == set(PresetRoleSlug)
    assert set(PRESET_ROLE_IDS) == set(PresetRoleSlug)
    assert set(PRESET_ROLE_NAMES) == set(PresetRoleSlug)
    for grants in PRESET_ROLE_GRANTS.values():
        validate_grants(grants)


def test_preset_ids_are_stable_uuid5_values() -> None:
    for slug in PresetRoleSlug:
        parsed = uuid.UUID(PRESET_ROLE_IDS[slug])
        assert parsed.version == 5
        assert PRESET_ROLE_IDS[slug] == preset_role_id(slug)
    assert len(set(PRESET_ROLE_IDS.values())) == len(PresetRoleSlug)
    # Pin one value so a namespace change is noticed.
    assert PRESET_ROLE_IDS[PresetRoleSlug.ADMIN] == preset_role_id(PresetRoleSlug.ADMIN)


def test_preset_grant_tables_match_the_plan() -> None:
    assert set(ADMIN_GRANTS) == set(Permission)
    assert dict(OPERATOR_GRANTS) == {
        Permission.DASHBOARD_READ: Scope.ALL,
        Permission.ACCOUNTS_READ: Scope.ALL,
        Permission.ACCOUNTS_WRITE: Scope.ALL,
        Permission.API_KEYS_READ: Scope.ALL,
        Permission.API_KEYS_WRITE: Scope.ALL,
        Permission.API_KEYS_ASSIGN: Scope.ALL,
        Permission.OPS_WRITE: Scope.ALL,
    }
    assert dict(MEMBER_GRANTS) == {
        Permission.DASHBOARD_READ: Scope.OWN,
        Permission.API_KEYS_READ: Scope.OWN,
        Permission.API_KEYS_WRITE: Scope.OWN,
    }
    assert set(MEMBER_GRANTS) <= OWN_SCOPED_PERMISSIONS
    assert dict(VIEWER_GRANTS) == {Permission.DASHBOARD_READ: Scope.ALL, Permission.ACCOUNTS_READ: Scope.ALL}
    assert GUEST_GRANTS == VIEWER_GRANTS
    # Only admin and operator hold the coarse write alias; member's own-scoped
    # key writes must not pass the generic write gate.
    assert legacy_permissions(OPERATOR_GRANTS) == legacy_permissions(ADMIN_GRANTS)
    assert "write" not in {p.value for p in legacy_permissions(MEMBER_GRANTS)}
    assert "write" not in {p.value for p in legacy_permissions(VIEWER_GRANTS)}


def test_legacy_role_table_still_maps_to_presets() -> None:
    assert ROLE_GRANTS[DashboardRole.ADMIN] is ADMIN_GRANTS
    assert ROLE_GRANTS[DashboardRole.GUEST] is GUEST_GRANTS


def test_guest_is_not_assignable() -> None:
    assert PresetRoleSlug.GUEST not in ASSIGNABLE_PRESET_ROLES
    assert ASSIGNABLE_PRESET_ROLES == {
        PresetRoleSlug.ADMIN,
        PresetRoleSlug.OPERATOR,
        PresetRoleSlug.MEMBER,
        PresetRoleSlug.VIEWER,
    }
    rows = {row["slug"]: row for row in preset_role_rows()}
    assert rows["guest"]["assignable_to_users"] is False
    assert all(rows[slug]["assignable_to_users"] is True for slug in ("admin", "operator", "member", "viewer"))
    assert all(row["kind"] == ROLE_KIND_PRESET for row in rows.values())


def test_preset_roles_resolve_from_code_not_rows() -> None:
    role = DashboardRoleRecord(id="r0", slug="operator", name="Operator", kind=ROLE_KIND_PRESET)
    role.grants = [DashboardRoleGrant(role_id="r0", permission="audit:read", scope="all")]
    assert resolve_role_grants(role) is OPERATOR_GRANTS


def test_custom_role_grants_ignore_unknown_vocabulary() -> None:
    rows = [
        DashboardRoleGrant(role_id="r1", permission="dashboard:read", scope="all"),
        DashboardRoleGrant(role_id="r1", permission="api_keys:write", scope="own"),
        DashboardRoleGrant(role_id="r1", permission="future:permission", scope="all"),
        DashboardRoleGrant(role_id="r1", permission="accounts:read", scope="everything"),
    ]
    grants = grants_from_rows(rows)
    assert dict(grants) == {Permission.DASHBOARD_READ: Scope.ALL, Permission.API_KEYS_WRITE: Scope.OWN}
    role = DashboardRoleRecord(id="r1", slug="my-role", name="My role", kind=ROLE_KIND_CUSTOM)
    role.grants = rows
    assert dict(resolve_role_grants(role)) == dict(grants)

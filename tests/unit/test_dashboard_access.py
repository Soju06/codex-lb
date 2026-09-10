from __future__ import annotations

import pytest

from app.core.auth.dashboard_access import (
    ADMIN_GRANTS,
    ADMIN_PERMISSIONS,
    GUEST_GRANTS,
    GUEST_PERMISSIONS,
    OPERATOR_GRANTS,
    OWN_SCOPED_PERMISSIONS,
    PERMISSION_IMPLIES,
    PRIVILEGED_PERMISSIONS,
    ROLE_GRANTS,
    VIEWER_GRANTS,
    DashboardPermission,
    DashboardPrincipal,
    DashboardRole,
    Permission,
    Scope,
    admin_principal,
    guest_principal,
    is_admin_level,
    legacy_permissions,
    scope_satisfies,
    totp_policy_applies,
    validate_grants,
)
from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.auth.dependencies import (
    DashboardPermissionDependency,
    PermissionRequirement,
    ensure_dashboard_permission,
    require_dashboard_permission,
)
from app.core.exceptions import DashboardPermissionError

pytestmark = pytest.mark.unit


def test_admin_preset_holds_every_permission_at_all_scope() -> None:
    assert set(ADMIN_GRANTS) == set(Permission)
    assert all(scope is Scope.ALL for scope in ADMIN_GRANTS.values())
    assert ROLE_GRANTS[DashboardRole.ADMIN] is ADMIN_GRANTS


def test_guest_preset_is_read_only_telemetry() -> None:
    assert dict(GUEST_GRANTS) == {
        Permission.DASHBOARD_READ: Scope.ALL,
        Permission.ACCOUNTS_READ: Scope.ALL,
    }
    assert ROLE_GRANTS[DashboardRole.GUEST] is GUEST_GRANTS


def test_legacy_aliases_are_derived_from_grants() -> None:
    assert ADMIN_PERMISSIONS == frozenset({DashboardPermission.READ, DashboardPermission.WRITE})
    assert GUEST_PERMISSIONS == frozenset({DashboardPermission.READ})

    operator_without_ops = dict(ADMIN_GRANTS)
    del operator_without_ops[Permission.OPS_WRITE]
    del operator_without_ops[Permission.SECURITY_WRITE]
    assert legacy_permissions(operator_without_ops) == frozenset({DashboardPermission.READ})

    own_key_writer = {
        Permission.DASHBOARD_READ: Scope.OWN,
        Permission.ACCOUNTS_WRITE: Scope.ALL,
        Permission.API_KEYS_WRITE: Scope.OWN,
        Permission.OPS_WRITE: Scope.ALL,
    }
    assert legacy_permissions(own_key_writer) == frozenset({DashboardPermission.READ})


def test_scope_ordering() -> None:
    assert scope_satisfies(Scope.ALL, Scope.ALL)
    assert scope_satisfies(Scope.ALL, Scope.OWN)
    assert scope_satisfies(Scope.OWN, Scope.OWN)
    assert not scope_satisfies(Scope.OWN, Scope.ALL)
    assert not scope_satisfies(None, Scope.OWN)


def test_validate_grants_rejects_own_scope_on_all_or_nothing_permission() -> None:
    with pytest.raises(ValueError, match="does not support the 'own' scope"):
        validate_grants({Permission.SECURITY_WRITE: Scope.OWN})
    for permission in OWN_SCOPED_PERMISSIONS:
        validate_grants({permission: Scope.OWN})


def test_validate_grants_enforces_dependency_rules() -> None:
    for permission, required in PERMISSION_IMPLIES.items():
        with pytest.raises(ValueError, match="requires"):
            validate_grants({permission: Scope.ALL})
        validate_grants({permission: Scope.ALL, **{dep: Scope.ALL for dep in required}})


def test_privileged_permissions_are_admin_only_in_presets() -> None:
    assert PRIVILEGED_PERMISSIONS <= set(ADMIN_GRANTS)
    assert not (PRIVILEGED_PERMISSIONS & set(GUEST_GRANTS))


def test_is_admin_level_follows_privileged_permissions() -> None:
    assert is_admin_level(ADMIN_GRANTS)
    assert is_admin_level({Permission.DASHBOARD_READ: Scope.ALL, Permission.AUDIT_READ: Scope.ALL})
    assert is_admin_level({Permission.OPS_WRITE: Scope.ALL, Permission.SECURITY_WRITE: Scope.ALL})
    assert not is_admin_level(OPERATOR_GRANTS)
    assert not is_admin_level(VIEWER_GRANTS)
    assert not is_admin_level(GUEST_GRANTS)
    assert not is_admin_level({})


def test_totp_policy_applies_combines_global_and_admin_role_toggles() -> None:
    for grants in (ADMIN_GRANTS, OPERATOR_GRANTS, VIEWER_GRANTS):
        assert totp_policy_applies(required_on_login=True, required_for_admin_role=False, grants=grants)
        assert not totp_policy_applies(required_on_login=False, required_for_admin_role=False, grants=grants)
    assert totp_policy_applies(required_on_login=False, required_for_admin_role=True, grants=ADMIN_GRANTS)
    assert totp_policy_applies(
        required_on_login=False,
        required_for_admin_role=True,
        grants={Permission.DASHBOARD_READ: Scope.ALL, Permission.USERS_MANAGE: Scope.ALL},
    )
    assert not totp_policy_applies(required_on_login=False, required_for_admin_role=True, grants=OPERATOR_GRANTS)
    assert not totp_policy_applies(required_on_login=False, required_for_admin_role=True, grants=VIEWER_GRANTS)


def test_principal_scope_and_has() -> None:
    principal = DashboardPrincipal(
        role=DashboardRole.ADMIN,
        permissions=frozenset({DashboardPermission.READ}),
        auth_mode=DashboardAuthMode.STANDARD,
        grants={Permission.API_KEYS_READ: Scope.OWN, Permission.DASHBOARD_READ: Scope.ALL},
    )
    assert principal.scope(Permission.API_KEYS_READ) is Scope.OWN
    assert principal.scope(Permission.AUDIT_READ) is None
    assert principal.has(Permission.API_KEYS_READ, minimum_scope=Scope.OWN)
    assert not principal.has(Permission.API_KEYS_READ)
    assert principal.has(Permission.DASHBOARD_READ)
    assert not principal.has(Permission.AUDIT_READ, minimum_scope=Scope.OWN)


def test_preset_principals_expose_grants_and_aliases() -> None:
    admin = admin_principal(auth_mode=DashboardAuthMode.STANDARD)
    guest = guest_principal()
    assert admin.grants is ADMIN_GRANTS
    assert admin.can(DashboardPermission.WRITE)
    assert admin.has(Permission.SECURITY_WRITE)
    assert guest.grants is GUEST_GRANTS
    assert not guest.can(DashboardPermission.WRITE)
    assert guest.has(Permission.ACCOUNTS_READ)
    assert not guest.has(Permission.CONVERSATIONS_READ)


def test_principals_are_hashable_and_reject_inconsistent_construction() -> None:
    admin = admin_principal(auth_mode=DashboardAuthMode.STANDARD)
    assert hash(admin) == hash(admin_principal(auth_mode=DashboardAuthMode.STANDARD))
    assert hash(guest_principal()) != hash(admin)
    assert {admin, guest_principal()} == {admin, guest_principal()}

    with pytest.raises(ValueError, match="do not match"):
        DashboardPrincipal(
            role=DashboardRole.ADMIN,
            permissions=ADMIN_PERMISSIONS,
            auth_mode=DashboardAuthMode.STANDARD,
            grants=GUEST_GRANTS,
        )
    with pytest.raises(ValueError, match="requires"):
        DashboardPrincipal(
            role=DashboardRole.ADMIN,
            permissions=frozenset(),
            auth_mode=DashboardAuthMode.STANDARD,
            grants={Permission.SECURITY_WRITE: Scope.ALL},
        )


def test_ensure_dashboard_permission_reports_missing_permission() -> None:
    guest = guest_principal()
    ensure_dashboard_permission(guest, Permission.DASHBOARD_READ)
    with pytest.raises(DashboardPermissionError) as exc_info:
        ensure_dashboard_permission(guest, Permission.ACCOUNTS_EXPORT)
    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "permission_required"
    assert exc_info.value.param == "accounts:export"


def test_require_dashboard_permission_is_cached_per_requirement() -> None:
    first = require_dashboard_permission(Permission.AUDIT_READ)
    second = require_dashboard_permission(Permission.AUDIT_READ)
    own = require_dashboard_permission(Permission.API_KEYS_READ, minimum_scope=Scope.OWN)
    assert first is second
    assert isinstance(first, DashboardPermissionDependency)
    assert first.requirement == PermissionRequirement(permission=Permission.AUDIT_READ, minimum_scope=Scope.ALL)
    assert own.requirement == PermissionRequirement(permission=Permission.API_KEYS_READ, minimum_scope=Scope.OWN)
    assert own is not first

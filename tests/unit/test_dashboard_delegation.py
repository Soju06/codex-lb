from __future__ import annotations

from types import MappingProxyType

import pytest

from app.core.auth.dashboard_access import (
    ADMIN_GRANTS,
    OPERATOR_GRANTS,
    OWN_SCOPED_PERMISSIONS,
    PERMISSION_IMPLIES,
    PRIVILEGED_PERMISSIONS,
    VIEWER_GRANTS,
    InsufficientDelegationError,
    Permission,
    Scope,
    assert_can_act_on,
    assert_can_delegate,
)
from app.modules.dashboard_roles.service import PERMISSION_DESCRIPTIONS, permission_descriptors
from app.modules.dashboard_users.credentials import CredentialRequiredError, assert_credential_remains

pytestmark = pytest.mark.unit


def test_admin_can_delegate_admin() -> None:
    assert_can_delegate(ADMIN_GRANTS, ADMIN_GRANTS)


def test_operator_cannot_delegate_admin() -> None:
    with pytest.raises(InsufficientDelegationError):
        assert_can_delegate(OPERATOR_GRANTS, ADMIN_GRANTS)


def test_operator_can_delegate_viewer() -> None:
    assert_can_delegate(OPERATOR_GRANTS, VIEWER_GRANTS)


def test_own_scope_is_below_all_scope() -> None:
    caller = MappingProxyType({Permission.API_KEYS_WRITE: Scope.OWN, Permission.DASHBOARD_READ: Scope.ALL})
    assert_can_delegate(caller, MappingProxyType({Permission.API_KEYS_WRITE: Scope.OWN}))
    assert_can_delegate(caller, MappingProxyType({Permission.DASHBOARD_READ: Scope.OWN}))
    with pytest.raises(InsufficientDelegationError):
        assert_can_delegate(caller, MappingProxyType({Permission.API_KEYS_WRITE: Scope.ALL}))


def test_missing_permission_is_below_any_scope() -> None:
    caller = MappingProxyType({Permission.DASHBOARD_READ: Scope.ALL})
    with pytest.raises(InsufficientDelegationError):
        assert_can_delegate(caller, MappingProxyType({Permission.API_KEYS_READ: Scope.OWN}))


def test_custom_grants_subset_rules() -> None:
    manager = MappingProxyType(
        {Permission.DASHBOARD_READ: Scope.ALL, Permission.ACCOUNTS_READ: Scope.ALL, Permission.USERS_MANAGE: Scope.ALL}
    )
    assert_can_delegate(manager, VIEWER_GRANTS)
    with pytest.raises(InsufficientDelegationError):
        assert_can_delegate(manager, OPERATOR_GRANTS)
    assert_can_delegate(manager, MappingProxyType({}))


def test_act_on_uses_the_same_subset_check() -> None:
    assert_can_act_on(ADMIN_GRANTS, OPERATOR_GRANTS)
    assert_can_act_on(OPERATOR_GRANTS, VIEWER_GRANTS)
    with pytest.raises(InsufficientDelegationError):
        assert_can_act_on(VIEWER_GRANTS, OPERATOR_GRANTS)
    with pytest.raises(InsufficientDelegationError):
        assert_can_act_on(OPERATOR_GRANTS, ADMIN_GRANTS)


def test_credential_must_remain_unless_solo_install_resets() -> None:
    assert_credential_remains(password_hash="$2b$x", identity_count=0, solo_install=False)
    assert_credential_remains(password_hash=None, identity_count=1, solo_install=False)
    assert_credential_remains(password_hash=None, identity_count=0, solo_install=True)
    with pytest.raises(CredentialRequiredError):
        assert_credential_remains(password_hash=None, identity_count=0, solo_install=False)


def test_every_permission_is_described_once() -> None:
    descriptors = permission_descriptors()
    assert [d.permission for d in descriptors] == list(Permission)
    assert set(PERMISSION_DESCRIPTIONS) == set(Permission)
    for descriptor in descriptors:
        assert descriptor.description.endswith(".")
        assert descriptor.own_supported is (descriptor.permission in OWN_SCOPED_PERMISSIONS)
        assert descriptor.privileged is (descriptor.permission in PRIVILEGED_PERMISSIONS)
        assert set(descriptor.implies) == set(PERMISSION_IMPLIES.get(descriptor.permission, frozenset()))

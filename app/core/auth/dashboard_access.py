from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from app.core.auth.dashboard_mode import DashboardAuthMode


class DashboardRole(StrEnum):
    ADMIN = "admin"
    GUEST = "guest"


class DashboardPermission(StrEnum):
    """Coarse wire-level permission aliases exposed to dashboard clients.

    ``read`` / ``write`` are derived from the fine-grained grants below and stay
    the only values the session response emits, so existing clients keep
    parsing the contract unchanged.
    """

    READ = "read"
    WRITE = "write"


class Permission(StrEnum):
    """Fine-grained dashboard permissions (``<resource>:<action>``)."""

    DASHBOARD_READ = "dashboard:read"
    ACCOUNTS_READ = "accounts:read"
    ACCOUNTS_WRITE = "accounts:write"
    ACCOUNTS_EXPORT = "accounts:export"
    API_KEYS_READ = "api_keys:read"
    API_KEYS_WRITE = "api_keys:write"
    API_KEYS_ASSIGN = "api_keys:assign"
    OPS_WRITE = "ops:write"
    SECURITY_WRITE = "security:write"
    USERS_MANAGE = "users:manage"
    ROLES_MANAGE = "roles:manage"
    CONVERSATIONS_READ = "conversations:read"
    AUDIT_READ = "audit:read"


class Scope(StrEnum):
    """How far a granted permission reaches.

    ``all`` covers every resource; ``own`` covers only resources owned by the
    principal. ``all`` satisfies any requirement, ``own`` satisfies only an
    ``own`` requirement.
    """

    ALL = "all"
    OWN = "own"


_SCOPE_RANK: Mapping[Scope, int] = MappingProxyType({Scope.OWN: 1, Scope.ALL: 2})

Grants = Mapping[Permission, Scope]


def _grants(mapping: dict[Permission, Scope]) -> Grants:
    return MappingProxyType(dict(mapping))


#: Permissions that may be granted with ``own`` scope. Every other permission is
#: all-or-nothing.
OWN_SCOPED_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.DASHBOARD_READ,
        Permission.API_KEYS_READ,
        Permission.API_KEYS_WRITE,
    }
)

#: Dependency rules: granting the key requires every listed permission at
#: ``all`` scope. Enforced on the built-in role table at import time and on any
#: future role editor.
PERMISSION_IMPLIES: Mapping[Permission, frozenset[Permission]] = MappingProxyType(
    {
        Permission.API_KEYS_ASSIGN: frozenset({Permission.API_KEYS_WRITE}),
        Permission.ACCOUNTS_EXPORT: frozenset({Permission.ACCOUNTS_READ}),
        Permission.SECURITY_WRITE: frozenset({Permission.OPS_WRITE}),
    }
)

#: Permissions whose holders count as "admin-grade" for security policy
#: (for example a future "TOTP required for admins" option).
PRIVILEGED_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.SECURITY_WRITE,
        Permission.USERS_MANAGE,
        Permission.ROLES_MANAGE,
        Permission.ACCOUNTS_EXPORT,
        Permission.CONVERSATIONS_READ,
        Permission.AUDIT_READ,
    }
)

#: The legacy ``write`` alias means "may perform every mutation the generic
#: write gate protects", which today spans accounts, API keys, and operations.
_WRITE_ALIAS_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.ACCOUNTS_WRITE,
        Permission.API_KEYS_WRITE,
        Permission.OPS_WRITE,
    }
)

ADMIN_GRANTS: Grants = _grants({permission: Scope.ALL for permission in Permission})
GUEST_GRANTS: Grants = _grants(
    {
        Permission.DASHBOARD_READ: Scope.ALL,
        Permission.ACCOUNTS_READ: Scope.ALL,
    }
)

ROLE_GRANTS: Mapping[DashboardRole, Grants] = MappingProxyType(
    {
        DashboardRole.ADMIN: ADMIN_GRANTS,
        DashboardRole.GUEST: GUEST_GRANTS,
    }
)


def scope_satisfies(granted: Scope | None, required: Scope) -> bool:
    if granted is None:
        return False
    return _SCOPE_RANK[granted] >= _SCOPE_RANK[required]


def legacy_permissions(grants: Grants) -> frozenset[DashboardPermission]:
    """Derive the coarse ``read`` / ``write`` aliases from fine-grained grants."""

    aliases: set[DashboardPermission] = set()
    if Permission.DASHBOARD_READ in grants:
        aliases.add(DashboardPermission.READ)
    if all(grants.get(permission) is Scope.ALL for permission in _WRITE_ALIAS_PERMISSIONS):
        aliases.add(DashboardPermission.WRITE)
    return frozenset(aliases)


def validate_grants(grants: Grants) -> None:
    """Fail fast when a grant table violates the vocabulary rules."""

    for permission, scope in grants.items():
        if scope is Scope.OWN and permission not in OWN_SCOPED_PERMISSIONS:
            raise ValueError(f"{permission.value} does not support the 'own' scope")
    for permission, required in PERMISSION_IMPLIES.items():
        if permission not in grants:
            continue
        missing = [dep.value for dep in sorted(required) if grants.get(dep) is not Scope.ALL]
        if missing:
            raise ValueError(f"{permission.value} requires {', '.join(missing)} at 'all' scope")


for _role_grants in ROLE_GRANTS.values():
    validate_grants(_role_grants)


@dataclass(frozen=True, slots=True)
class DashboardPrincipal:
    """An authenticated dashboard caller.

    ``grants`` is the source of truth for authorization; ``permissions`` holds
    the coarse aliases derived from it and is validated for consistency so the
    two can never drift. ``grants`` is excluded from hashing because mappings
    are unhashable; equality still compares it.
    """

    role: DashboardRole
    permissions: frozenset[DashboardPermission]
    auth_mode: DashboardAuthMode
    actor: str | None = None
    grants: Grants = field(kw_only=True, hash=False)

    def __post_init__(self) -> None:
        validate_grants(self.grants)
        expected = legacy_permissions(self.grants)
        if self.permissions != expected:
            raise ValueError(
                f"permissions {sorted(self.permissions)} do not match grants-derived aliases {sorted(expected)}"
            )

    def can(self, permission: DashboardPermission) -> bool:
        return permission in self.permissions

    def scope(self, permission: Permission) -> Scope | None:
        return self.grants.get(permission)

    def has(self, permission: Permission, *, minimum_scope: Scope = Scope.ALL) -> bool:
        return scope_satisfies(self.grants.get(permission), minimum_scope)


ADMIN_PERMISSIONS = legacy_permissions(ADMIN_GRANTS)
GUEST_PERMISSIONS = legacy_permissions(GUEST_GRANTS)


def admin_principal(*, auth_mode: DashboardAuthMode, actor: str | None = None) -> DashboardPrincipal:
    return DashboardPrincipal(
        role=DashboardRole.ADMIN,
        permissions=ADMIN_PERMISSIONS,
        auth_mode=auth_mode,
        actor=actor,
        grants=ADMIN_GRANTS,
    )


def guest_principal() -> DashboardPrincipal:
    return DashboardPrincipal(
        role=DashboardRole.GUEST,
        permissions=GUEST_PERMISSIONS,
        auth_mode=DashboardAuthMode.STANDARD,
        grants=GUEST_GRANTS,
    )

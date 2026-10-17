from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING

from app.core.auth.dashboard_mode import DashboardAuthMode

if TYPE_CHECKING:
    from app.db.models import DashboardUser


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

#: Permissions whose mutations additionally require a recent re-verification
#: of the caller ("step-up", PLAN §5 H5): a stolen cookie alone must not be
#: enough to change who may sign in or to export account credentials.
STEP_UP_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.SECURITY_WRITE,
        Permission.USERS_MANAGE,
        Permission.ROLES_MANAGE,
        Permission.ACCOUNTS_EXPORT,
    }
)
assert STEP_UP_PERMISSIONS <= PRIVILEGED_PERMISSIONS

#: The legacy ``write`` alias means "may perform every mutation the generic
#: write gate protects", which today spans accounts, API keys, and operations.
_WRITE_ALIAS_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.ACCOUNTS_WRITE,
        Permission.API_KEYS_WRITE,
        Permission.OPS_WRITE,
    }
)


class RoleKind(StrEnum):
    """``dashboard_roles.kind``: presets resolve from code, custom roles from grant rows."""

    PRESET = "preset"
    CUSTOM = "custom"


class PresetRoleSlug(StrEnum):
    """The built-in roles. Their grants live in code, never in the database."""

    ADMIN = "admin"
    OPERATOR = "operator"
    MEMBER = "member"
    VIEWER = "viewer"
    GUEST = "guest"


#: Stable identifiers for the preset role rows (``dashboard_roles``), derived
#: from the slug so every install and every replica agrees without coordination.
_PRESET_ROLE_ID_NAMESPACE = uuid.UUID("6f1c0e4e-2b4a-4c1e-9c3b-7a5d2e8f0a11")


def preset_role_id(slug: PresetRoleSlug) -> str:
    """Deterministic id of a preset role row (UUIDv5 of the slug)."""

    return str(uuid.uuid5(_PRESET_ROLE_ID_NAMESPACE, f"codex-lb:dashboard-role:{slug.value}"))


PRESET_ROLE_IDS: Mapping[PresetRoleSlug, str] = MappingProxyType(
    {slug: preset_role_id(slug) for slug in PresetRoleSlug}
)

PRESET_ROLE_NAMES: Mapping[PresetRoleSlug, str] = MappingProxyType(
    {
        PresetRoleSlug.ADMIN: "Admin",
        PresetRoleSlug.OPERATOR: "Operator",
        PresetRoleSlug.MEMBER: "Member",
        PresetRoleSlug.VIEWER: "Viewer",
        PresetRoleSlug.GUEST: "Guest",
    }
)

ADMIN_GRANTS: Grants = _grants({permission: Scope.ALL for permission in Permission})
OPERATOR_GRANTS: Grants = _grants(
    {
        Permission.DASHBOARD_READ: Scope.ALL,
        Permission.ACCOUNTS_READ: Scope.ALL,
        Permission.ACCOUNTS_WRITE: Scope.ALL,
        Permission.API_KEYS_READ: Scope.ALL,
        Permission.API_KEYS_WRITE: Scope.ALL,
        Permission.API_KEYS_ASSIGN: Scope.ALL,
        Permission.OPS_WRITE: Scope.ALL,
    }
)
MEMBER_GRANTS: Grants = _grants(
    {
        Permission.DASHBOARD_READ: Scope.OWN,
        Permission.API_KEYS_READ: Scope.OWN,
        Permission.API_KEYS_WRITE: Scope.OWN,
    }
)
VIEWER_GRANTS: Grants = _grants(
    {
        Permission.DASHBOARD_READ: Scope.ALL,
        Permission.ACCOUNTS_READ: Scope.ALL,
    }
)
GUEST_GRANTS: Grants = VIEWER_GRANTS

#: Grants of every preset role. The database stores preset *rows* (for foreign
#: keys and listings) but never their grants: this table is the single truth,
#: so an upgrade that adds a permission updates every preset by definition and
#: replicas of different versions can never disagree about what ``admin`` means.
PRESET_ROLE_GRANTS: Mapping[PresetRoleSlug, Grants] = MappingProxyType(
    {
        PresetRoleSlug.ADMIN: ADMIN_GRANTS,
        PresetRoleSlug.OPERATOR: OPERATOR_GRANTS,
        PresetRoleSlug.MEMBER: MEMBER_GRANTS,
        PresetRoleSlug.VIEWER: VIEWER_GRANTS,
        PresetRoleSlug.GUEST: GUEST_GRANTS,
    }
)

#: Presets that may be assigned to a user account. Guest is the anonymous
#: shared read-only session and is never a user's role. Member (own-scoped
#: grants only) becomes assignable once own-scoped views ship; until then the
#: session dependency refuses roles without ``dashboard:read`` at ``all``.
ASSIGNABLE_PRESET_ROLES: frozenset[PresetRoleSlug] = frozenset(PresetRoleSlug) - {
    PresetRoleSlug.GUEST,
    PresetRoleSlug.MEMBER,
}

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


class InsufficientDelegationError(Exception):
    """The caller's grants do not cover the grants of the role it wants to assign or act on."""


def _grants_cover(caller: Grants, target: Grants) -> bool:
    return all(scope_satisfies(caller.get(permission), scope) for permission, scope in target.items())


def assert_can_delegate(caller: Grants, target: Grants) -> None:
    """Assigning a role: every (permission, scope) of ``target`` must be at or
    below the caller's own grant for that permission (``own`` < ``all``; a
    permission the caller lacks is below both). It follows that only an admin
    preset holder can grant the admin preset.
    """

    if not _grants_cover(caller, target):
        raise InsufficientDelegationError("The role holds permissions the caller does not have")


def assert_can_act_on(caller: Grants, target_user_grants: Grants) -> None:
    """Acting on an account (disable, delete, reset, revoke, re-invite): the
    same subset check applied to the grants of the account's current role."""

    if not _grants_cover(caller, target_user_grants):
        raise InsufficientDelegationError("The account holds permissions the caller does not have")


def is_admin_level(grants: Grants) -> bool:
    """Whether a grant table counts as "admin-level" for security policy.

    The admin preset qualifies by definition; a custom role qualifies as soon as
    it holds one :data:`PRIVILEGED_PERMISSIONS` entry. Operator, member, viewer
    and guest never do.
    """

    return not PRIVILEGED_PERMISSIONS.isdisjoint(grants)


def totp_policy_applies(*, required_on_login: bool, required_for_admin_role: bool, grants: Grants) -> bool:
    """Whether the install's TOTP policy binds an account holding ``grants``.

    The global toggle binds every password account; the admin-role toggle binds
    admin-level accounts only. One predicate serves the session dependency and
    the session response so the two can never disagree.
    """

    return required_on_login or (required_for_admin_role and is_admin_level(grants))


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


for _role_grants in PRESET_ROLE_GRANTS.values():
    validate_grants(_role_grants)


@dataclass(frozen=True, slots=True)
class DashboardPrincipal:
    """An authenticated dashboard caller.

    ``grants`` is the source of truth for authorization; ``permissions`` holds
    the coarse aliases derived from it and is validated for consistency so the
    two can never drift. ``grants`` is excluded from hashing because mappings
    are unhashable; equality still compares it.

    ``role`` stays the coarse wire value (``admin`` for every user account and
    for the implicit/trusted-header/disabled admin, ``guest`` for guests);
    ``role_slug`` carries the account's actual role when the principal is a
    user. ``user_id`` is ``None`` for principals without a user row.
    ``totp_enrollment_required`` marks a user whose install requires TOTP but
    who has not enrolled yet: only the dashboard-auth self-service routes may
    serve such a principal. ``step_up_verified_at`` is the unix time the
    session cookie last recorded a step-up re-verification (``su``), if any.
    """

    role: DashboardRole
    permissions: frozenset[DashboardPermission]
    auth_mode: DashboardAuthMode
    actor: str | None = None
    grants: Grants = field(kw_only=True, hash=False)
    user_id: str | None = field(default=None, kw_only=True)
    username: str | None = field(default=None, kw_only=True)
    role_slug: str | None = field(default=None, kw_only=True)
    auth_method: str | None = field(default=None, kw_only=True)
    totp_enrollment_required: bool = field(default=False, kw_only=True)
    step_up_verified_at: int | None = field(default=None, kw_only=True)

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


def permission_strings(grants: Grants) -> list[str]:
    """Wire form of a grant table: the legacy aliases first, then ``<permission>:<scope>``.

    Clients that predate fine-grained permissions only look for ``write``; newer
    clients read the scoped entries.
    """

    aliases = sorted(alias.value for alias in legacy_permissions(grants))
    scoped = sorted(f"{permission.value}:{scope.value}" for permission, scope in grants.items())
    return aliases + scoped


def admin_principal(
    *,
    auth_mode: DashboardAuthMode,
    actor: str | None = None,
    auth_method: str | None = None,
) -> DashboardPrincipal:
    """The implicit admin: no user row (local passwordless install, trusted header, disabled auth)."""

    return DashboardPrincipal(
        role=DashboardRole.ADMIN,
        permissions=ADMIN_PERMISSIONS,
        auth_mode=auth_mode,
        actor=actor,
        grants=ADMIN_GRANTS,
        auth_method=auth_method,
    )


def user_principal(
    user: DashboardUser,
    grants: Grants,
    *,
    auth_method: str | None,
    totp_enrollment_required: bool = False,
    auth_mode: DashboardAuthMode = DashboardAuthMode.STANDARD,
    step_up_verified_at: int | None = None,
) -> DashboardPrincipal:
    """A signed-in user account. ``grants`` is the resolved grant table of ``user.role``."""

    return DashboardPrincipal(
        role=DashboardRole.ADMIN,
        permissions=legacy_permissions(grants),
        auth_mode=auth_mode,
        actor=user.username,
        grants=grants,
        user_id=user.id,
        username=user.username,
        role_slug=user.role.slug,
        auth_method=auth_method,
        totp_enrollment_required=totp_enrollment_required,
        step_up_verified_at=step_up_verified_at,
    )


def guest_principal() -> DashboardPrincipal:
    return DashboardPrincipal(
        role=DashboardRole.GUEST,
        permissions=GUEST_PERMISSIONS,
        auth_mode=DashboardAuthMode.STANDARD,
        grants=GUEST_GRANTS,
    )

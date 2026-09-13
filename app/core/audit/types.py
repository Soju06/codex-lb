from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.core.auth.dashboard_access import DashboardPrincipal, DashboardRole, PresetRoleSlug

type AuditDetailScalar = str | int | float | bool | None
type AuditDetailValue = AuditDetailScalar | Sequence[AuditDetailScalar]
type AuditDetails = Mapping[str, AuditDetailValue]


class AuditSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AuditAuthMethod(StrEnum):
    """How the acting party authenticated. Stored as the plain string."""

    PASSWORD = "password"
    TOTP = "totp"
    GUEST = "guest"
    TRUSTED_HEADER = "trusted_header"
    DISABLED = "disabled"
    LOCAL_BOOTSTRAP = "local_bootstrap"
    API_KEY = "api_key"
    OIDC = "oidc"
    SCIM = "scim"
    CLI = "cli"


@dataclass(frozen=True, slots=True)
class AuditActor:
    """Snapshot of who acted, taken at write time.

    The user id is a plain string, not a foreign key: an audit row must outlive
    the account it names. ``user_id`` is ``None`` for principals without an
    account row (implicit local admin, trusted header, disabled auth, guest);
    a trusted-header admin still records the proxy-asserted username.
    """

    user_id: str | None
    username: str | None
    role_slug: str | None
    auth_method: str | None

    @classmethod
    def from_principal(cls, principal: DashboardPrincipal) -> AuditActor:
        if principal.user_id is not None:
            return cls(
                user_id=principal.user_id,
                username=principal.username,
                role_slug=principal.role_slug,
                auth_method=principal.auth_method,
            )
        if principal.role is DashboardRole.GUEST:
            return cls(
                user_id=None,
                username=None,
                role_slug=PresetRoleSlug.GUEST.value,
                auth_method=AuditAuthMethod.GUEST.value,
            )
        return cls(
            user_id=None,
            username=principal.actor,
            role_slug=PresetRoleSlug.ADMIN.value,
            auth_method=principal.auth_method,
        )


@dataclass(frozen=True, slots=True)
class AuditTarget:
    """What was acted on: a stable type name plus the object's identifier."""

    type: str
    id: str


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One audit record as handed to every sink. ``details`` is already sanitised."""

    action: str
    timestamp: datetime
    actor: AuditActor | None
    actor_ip: str | None
    target: AuditTarget | None
    severity: AuditSeverity
    details: dict[str, AuditDetailValue] | None
    request_id: str | None

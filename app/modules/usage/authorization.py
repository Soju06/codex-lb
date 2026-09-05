from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from app.core.usage.account_limits import AccountUsageLimitState, evaluate_standard_usage_limit
from app.db.models import AccountStatus
from app.modules.usage.repository import AccountUsageLimitSnapshot, UsageRepository

logger = logging.getLogger(__name__)


class OwnerAuthorizationKind(StrEnum):
    ALLOWED = "allowed"
    USAGE_POLICY_BLOCKED = "usage_policy_blocked"
    OWNER_UNAVAILABLE = "owner_unavailable"
    AUTHORIZATION_FAILED = "authorization_failed"


@dataclass(frozen=True, slots=True)
class OwnerAuthorization:
    """An owner decision, not an optional usage measurement.

    Only ALLOWED grants permission. Transport owners map the other outcomes to
    their own continuity, policy, and local infrastructure error contracts.
    Cancellation is not a decision and must propagate through resource cleanup.
    """

    kind: OwnerAuthorizationKind
    usage_limit_state: AccountUsageLimitState | None = None
    owner_status: AccountStatus | None = None
    snapshot: AccountUsageLimitSnapshot | None = None

    @property
    def allowed(self) -> bool:
        return self.kind is OwnerAuthorizationKind.ALLOWED


def authorize_usage_snapshot(
    snapshot: AccountUsageLimitSnapshot | None,
    *,
    refresh_interval_seconds: int,
    require_active: bool = False,
) -> OwnerAuthorization:
    """Apply the shared owner contract to one authoritative database snapshot.

    Warmups require an active owner. Normal routing handles transient upstream
    quota states separately, including recovery and additional-quota routing.
    Administrative unavailability is never overridden by a disabled policy.
    """
    status = snapshot.status if snapshot is not None else None
    if (
        snapshot is None
        or status in {AccountStatus.PAUSED, AccountStatus.DEACTIVATED, AccountStatus.REAUTH_REQUIRED}
        or (require_active and status is not AccountStatus.ACTIVE)
    ):
        return OwnerAuthorization(OwnerAuthorizationKind.OWNER_UNAVAILABLE, owner_status=status, snapshot=snapshot)
    state = evaluate_standard_usage_limit(
        enabled=snapshot.enabled,
        limit_percent=snapshot.limit_percent,
        plan_type=snapshot.plan_type,
        primary=snapshot.primary,
        secondary=snapshot.secondary,
        monthly=snapshot.monthly,
        refresh_interval_seconds=refresh_interval_seconds,
    )
    return OwnerAuthorization(
        OwnerAuthorizationKind.USAGE_POLICY_BLOCKED if state.blocks_account_use else OwnerAuthorizationKind.ALLOWED,
        usage_limit_state=state,
        owner_status=status,
        snapshot=snapshot,
    )


async def load_owner_authorization(
    usage: UsageRepository,
    account_id: str,
    *,
    refresh_interval_seconds: int,
    require_active: bool = False,
) -> OwnerAuthorization:
    """Read one authoritative snapshot; cancellation remains caller-owned."""
    try:
        snapshot = await usage.account_usage_limit_snapshot(account_id)
        return authorize_usage_snapshot(
            snapshot, refresh_interval_seconds=refresh_interval_seconds, require_active=require_active
        )
    except Exception:
        logger.warning("Fresh owner authorization failed account_id=%s", account_id, exc_info=True)
        return OwnerAuthorization(OwnerAuthorizationKind.AUTHORIZATION_FAILED)

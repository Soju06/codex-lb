from __future__ import annotations

import time
from dataclasses import replace
from datetime import timezone

from app.db.models import Account, AccountStatus, AdditionalUsageHistory, UsageHistory
from app.modules.proxy._load_balancer.types import RuntimeState

_RESET_RECOVERY_WRITER_STARTED_AT = time.time()


def _runtime_quota_recovered(
    account: Account,
    runtime: RuntimeState,
    entries: tuple[UsageHistory | AdditionalUsageHistory | None, ...],
) -> bool:
    from app.modules.proxy.load_balancer import _usage_entry_is_recent_enough

    if account.status != AccountStatus.ACTIVE or account.blocked_at is not None or runtime.blocked_at is None:
        return False
    observed_after = max(
        runtime.blocked_at,
        runtime.last_error_at or 0,
        _RESET_RECOVERY_WRITER_STARTED_AT,
    )
    measured = [entry for entry in entries if entry is not None]
    return bool(measured) and all(
        entry.used_percent is not None
        and 0 <= entry.used_percent < 100
        and entry.reset_at is not None
        and entry.reset_at > time.time()
        and entry.recorded_at is not None
        and entry.recorded_at.replace(tzinfo=timezone.utc).timestamp() > observed_after
        and _usage_entry_is_recent_enough(entry.recorded_at)
        for entry in measured
    )


def standard_quota_status_from_account(
    account: Account,
    *,
    primary_entry: UsageHistory | None,
    secondary_entry: UsageHistory | None,
    monthly_entry: UsageHistory | None = None,
    runtime: RuntimeState | None = None,
) -> AccountStatus:
    """Project the same persisted quota/cooldown eligibility used by routing."""
    from app.modules.proxy.load_balancer import _select_long_window_entry, _state_from_account

    state = _state_from_account(
        account=account,
        primary_entry=primary_entry,
        secondary_entry=_select_long_window_entry(
            account=account,
            secondary_entry=secondary_entry,
            monthly_entry=monthly_entry,
        ),
        runtime=replace(runtime) if runtime is not None else RuntimeState(),
    )
    if (
        state.status == AccountStatus.ACTIVE
        and runtime is not None
        and runtime.blocked_at is not None
        and state.cooldown_until is not None
        and state.cooldown_until > time.time()
    ):
        return AccountStatus.RATE_LIMITED
    return state.status

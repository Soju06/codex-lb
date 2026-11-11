from __future__ import annotations

from datetime import timezone

from app.core import usage as usage_core
from app.core.balancer import AccountState
from app.db.models import Account, AccountStatus, AdditionalUsageHistory, UsageHistory
from app.modules.proxy._load_balancer.types import RuntimeState


def _runtime_quota_recovered(
    account: Account,
    runtime: RuntimeState,
    entries: tuple[UsageHistory | AdditionalUsageHistory | None, ...],
    *,
    now: float,
) -> bool:
    from app.modules.proxy.load_balancer import _usage_entry_is_recent_enough

    if account.status != AccountStatus.ACTIVE or account.blocked_at is not None or runtime.blocked_at is None:
        return False
    observed_after = max(
        runtime.blocked_at,
        runtime.last_error_at or 0,
    )
    measured = [entry for entry in entries if entry is not None]
    return bool(measured) and all(
        entry.used_percent is not None
        and 0 <= entry.used_percent < 100
        and entry.reset_at is not None
        and entry.reset_at > now
        and entry.recorded_at is not None
        and entry.recorded_at.replace(tzinfo=timezone.utc).timestamp() > observed_after
        and _usage_entry_is_recent_enough(entry.recorded_at, now=now)
        for entry in measured
    )


def recovered_runtime_holds(
    account: Account,
    runtime: RuntimeState,
    primary: UsageHistory | AdditionalUsageHistory | None,
    primary_window_minutes: int | None,
    secondary: UsageHistory | AdditionalUsageHistory | None,
    *,
    now: float,
) -> tuple[float | None, float | None, float | None]:
    """Retire proven old quota holds without replacing leases or non-quota errors."""
    if primary_window_minutes is None or usage_core.capacity_for_plan(account.plan_type, "primary") == 0.0:
        primary = None
    if not _runtime_quota_recovered(account, runtime, (primary, secondary), now=now):
        return runtime.reset_at, runtime.blocked_at, runtime.cooldown_until
    if runtime.quota_error_count:
        runtime.error_count = max(0, runtime.error_count - runtime.quota_error_count)
        runtime.last_error_at = runtime.last_non_quota_error_at if runtime.error_count else None
        runtime.quota_error_count = 0
        runtime.version += 1
        runtime.health_version += 1
    return None, None, None


def record_non_quota_error(runtime: RuntimeState, state: AccountState, minimum: int) -> None:
    runtime.last_non_quota_error_at = state.last_error_at
    runtime.quota_error_count = min(runtime.quota_error_count, max(0, state.error_count - minimum))


def preserve_non_quota_error(runtime: RuntimeState, state: AccountState) -> None:
    if state.error_count > runtime.quota_error_count and runtime.last_non_quota_error_at is None:
        runtime.last_non_quota_error_at = state.last_error_at


def clear_quota_error_history(runtime: RuntimeState) -> None:
    runtime.quota_error_count = 0
    runtime.last_non_quota_error_at = None


def sync_quota_error_history(runtime: RuntimeState, state: AccountState) -> None:
    if state.error_count == 0:
        clear_quota_error_history(runtime)
    elif runtime.blocked_at is not None and state.blocked_at is None:
        runtime.quota_error_count = 0


def clear_error_history(runtime: RuntimeState) -> None:
    runtime.error_count = 0
    runtime.last_error_at = None
    clear_quota_error_history(runtime)

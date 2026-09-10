from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.balancer import capacity_for_routing_plan
from app.core.config import settings as config_settings
from app.db.models import Account, AccountStatus, AdditionalUsageHistory, UsageHistory

_UsageWindowEntry = UsageHistory | AdditionalUsageHistory
_DEFAULT_USAGE_REFRESH_INTERVAL_SECONDS = 60


def _rate_limited_freshness_entry(
    *,
    account: Account,
    primary_entry: _UsageWindowEntry | None,
    long_window_entry: _UsageWindowEntry | None,
    now: float,
) -> _UsageWindowEntry | None:
    if (
        long_window_entry is not None
        and long_window_entry.reset_at is not None
        and long_window_entry.reset_at <= int(now)
    ):
        long_window_entry = None
    if (
        long_window_entry is not None
        and long_window_entry.window == "monthly"
        and capacity_for_routing_plan(account.plan_type, AccountStatus.RATE_LIMITED, "monthly") is None
    ):
        long_window_entry = None
    if capacity_for_routing_plan(account.plan_type, AccountStatus.RATE_LIMITED, "primary") == 0.0:
        # A synthetic primary row is not an applicable quota window for these
        # plans, so it cannot prove that the usable long window refreshed after
        # the block.
        return long_window_entry
    if long_window_entry is not None and long_window_entry.window == "monthly":
        return long_window_entry
    if primary_entry is None:
        return long_window_entry
    # A newer long-window row can replace primary evidence only after the
    # primary reset expires. Availability is evaluated by the caller's
    # credit-aware, plan-normalized recovery predicate.
    primary_window_expired = primary_entry.reset_at is not None and float(primary_entry.reset_at) <= now
    if (
        primary_window_expired
        and long_window_entry is not None
        and long_window_entry.recorded_at > primary_entry.recorded_at
    ):
        return long_window_entry
    return primary_entry


def _usage_entry_recorded_after_block(entry: _UsageWindowEntry | None, blocked_at: float) -> bool:
    if entry is None or entry.recorded_at is None:
        return False
    recorded_at = entry.recorded_at
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=timezone.utc)
    # Persistence truncates block timestamps to whole seconds. A sample
    # within that same second cannot prove it was captured after the block.
    return int(recorded_at.timestamp()) > int(blocked_at)


def _extract_credit_status(
    *entries: _UsageWindowEntry | None,
    recorded_after: float | None = None,
) -> tuple[bool | None, bool | None, float | None]:
    credit_entries: list[UsageHistory] = [
        entry
        for entry in entries
        if isinstance(entry, UsageHistory)
        and (recorded_after is None or _usage_entry_recorded_after_block(entry, recorded_after))
        and not (entry.credits_has is None and entry.credits_unlimited is None and entry.credits_balance is None)
    ]
    if not credit_entries:
        return None, None, None
    entry = max(
        credit_entries,
        key=lambda item: item.recorded_at if item.recorded_at is not None else datetime.min,
    )
    return entry.credits_has, entry.credits_unlimited, entry.credits_balance


def _usage_entry_is_recent_enough(recorded_at: datetime | None, *, now: float) -> bool:
    if recorded_at is None:
        return False
    current_time = datetime.fromtimestamp(now, tz=timezone.utc)
    interval_seconds = max(_usage_refresh_interval_seconds() * 2, 180)
    recorded_time = recorded_at if recorded_at.tzinfo is not None else recorded_at.replace(tzinfo=timezone.utc)
    return recorded_time >= current_time - timedelta(seconds=interval_seconds)


def _usage_refresh_interval_seconds() -> int:
    settings = config_settings.get_settings()
    return int(getattr(settings, "usage_refresh_interval_seconds", _DEFAULT_USAGE_REFRESH_INTERVAL_SECONDS))

from __future__ import annotations

from app.core import usage as usage_core
from app.db.models import Account, UsageHistory
from app.modules.usage.mappers import usage_history_to_window_row


def reached_usage_cap_resets(
    account: Account,
    primary: UsageHistory | None,
    secondary: UsageHistory | None,
    *,
    now: float,
) -> tuple[int | None, ...]:
    """Reset deadlines of reached standard caps; None means no known deadline."""
    if account.usage_cap_5h_percent is None and account.usage_cap_weekly_percent is None:
        return ()
    current = now
    if primary is not None and usage_core.should_use_weekly_primary(
        usage_history_to_window_row(primary),
        usage_history_to_window_row(secondary) if secondary is not None else None,
    ):
        secondary, primary = primary, None
    if (usage_core.capacity_for_plan(account.plan_type, "primary") or 0) <= 0:
        primary = None
    if (
        primary is not None
        and secondary is not None
        and (secondary.recorded_at - primary.recorded_at).total_seconds() > usage_core.SIBLING_FETCH_MARGIN_SECONDS
    ):
        primary = None
    return tuple(
        entry.reset_at
        for entry, cap, minutes in (
            (primary, account.usage_cap_5h_percent, usage_core.DEFAULT_WINDOW_MINUTES_PRIMARY),
            (secondary, account.usage_cap_weekly_percent, usage_core.DEFAULT_WINDOW_MINUTES_SECONDARY),
        )
        if entry is not None
        and cap is not None
        and entry.window_minutes == minutes
        and entry.used_percent is not None
        and entry.used_percent >= cap
        and (entry.reset_at is None or entry.reset_at > current)
    )

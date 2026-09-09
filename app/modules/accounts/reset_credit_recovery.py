"""Assess fresh standard quota without treating model-only limits as exhaustion."""

from __future__ import annotations

from app.core import usage as usage_core
from app.core.usage.quota import apply_usage_quota
from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import Account, AccountStatus
from app.modules.usage.repository import UsageRepository
from app.modules.usage.updater import UsageUpdater


async def refresh_standard_capacity(
    account: Account,
    updater: UsageUpdater,
    usage_repo: UsageRepository,
) -> bool | None:
    started = utcnow()
    if not await updater.force_refresh(account):
        return None
    primary = await usage_repo.latest_entry_for_account(account.id, window="primary")
    secondary = await usage_repo.latest_entry_for_account(account.id, window="secondary")
    monthly = await usage_repo.latest_entry_for_account(account.id, window="monthly")
    if monthly is not None and usage_core.capacity_for_plan(account.plan_type, "monthly") is not None:
        secondary = monthly
    if primary is not None and usage_core.should_use_weekly_primary(primary, secondary):
        secondary, primary = primary, None
    if usage_core.capacity_for_plan(account.plan_type, "primary") == 0:
        primary = None
    if secondary is None:
        return None
    if any(to_utc_naive(row.recorded_at) < started for row in (primary, secondary) if row is not None):
        return None
    credits = next(
        (
            row
            for row in (primary, secondary)
            if row is not None
            and any(value is not None for value in (row.credits_has, row.credits_unlimited, row.credits_balance))
        ),
        secondary,
    )
    status, _, _ = apply_usage_quota(
        status=AccountStatus.ACTIVE,
        primary_used=primary.used_percent if primary else None,
        primary_reset=primary.reset_at if primary else None,
        primary_window_minutes=primary.window_minutes if primary else None,
        runtime_reset=None,
        secondary_used=secondary.used_percent,
        secondary_reset=secondary.reset_at,
        credits_has=credits.credits_has,
        credits_unlimited=credits.credits_unlimited,
        credits_balance=credits.credits_balance,
    )
    return status == AccountStatus.ACTIVE

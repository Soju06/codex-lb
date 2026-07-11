from __future__ import annotations

import math
from datetime import datetime, timedelta

from app.core.utils.time import from_epoch_seconds, to_utc_naive
from app.db.models import AccountStatus
from app.modules.accounts.schemas import AccountSummary
from app.modules.fleet.schemas import (
    FleetAccountSummary,
    FleetAdditionalCapacity,
    FleetCapacityHeadline,
    FleetExcludedAccount,
    FleetWindowSummary,
)

_INCLUDED_STATUSES = {
    AccountStatus.ACTIVE.value,
    AccountStatus.RATE_LIMITED.value,
    AccountStatus.QUOTA_EXCEEDED.value,
}
_FRESHNESS_LIMIT = timedelta(minutes=10)


def fleet_account_summary_from_account(
    account: AccountSummary,
    *,
    include_usage: bool = True,
    persisted_status_by_account_id: dict[str, str] | None = None,
) -> FleetAccountSummary:
    """Project a dashboard account into the minimal fleet payload."""

    usage = account.usage
    if include_usage:
        status = account.status
    elif persisted_status_by_account_id is None:
        status = "unknown"
    else:
        status = persisted_status_by_account_id.get(account.account_id, "unknown")
    return FleetAccountSummary(
        account_id=account.account_id,
        display_name=account.display_name,
        email=account.email,
        status=status,
        plan_type=account.plan_type,
        primary=FleetWindowSummary(
            remaining_percent=usage.primary_remaining_percent if include_usage and usage is not None else None,
            reset_at=account.reset_at_primary if include_usage else None,
            window_minutes=account.window_minutes_primary if include_usage else None,
        ),
        secondary=FleetWindowSummary(
            remaining_percent=usage.secondary_remaining_percent if include_usage and usage is not None else None,
            reset_at=account.reset_at_secondary if include_usage else None,
            window_minutes=account.window_minutes_secondary if include_usage else None,
        ),
        last_refresh_at=account.last_refresh_at if include_usage else None,
    )


def build_fleet_account_summaries(
    accounts: list[AccountSummary],
    *,
    include_usage: bool = True,
    persisted_status_by_account_id: dict[str, str] | None = None,
) -> list[FleetAccountSummary]:
    return [
        fleet_account_summary_from_account(
            account,
            include_usage=include_usage,
            persisted_status_by_account_id=persisted_status_by_account_id,
        )
        for account in accounts
    ]


def build_fleet_capacity(
    accounts: list[AccountSummary],
    *,
    include_usage: bool,
    generated_at: datetime,
    primary_recorded_at_by_account: dict[str, datetime] | None = None,
    secondary_recorded_at_by_account: dict[str, datetime] | None = None,
) -> tuple[
    list[str],
    list[FleetExcludedAccount],
    FleetCapacityHeadline,
    FleetCapacityHeadline,
    list[FleetAdditionalCapacity],
]:
    included = [account for account in accounts if account.status in _INCLUDED_STATUSES]
    excluded = [
        FleetExcludedAccount(account_id=account.account_id, status=account.status)
        for account in accounts
        if account.status not in _INCLUDED_STATUSES
    ]
    included_ids = [account.account_id for account in included]
    if not include_usage:
        hidden = FleetCapacityHeadline(stale=True, stale_reason="usage_not_visible")
        return included_ids, excluded, hidden, hidden.model_copy(), []

    five_hour = _capacity_headline(
        included,
        generated_at=generated_at,
        window="primary",
        recorded_at_by_account=primary_recorded_at_by_account or {},
    )
    weekly = _capacity_headline(
        included,
        generated_at=generated_at,
        window="secondary",
        recorded_at_by_account=secondary_recorded_at_by_account or {},
    )
    additional = [
        FleetAdditionalCapacity(
            account_id=account.account_id,
            quota_key=quota.quota_key,
            label=quota.display_label or quota.limit_name or quota.metered_feature,
            primary_used_percent=_round_used(quota.primary_window.used_percent)
            if quota.primary_window is not None
            else None,
            secondary_used_percent=_round_used(quota.secondary_window.used_percent)
            if quota.secondary_window is not None
            else None,
            primary_reset_at=from_epoch_seconds(quota.primary_window.reset_at)
            if quota.primary_window is not None
            else None,
            secondary_reset_at=from_epoch_seconds(quota.secondary_window.reset_at)
            if quota.secondary_window is not None
            else None,
        )
        for account in included
        for quota in account.additional_quotas
    ]
    return included_ids, excluded, five_hour, weekly, additional


def _capacity_headline(
    accounts: list[AccountSummary],
    *,
    generated_at: datetime,
    window: str,
    recorded_at_by_account: dict[str, datetime],
) -> FleetCapacityHeadline:
    if not accounts:
        return FleetCapacityHeadline(stale=True, stale_reason="no_included_accounts")
    stale_ids = [
        account.account_id
        for account in accounts
        if account.account_id not in recorded_at_by_account
        or generated_at - to_utc_naive(recorded_at_by_account[account.account_id]) > _FRESHNESS_LIMIT
    ]
    remaining = [
        getattr(account.usage, f"{window}_remaining_percent") if account.usage is not None else None
        for account in accounts
    ]
    missing_ids = [account.account_id for account, value in zip(accounts, remaining, strict=True) if value is None]
    if missing_ids:
        return FleetCapacityHeadline(stale=True, stale_reason="missing_usage:" + ",".join(missing_ids))
    if stale_ids:
        return FleetCapacityHeadline(stale=True, stale_reason="stale_usage:" + ",".join(stale_ids))
    values = [100.0 - _clamp_percent(float(value)) for value in remaining if value is not None]
    return FleetCapacityHeadline(used_percent=_round_used(sum(values) / len(values)), stale=False)


def _clamp_percent(value: float) -> float:
    return min(100.0, max(0.0, value))


def _round_used(value: float) -> int:
    return min(100, max(0, math.floor(float(value) + 0.5)))

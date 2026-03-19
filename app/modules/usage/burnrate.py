from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from app.core import usage as usage_core
from app.core.usage.types import UsageWindowRow
from app.db.models import Account, AccountStatus, UsageHistory

PLUS_CAPACITY_CREDITS = {
    "primary": 225.0,
    "secondary": 7560.0,
}


@dataclass(frozen=True, slots=True)
class BurnRateWindowSnapshot:
    projected_plus_accounts: float | None
    used_plus_accounts: float | None
    included_account_count: int
    max_plus_equivalent_accounts: float
    window_minutes: int | None


@dataclass(frozen=True, slots=True)
class BurnRateSnapshot:
    recorded_at: datetime
    primary: BurnRateWindowSnapshot
    secondary: BurnRateWindowSnapshot


def compute_burn_rate_snapshot(
    *,
    accounts: list[Account],
    latest_primary_usage: Mapping[str, UsageHistory],
    latest_secondary_usage: Mapping[str, UsageHistory],
    now: datetime,
) -> BurnRateSnapshot:
    primary_rows, secondary_rows = _normalize_latest_windows(latest_primary_usage, latest_secondary_usage)
    accounts_by_id = {account.id: account for account in accounts}
    now_epoch = int(now.timestamp())

    primary_snapshot = _compute_window_snapshot(
        accounts_by_id=accounts_by_id,
        rows_by_account=primary_rows,
        window="primary",
        now_epoch=now_epoch,
    )
    secondary_snapshot = _compute_window_snapshot(
        accounts_by_id=accounts_by_id,
        rows_by_account=secondary_rows,
        window="secondary",
        now_epoch=now_epoch,
    )

    return BurnRateSnapshot(
        recorded_at=now,
        primary=primary_snapshot,
        secondary=secondary_snapshot,
    )


def _normalize_latest_windows(
    latest_primary_usage: Mapping[str, UsageHistory],
    latest_secondary_usage: Mapping[str, UsageHistory],
) -> tuple[dict[str, UsageWindowRow], dict[str, UsageWindowRow]]:
    primary_rows_raw = [_usage_history_to_window_row(entry) for entry in latest_primary_usage.values()]
    secondary_rows_raw = [_usage_history_to_window_row(entry) for entry in latest_secondary_usage.values()]

    primary_rows, secondary_rows = usage_core.normalize_weekly_only_rows(primary_rows_raw, secondary_rows_raw)
    return (
        {row.account_id: row for row in primary_rows},
        {row.account_id: row for row in secondary_rows},
    )


def _usage_history_to_window_row(entry: UsageHistory) -> UsageWindowRow:
    return UsageWindowRow(
        account_id=entry.account_id,
        used_percent=entry.used_percent,
        reset_at=entry.reset_at,
        window_minutes=entry.window_minutes,
        recorded_at=entry.recorded_at,
    )


def _compute_window_snapshot(
    *,
    accounts_by_id: dict[str, Account],
    rows_by_account: dict[str, UsageWindowRow],
    window: str,
    now_epoch: int,
) -> BurnRateWindowSnapshot:
    included_account_count = 0
    used_plus_accounts = 0.0
    projected_plus_accounts = 0.0
    max_plus_equivalent_accounts = 0.0

    for account_id, row in rows_by_account.items():
        account = accounts_by_id.get(account_id)
        if account is None:
            continue

        used_percent = _normalize_used_percent(row.used_percent)
        if used_percent is None:
            continue

        weight = _plus_equivalent_weight(account.plan_type, window)
        used_equivalent = (used_percent / 100.0) * weight

        projected_equivalent = used_equivalent
        window_minutes = _effective_window_minutes(window, row.window_minutes)
        if window_minutes is not None and window_minutes > 0 and row.reset_at is not None:
            window_seconds = window_minutes * 60
            seconds_until_reset = max(0, row.reset_at - now_epoch)
            elapsed_seconds = max(0, window_seconds - seconds_until_reset)
            if elapsed_seconds > 0:
                projected_equivalent = used_equivalent * (window_seconds / elapsed_seconds)

        if window == "secondary" and account.status == AccountStatus.QUOTA_EXCEEDED:
            used_equivalent = max(used_equivalent, weight)
            projected_equivalent = max(projected_equivalent, weight)

        included_account_count += 1
        used_plus_accounts += used_equivalent
        projected_plus_accounts += projected_equivalent
        max_plus_equivalent_accounts += weight

    if included_account_count == 0:
        return BurnRateWindowSnapshot(
            projected_plus_accounts=None,
            used_plus_accounts=None,
            included_account_count=0,
            max_plus_equivalent_accounts=0.0,
            window_minutes=usage_core.default_window_minutes(window),
        )

    used_plus_accounts = _clamp_equivalent(used_plus_accounts, max_plus_equivalent_accounts)
    projected_plus_accounts = _clamp_equivalent(projected_plus_accounts, max_plus_equivalent_accounts)
    window_minutes = usage_core.resolve_window_minutes(window, rows_by_account.values())

    return BurnRateWindowSnapshot(
        projected_plus_accounts=projected_plus_accounts,
        used_plus_accounts=used_plus_accounts,
        included_account_count=included_account_count,
        max_plus_equivalent_accounts=max_plus_equivalent_accounts,
        window_minutes=window_minutes,
    )


def _effective_window_minutes(window: str, raw_window_minutes: int | None) -> int | None:
    if raw_window_minutes is not None and raw_window_minutes > 0:
        return raw_window_minutes
    return usage_core.default_window_minutes(window)


def _normalize_used_percent(value: float | None) -> float | None:
    if value is None or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return max(0.0, min(100.0, number))


def _plus_equivalent_weight(plan_type: str | None, window: str) -> float:
    plus_capacity = PLUS_CAPACITY_CREDITS[window]
    plan_capacity = usage_core.capacity_for_plan(plan_type, window)
    if plan_capacity is None or not math.isfinite(plan_capacity) or plan_capacity <= 0:
        # Unknown plans (e.g. enterprise seats) are treated as plus-equivalent by default.
        return 1.0
    return float(plan_capacity) / plus_capacity


def _clamp_equivalent(value: float, max_value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    clamped = max(0.0, value)
    if not math.isfinite(max_value) or max_value <= 0:
        return clamped
    return min(clamped, max_value)

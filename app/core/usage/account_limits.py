from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from enum import Enum

from app.core import usage as usage_core
from app.core.usage.types import UsageWindowRow

MINIMUM_USAGE_LIMIT_FRESHNESS_SECONDS = 180


class AccountUsageLimitState(str, Enum):
    DISABLED = "disabled"
    AVAILABLE = "available"
    REACHED = "reached"
    DATA_UNAVAILABLE = "data_unavailable"

    @property
    def blocks_account_use(self) -> bool:
        return self is type(self).REACHED or self is type(self).DATA_UNAVAILABLE


def evaluate_standard_usage_limit(
    *,
    enabled: bool,
    limit_percent: float | None,
    plan_type: str | None,
    primary: UsageWindowRow | None,
    secondary: UsageWindowRow | None,
    monthly: UsageWindowRow | None,
    refresh_interval_seconds: int,
    now: datetime | None = None,
) -> AccountUsageLimitState:
    """Evaluate an account's operator-defined cap from standard quota rows."""

    if not enabled:
        return AccountUsageLimitState.DISABLED
    if limit_percent is None or not math.isfinite(limit_percent) or not 0.0 < limit_percent <= 100.0:
        return AccountUsageLimitState.DATA_UNAVAILABLE

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    else:
        current_time = current_time.astimezone(timezone.utc)

    relevant_rows = _relevant_standard_rows(
        plan_type=plan_type,
        primary=primary,
        secondary=secondary,
        monthly=monthly,
    )
    current_rows = [row for row in relevant_rows if not _window_elapsed(row, current_time)]
    if not current_rows:
        return AccountUsageLimitState.DATA_UNAVAILABLE

    freshness_seconds = max(int(refresh_interval_seconds) * 2, MINIMUM_USAGE_LIMIT_FRESHNESS_SECONDS)
    freshness_cutoff = current_time - timedelta(seconds=freshness_seconds)
    checked_used_percents: list[float] = []
    for row in current_rows:
        recorded_at = _as_utc(row.recorded_at)
        used_percent = row.used_percent
        if (
            recorded_at is None
            or recorded_at < freshness_cutoff
            or used_percent is None
            or not math.isfinite(used_percent)
            or usage_core.is_no_data_placeholder(row)
        ):
            return AccountUsageLimitState.DATA_UNAVAILABLE
        checked_used_percents.append(used_percent)

    if any(used_percent >= limit_percent for used_percent in checked_used_percents):
        return AccountUsageLimitState.REACHED
    return AccountUsageLimitState.AVAILABLE


def _relevant_standard_rows(
    *,
    plan_type: str | None,
    primary: UsageWindowRow | None,
    secondary: UsageWindowRow | None,
    monthly: UsageWindowRow | None,
) -> tuple[UsageWindowRow, ...]:
    effective_primary, effective_secondary = _effective_primary_and_long_window(primary, secondary)
    if monthly is not None and usage_core.capacity_for_plan(plan_type, "monthly") is not None:
        if (
            effective_primary is None
            and effective_secondary is not None
            and usage_core.is_weekly_window_minutes(effective_secondary.window_minutes)
            and usage_core.should_use_weekly_primary(effective_secondary, monthly)
        ):
            return (effective_secondary,)
        return (monthly,)

    return tuple(row for row in (effective_primary, effective_secondary) if row is not None)


def _effective_primary_and_long_window(
    primary: UsageWindowRow | None,
    secondary: UsageWindowRow | None,
) -> tuple[UsageWindowRow | None, UsageWindowRow | None]:
    if primary is None:
        return None, secondary
    if not usage_core.is_weekly_window_minutes(primary.window_minutes):
        return primary, secondary
    if secondary is None or usage_core.should_use_weekly_primary(primary, secondary):
        return None, primary
    return None, secondary


def _window_elapsed(row: UsageWindowRow, current_time: datetime) -> bool:
    return row.reset_at is not None and row.reset_at <= int(current_time.timestamp())


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

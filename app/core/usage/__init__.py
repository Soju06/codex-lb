from __future__ import annotations

from typing import Iterable, Mapping

from app.core.usage.types import (
    UsageCostSummary,
    UsageHistoryPayload,
    UsageMetricsSummary,
    UsageSummaryPayload,
    UsageWindowRow,
    UsageWindowSnapshot,
    UsageWindowSummary,
)
from app.db.models import Account

PLAN_CAPACITY_CREDITS_PRIMARY = {
    "plus": 225.0,
    "business": 225.0,
    "pro": 1500.0,
}

PLAN_CAPACITY_CREDITS_SECONDARY = {
    "plus": 7560.0,
    "business": 7560.0,
    "pro": 50400.0,
}

DEFAULT_WINDOW_MINUTES_PRIMARY = 300
DEFAULT_WINDOW_MINUTES_SECONDARY = 10080


def _normalize_window_key(window: str | None) -> str:
    normalized = (window or "").lower()
    if normalized in {"primary", "5h"}:
        return "primary"
    if normalized in {"secondary", "7d"}:
        return "secondary"
    return normalized


def _empty_cost() -> UsageCostSummary:
    return UsageCostSummary(currency="USD", total_usd_7d=0.0, by_model=[])


def _empty_window(
    reset_at: int | None = None,
    window_minutes: int | None = None,
) -> UsageWindowSnapshot:
    return UsageWindowSnapshot(
        used_percent=0.0,
        capacity_credits=0.0,
        used_credits=0.0,
        reset_at=reset_at,
        window_minutes=window_minutes,
    )


def used_credits_from_percent(used_percent: float | None, capacity_credits: float | None) -> float | None:
    if used_percent is None or capacity_credits is None:
        return None
    return (capacity_credits * used_percent) / 100.0


def remaining_percent_from_used(used_percent: float | None) -> float | None:
    if used_percent is None:
        return None
    return max(0.0, 100.0 - float(used_percent))


def remaining_credits_from_used(
    used_credits: float | None,
    capacity_credits: float | None,
) -> float | None:
    if used_credits is None or capacity_credits is None:
        return None
    return max(0.0, float(capacity_credits) - float(used_credits))


def remaining_credits_from_percent(
    used_percent: float | None,
    capacity_credits: float | None,
) -> float | None:
    used_credits = used_credits_from_percent(used_percent, capacity_credits)
    return remaining_credits_from_used(used_credits, capacity_credits)


def normalize_usage_window(summary: UsageWindowSummary) -> UsageWindowSnapshot:
    return UsageWindowSnapshot(
        used_percent=float(summary.used_percent or 0.0),
        capacity_credits=float(summary.capacity_credits),
        used_credits=float(summary.used_credits),
        reset_at=summary.reset_at,
        window_minutes=summary.window_minutes,
    )


def summarize_usage_window(
    usage_rows: Iterable[UsageWindowRow],
    account_map: Mapping[str, Account],
    window: str,
) -> UsageWindowSummary:
    total_capacity = 0.0
    total_used = 0.0
    reset_candidates: list[int] = []
    window_minutes: int | None = None

    for row in usage_rows:
        if row.reset_at is not None:
            reset_candidates.append(row.reset_at)
        if row.window_minutes is not None and row.window_minutes > 0:
            if window_minutes is None or row.window_minutes > window_minutes:
                window_minutes = row.window_minutes
        account = account_map.get(row.account_id)
        capacity = capacity_for_plan(account.plan_type if account else None, window)
        if row.used_percent is None or capacity is None:
            continue
        total_capacity += capacity
        total_used += (capacity * float(row.used_percent)) / 100.0

    if window_minutes is None:
        window_minutes = default_window_minutes(window)

    overall = None
    if total_capacity > 0:
        overall = (total_used / total_capacity) * 100.0
    reset_at_value = min(reset_candidates) if reset_candidates else None
    return UsageWindowSummary(
        used_percent=float(overall) if overall is not None else None,
        capacity_credits=float(total_capacity),
        used_credits=float(total_used),
        reset_at=reset_at_value,
        window_minutes=window_minutes,
    )


def capacity_for_plan(plan_type: str | None, window: str) -> float | None:
    if not plan_type:
        return None
    normalized = plan_type.lower()
    window_key = _normalize_window_key(window)
    if window_key == "primary":
        return PLAN_CAPACITY_CREDITS_PRIMARY.get(normalized)
    if window_key == "secondary":
        return PLAN_CAPACITY_CREDITS_SECONDARY.get(normalized)
    return None


def default_window_minutes(window: str) -> int | None:
    window_key = _normalize_window_key(window)
    if window_key == "primary":
        return DEFAULT_WINDOW_MINUTES_PRIMARY
    if window_key == "secondary":
        return DEFAULT_WINDOW_MINUTES_SECONDARY
    return None


def parse_usage_summary(
    primary_window: UsageWindowSummary,
    secondary_window: UsageWindowSummary | None,
    cost: UsageCostSummary,
    metrics: UsageMetricsSummary | None = None,
) -> UsageSummaryPayload:
    primary = normalize_usage_window(primary_window)
    secondary = None
    if secondary_window is not None:
        secondary = normalize_usage_window(secondary_window)
    return UsageSummaryPayload(
        primary_window=primary,
        secondary_window=secondary,
        cost=cost,
        metrics=metrics,
    )


async def usage_summary() -> UsageSummaryPayload:
    return UsageSummaryPayload(
        primary_window=_empty_window(window_minutes=None),
        secondary_window=None,
        cost=_empty_cost(),
        metrics=None,
    )


async def usage_history(hours: int) -> UsageHistoryPayload:
    return UsageHistoryPayload(window_hours=hours, accounts=[])

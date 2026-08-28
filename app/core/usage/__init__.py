from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Iterable, Mapping

from app.core.plan_types import normalize_account_plan_type
from app.core.usage.models import UsageWindow
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
    "free": 0.0,
    "plus": 225.0,
    "business": 225.0,
    "team": 225.0,
    "edu": 225.0,
    "pro": 1500.0,
    "prolite": 1125.0,
    "enterprise": 1500.0,
}

PLAN_CAPACITY_CREDITS_SECONDARY = {
    "free": 1134.0,
    "plus": 7560.0,
    "business": 7560.0,
    "team": 7560.0,
    "edu": 7560.0,
    "pro": 50400.0,
    "prolite": 37800.0,
    "enterprise": 50400.0,
}

PLAN_CAPACITY_CREDITS_MONTHLY = {
    "free": 1134.0,
}

# Rows written by the same upstream fetch land within milliseconds of each
# other; a sibling row only proves a *later* fetch (one that no longer
# reported the stale window) when it is newer by more than this margin. This
# is the same-fetch threshold shared by the weekly-primary remap tiebreak and
# the usage updater's sibling-row freshness logic. (Other modules keep their
# own private 5.0 s constants for unrelated sibling logic; consolidating those
# is out of scope for the weekly-primary tiebreak fix.)
SIBLING_FETCH_MARGIN_SECONDS = 5.0

DEFAULT_WINDOW_MINUTES_PRIMARY = 300
DEFAULT_WINDOW_MINUTES_SECONDARY = 10080
DEFAULT_WINDOW_MINUTES_MONTHLY = 43200


@dataclass(frozen=True)
class NormalizedRateLimitWindows:
    primary: UsageWindow | None
    secondary: UsageWindow | None
    monthly: UsageWindow | None


def _normalize_window_key(window: str | None) -> str:
    normalized = (window or "").lower()
    if normalized in {"primary", "5h"}:
        return "primary"
    if normalized in {"secondary", "7d"}:
        return "secondary"
    if normalized in {"monthly", "30d"}:
        return "monthly"
    return normalized


def normalize_rate_limit_windows(
    primary_window: UsageWindow | None,
    secondary_window: UsageWindow | None,
) -> NormalizedRateLimitWindows:
    if (
        primary_window is not None
        and primary_window.limit_window_seconds == DEFAULT_WINDOW_MINUTES_MONTHLY * 60
        and secondary_window is None
    ):
        return NormalizedRateLimitWindows(primary=None, secondary=None, monthly=primary_window)
    return NormalizedRateLimitWindows(primary=primary_window, secondary=secondary_window, monthly=None)


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
    window_minutes_values: set[int] = set()

    for row in usage_rows:
        if row.reset_at is not None:
            reset_candidates.append(row.reset_at)
        if row.window_minutes is not None and row.window_minutes > 0:
            window_minutes_values.add(row.window_minutes)
        account = account_map.get(row.account_id)
        capacity = capacity_for_plan(account.plan_type if account else None, window)
        if row.used_percent is None or capacity is None:
            continue
        total_capacity += capacity
        total_used += (capacity * float(row.used_percent)) / 100.0

    window_minutes = _resolve_window_minutes(window, window_minutes_values)

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
    normalized = normalize_account_plan_type(plan_type)
    if not normalized:
        return None
    window_key = _normalize_window_key(window)
    if window_key == "primary":
        return PLAN_CAPACITY_CREDITS_PRIMARY.get(normalized)
    if window_key == "secondary":
        return PLAN_CAPACITY_CREDITS_SECONDARY.get(normalized)
    if window_key == "monthly":
        return PLAN_CAPACITY_CREDITS_MONTHLY.get(normalized)
    return None


def default_window_minutes(window: str) -> int | None:
    window_key = _normalize_window_key(window)
    if window_key == "primary":
        return DEFAULT_WINDOW_MINUTES_PRIMARY
    if window_key == "secondary":
        return DEFAULT_WINDOW_MINUTES_SECONDARY
    if window_key == "monthly":
        return DEFAULT_WINDOW_MINUTES_MONTHLY
    return None


def resolve_window_minutes(window: str, rows: Iterable[UsageWindowRow]) -> int | None:
    values = {row.window_minutes for row in rows if row.window_minutes is not None and row.window_minutes > 0}
    return _resolve_window_minutes(window, values)


def is_weekly_window_minutes(window_minutes: int | None) -> bool:
    if window_minutes is None:
        return False
    secondary_default = default_window_minutes("secondary")
    if secondary_default is None:
        return False
    return window_minutes == secondary_default


def is_monthly_window_minutes(window_minutes: int | None) -> bool:
    if window_minutes is None:
        return False
    monthly_default = default_window_minutes("monthly")
    if monthly_default is None:
        return False
    return window_minutes == monthly_default


def is_primary_window_minutes(window_minutes: int | None) -> bool:
    if window_minutes is None:
        return False
    primary_default = default_window_minutes("primary")
    if primary_default is None:
        return False
    return window_minutes == primary_default


def should_use_weekly_primary(
    primary_row: UsageWindowRow,
    secondary_row: UsageWindowRow | None,
) -> bool:
    if not is_weekly_window_minutes(primary_row.window_minutes):
        return False
    if secondary_row is None:
        return True
    return _should_prefer_primary_row(primary_row, secondary_row)


def expire_elapsed_window_rows(
    rows: Iterable[UsageWindowRow],
    *,
    now_epoch: int,
) -> list[UsageWindowRow]:
    # A row whose reset_at has elapsed describes an expired window: upstream
    # may have stopped reporting that window entirely, in which case the row
    # is never rewritten. Treat it as a reset window (0% used, no reset) so
    # pooled summaries and availability never freeze on stale samples.
    expired: list[UsageWindowRow] = []
    for row in rows:
        if row.reset_at is not None and row.reset_at <= now_epoch:
            expired.append(replace(row, used_percent=0.0, reset_at=None))
        else:
            expired.append(row)
    return expired


def normalize_weekly_only_rows(
    primary_rows: Iterable[UsageWindowRow],
    secondary_rows: Iterable[UsageWindowRow],
) -> tuple[list[UsageWindowRow], list[UsageWindowRow]]:
    # Some plans (notably free) can report only one weekly window in the
    # primary slot. Re-map those rows into secondary so downstream 5h/7d
    # consumers operate on consistent semantics.
    primary_by_account = {row.account_id: row for row in primary_rows}
    normalized_secondary_by_account = {row.account_id: row for row in secondary_rows}

    normalized_primary: list[UsageWindowRow] = []

    for account_id, primary_row in primary_by_account.items():
        if is_weekly_window_minutes(primary_row.window_minutes):
            secondary_row = normalized_secondary_by_account.get(account_id)
            if should_use_weekly_primary(primary_row, secondary_row):
                normalized_secondary_by_account[account_id] = primary_row
            continue
        normalized_primary.append(primary_row)

    return normalized_primary, list(normalized_secondary_by_account.values())


def _has_real_quota_metadata(row: UsageWindowRow) -> bool:
    """A row carries real quota metadata when it has a positive window
    duration AND a reset deadline. Used by the weekly-primary remap tiebreak to
    distinguish a real weekly sample from a no-data placeholder."""
    return row.window_minutes is not None and row.window_minutes > 0 and row.reset_at is not None


def _is_no_data_placeholder(row: UsageWindowRow) -> bool:
    """A no-data placeholder is the absence of a measurement, not 0% used.

    Such rows (no positive window duration AND no reset deadline) are written
    when upstream omits a window slot entirely. Within the data-aware tiebreak
    (same-fetch or indeterminate ordering), a placeholder must not displace a
    row carrying real quota metadata. A newer cross-fetch placeholder can still
    win via fetch ordering, which is intentional — the cross-fetch winner is
    rendered per existing placeholder rules.
    """
    has_window = row.window_minutes is not None and row.window_minutes > 0
    return not has_window and row.reset_at is None


def _should_prefer_primary_row(primary_row: UsageWindowRow, secondary_row: UsageWindowRow) -> bool:
    primary_recorded_at = _normalize_recorded_at(primary_row.recorded_at)
    secondary_recorded_at = _normalize_recorded_at(secondary_row.recorded_at)

    # Fetch ordering decides ONLY when both timestamps are present and differ
    # by more than the sibling margin. A genuinely later fetch is more
    # authoritative about what upstream currently reports, so the newer row
    # wins — this preserves the pre-fix cross-fetch behavior and avoids
    # freezing a stale real weekly primary over a fresh placeholder from a
    # later fetch. When only one (or neither) timestamp is present we cannot
    # determine fetch ordering, so we fall through to the data-aware tiebreak
    # rather than letting timestamp presence alone decide (a timestamped
    # no-data placeholder must not beat an untimestamped real weekly row).
    if primary_recorded_at is not None and secondary_recorded_at is not None:
        if primary_recorded_at != secondary_recorded_at:
            delta_seconds = abs((primary_recorded_at - secondary_recorded_at).total_seconds())
            if delta_seconds > SIBLING_FETCH_MARGIN_SECONDS:
                return primary_recorded_at > secondary_recorded_at
            # Within the sibling margin: same fetch, fall through to the
            # data-aware tiebreak below so sub-second write skew cannot flip
            # the winner per refresh.

    # Same-fetch, or one/both timestamps unavailable, or rows equidistant
    # within the margin: a row carrying real quota metadata MUST win over a
    # no-data placeholder. A placeholder is the absence of a measurement, not
    # 0% used, so it must never displace a real weekly sample (otherwise the
    # dashboard jumps to 100% remaining every refresh).
    primary_has_real = _has_real_quota_metadata(primary_row)
    if primary_has_real and _is_no_data_placeholder(secondary_row):
        return True
    if _has_real_quota_metadata(secondary_row) and _is_no_data_placeholder(primary_row):
        return False

    # Both real or both placeholder (same fetch / indeterminate ordering):
    # reset-at precedence, then the stable weekly-primary default.
    if primary_row.reset_at is not None and secondary_row.reset_at is not None:
        if primary_row.reset_at != secondary_row.reset_at:
            return primary_row.reset_at > secondary_row.reset_at
    elif primary_row.reset_at is not None:
        return True
    elif secondary_row.reset_at is not None:
        return False

    # Keep weekly-only semantics stable when no discriminator is available.
    return True


def _normalize_recorded_at(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _resolve_window_minutes(window: str, values: set[int]) -> int | None:
    if not values:
        return default_window_minutes(window)
    if len(values) == 1:
        return next(iter(values))
    default = default_window_minutes(window)
    if default is not None:
        return default
    return min(values)


def parse_usage_summary(
    primary_window: UsageWindowSummary,
    secondary_window: UsageWindowSummary | None,
    monthly_window: UsageWindowSummary | None,
    cost: UsageCostSummary,
    metrics: UsageMetricsSummary | None = None,
) -> UsageSummaryPayload:
    primary = normalize_usage_window(primary_window)
    secondary = None
    if secondary_window is not None:
        secondary = normalize_usage_window(secondary_window)
    monthly = None
    if monthly_window is not None:
        monthly = normalize_usage_window(monthly_window)
    return UsageSummaryPayload(
        primary_window=primary,
        secondary_window=secondary,
        monthly_window=monthly,
        cost=cost,
        metrics=metrics,
    )


async def usage_summary() -> UsageSummaryPayload:
    return UsageSummaryPayload(
        primary_window=_empty_window(window_minutes=None),
        secondary_window=None,
        monthly_window=None,
        cost=_empty_cost(),
        metrics=None,
    )


async def usage_history(hours: int) -> UsageHistoryPayload:
    return UsageHistoryPayload(window_hours=hours, accounts=[])

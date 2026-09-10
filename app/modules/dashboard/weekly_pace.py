from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil, isfinite
from typing import Literal

from app.core.usage import PLAN_CAPACITY_CREDITS_SECONDARY
from app.core.usage.depletion import EWMAState, ewma_update
from app.core.utils.time import naive_utc_to_epoch
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.accounts.schemas import AccountSummary
from app.modules.dashboard.schemas import (
    WeeklyCreditApiKeyAttribution,
    WeeklyCreditPaceResponse,
    WeeklyCreditPaceStatus,
    WeeklyCreditResetEvent,
    WeeklyCreditRunwayStatus,
)

PRO_WEEKLY_CAPACITY_CREDITS = PLAN_CAPACITY_CREDITS_SECONDARY["pro"]
RECENT_BURN_WINDOW = timedelta(hours=6)
FLEET_BURN_WINDOW = timedelta(hours=3)
DEMAND_WINDOW = timedelta(days=7)
RELIEF_COHORT_USED_PERCENT = 95.0
RELIEF_CLUSTER_WINDOW = timedelta(hours=1)
SATURATED_USED_PERCENT = 99.5
TIGHT_MARGIN_HOURS = 24.0
TIGHT_HEADROOM_PERCENT = 12.0
MIN_FRESHNESS_SECONDS = 300.0
FRESHNESS_MISSED_REFRESH_CYCLES = 3.0
PACE_ELIGIBLE_ACCOUNT_STATUSES = frozenset(
    (
        AccountStatus.ACTIVE,
        AccountStatus.REAUTH_REQUIRED,
        AccountStatus.RATE_LIMITED,
        AccountStatus.QUOTA_EXCEEDED,
    )
)


@dataclass
class _PaceAccount:
    account_id: str
    full_credits: float
    remaining_credits: float
    reset_at_ms: float
    window_ms: float
    forecast_burn_rate_credits_per_hour: float | None


@dataclass
class _SimulationAccount:
    full_credits: float
    balance_credits: float
    reset_at_ms: float
    window_ms: float


@dataclass
class _Projection:
    projected_shortfall_credits: float
    projected_depletion_hours: float | None
    projected_minimum_remaining_credits: float


def build_weekly_credit_pace(
    *,
    accounts: list[Account],
    account_summaries: list[AccountSummary],
    secondary_history: dict[str, list[UsageHistory]],
    now: datetime,
    usage_refresh_interval_seconds: int,
    top_api_keys: list[WeeklyCreditApiKeyAttribution] | None = None,
    trailing_demand_used_percent_by_account: Mapping[str, float] | None = None,
    working_days: set[int] | None = None,
    smoothing_window_minutes: int = 30,
) -> WeeklyCreditPaceResponse | None:
    """Build server-side weekly quota pace from active, fresh weekly usage rows.

    The dashboard card needs two separate signals:
    - current schedule gap: actual remaining vs. linear expected remaining now
    - forecast shortfall: whether recent burn will deplete the pool before resets

    Computing this in the backend keeps status/freshness filters aligned with the
    routing pool and lets the forecast use usage_history instead of a full-window
    cumulative average.
    """

    now_ms = naive_utc_to_epoch(now) * 1000.0
    if not _is_finite_positive(now_ms):
        return None

    accounts_by_id = {account.id: account for account in accounts}
    freshness_cutoff = now - timedelta(seconds=_freshness_seconds(usage_refresh_interval_seconds))

    pace_accounts: list[_PaceAccount] = []
    stale_account_count = 0
    inactive_account_count = 0
    rate_sample_count = 0
    total_full_credits = 0.0
    total_actual_remaining_credits = 0.0
    total_smoothed_remaining_credits = 0.0
    total_expected_remaining_credits = 0.0
    scheduled_burn_rate_credits_per_hour = 0.0
    forecast_burn_rate_credits_per_hour = 0.0

    for summary in account_summaries:
        timing = _weekly_timing(summary, now_ms)
        if timing is None:
            continue

        account = accounts_by_id.get(summary.account_id)
        if account is None or account.status not in PACE_ELIGIBLE_ACCOUNT_STATUSES:
            inactive_account_count += 1
            continue

        rows = sorted(secondary_history.get(summary.account_id, []), key=lambda row: row.recorded_at)
        latest = rows[-1] if rows else None
        if latest is None or latest.recorded_at < freshness_cutoff:
            stale_account_count += 1
            continue

        full_credits, actual_remaining_credits, effective_reset_at_ms, window_ms = timing
        used_schedule_fraction = _used_schedule_fraction(
            reset_at_ms=effective_reset_at_ms,
            window_ms=window_ms,
            now_ms=now_ms,
            working_days=working_days,
        )
        expected_remaining_credits = full_credits * (1.0 - used_schedule_fraction)
        account_rate = _recent_burn_rate_credits_per_hour(rows, full_credits, now)
        smoothed_remaining_credits = _smoothed_remaining_credits(
            rows=rows,
            full_credits=full_credits,
            current_remaining_credits=actual_remaining_credits,
            now=now,
            smoothing_window_minutes=smoothing_window_minutes,
        )

        total_full_credits += full_credits
        total_actual_remaining_credits += actual_remaining_credits
        total_smoothed_remaining_credits += smoothed_remaining_credits
        total_expected_remaining_credits += expected_remaining_credits
        scheduled_burn_rate_credits_per_hour += full_credits * _working_schedule_share_per_hour(
            reset_at_ms=effective_reset_at_ms,
            window_ms=window_ms,
            working_days=working_days,
        )
        if account_rate is not None:
            rate_sample_count += 1
            forecast_burn_rate_credits_per_hour += account_rate

        pace_accounts.append(
            _PaceAccount(
                account_id=summary.account_id,
                full_credits=full_credits,
                remaining_credits=actual_remaining_credits,
                reset_at_ms=effective_reset_at_ms,
                window_ms=window_ms,
                forecast_burn_rate_credits_per_hour=account_rate,
            )
        )

    if not pace_accounts or total_full_credits <= 0:
        return None

    actual_used_percent = 100.0 * (total_full_credits - total_actual_remaining_credits) / total_full_credits
    scheduled_used_percent = 100.0 * (total_full_credits - total_expected_remaining_credits) / total_full_credits
    delta_percent = actual_used_percent - scheduled_used_percent
    schedule_gap_credits = max(0.0, total_expected_remaining_credits - total_actual_remaining_credits)
    smoothed_used_percent = 100.0 * (total_full_credits - total_smoothed_remaining_credits) / total_full_credits
    smoothed_delta_percent = smoothed_used_percent - scheduled_used_percent
    smoothed_schedule_gap_credits = max(0.0, total_expected_remaining_credits - total_smoothed_remaining_credits)

    forecast_rate = forecast_burn_rate_credits_per_hour if rate_sample_count > 0 else None
    projection = _project_weekly_pool(pace_accounts, now_ms, forecast_rate)
    projected_shortfall_credits = projection.projected_shortfall_credits
    pace_multiplier = (
        forecast_rate / scheduled_burn_rate_credits_per_hour
        if forecast_rate is not None and scheduled_burn_rate_credits_per_hour > 0
        else None
    )
    pause_for_break_even_hours = (
        projected_shortfall_credits / forecast_rate
        if forecast_rate is not None and forecast_rate > 0 and projected_shortfall_credits > 0
        else None
    )
    throttle_to_percent = (
        _clamp((scheduled_burn_rate_credits_per_hour / forecast_rate) * 100.0, 0.0, 100.0)
        if forecast_rate is not None
        and forecast_rate > 0
        and scheduled_burn_rate_credits_per_hour > 0
        and projected_shortfall_credits > 0
        else None
    )
    reduce_by_percent = 100.0 - throttle_to_percent if throttle_to_percent is not None else None
    pro_equivalent = (
        projected_shortfall_credits / PRO_WEEKLY_CAPACITY_CREDITS if projected_shortfall_credits > 0 else None
    )
    pro_accounts = ceil(pro_equivalent) if pro_equivalent is not None else None

    headroom_credits = total_actual_remaining_credits
    headroom_percent = 100.0 * headroom_credits / total_full_credits
    recent_burn_rate = _fleet_recent_burn_rate_credits_per_hour(
        pace_accounts,
        secondary_history,
        now,
    )
    depletion_eta_hours = (
        headroom_credits / recent_burn_rate if recent_burn_rate is not None and recent_burn_rate > 0 else None
    )
    next_relief_in_hours, next_relief_credits = _next_relief(pace_accounts, now_ms)
    runway_status = _runway_status(
        depletion_eta_hours=depletion_eta_hours,
        next_relief_in_hours=next_relief_in_hours,
        headroom_percent=headroom_percent,
    )
    saturated_account_count = sum(_used_percent(account) >= SATURATED_USED_PERCENT for account in pace_accounts)
    add_pro_accounts = None
    if trailing_demand_used_percent_by_account is not None:
        trailing_demand_credits = sum(
            account.full_credits
            * max(0.0, trailing_demand_used_percent_by_account.get(account.account_id, 0.0))
            / 100.0
            for account in pace_accounts
        )
        demand_quota_weeks = trailing_demand_credits / PRO_WEEKLY_CAPACITY_CREDITS
        fleet_capacity_quota_weeks = total_full_credits / PRO_WEEKLY_CAPACITY_CREDITS
        demand_surplus_accounts = demand_quota_weeks - fleet_capacity_quota_weeks
        if demand_surplus_accounts > 0 and (runway_status == "runs_dry" or saturated_account_count > 0):
            add_pro_accounts = ceil(demand_surplus_accounts)

    return WeeklyCreditPaceResponse(
        total_full_credits=total_full_credits,
        total_actual_remaining_credits=total_actual_remaining_credits,
        total_expected_remaining_credits=total_expected_remaining_credits,
        actual_used_percent=actual_used_percent,
        scheduled_used_percent=scheduled_used_percent,
        delta_percent=delta_percent,
        schedule_gap_credits=schedule_gap_credits,
        smoothed_delta_percent=smoothed_delta_percent,
        smoothed_schedule_gap_credits=smoothed_schedule_gap_credits,
        pace_gap_smoothing_minutes=smoothing_window_minutes,
        over_plan_credits=schedule_gap_credits,
        projected_shortfall_credits=projected_shortfall_credits,
        pause_for_break_even_hours=pause_for_break_even_hours,
        pace_multiplier=pace_multiplier,
        throttle_to_percent=throttle_to_percent,
        reduce_by_percent=reduce_by_percent,
        pro_account_equivalent_to_cover_over_plan=pro_equivalent,
        pro_accounts_to_cover_over_plan=pro_accounts,
        projected_depletion_hours=projection.projected_depletion_hours,
        projected_minimum_remaining_credits=projection.projected_minimum_remaining_credits,
        forecast_burn_rate_credits_per_hour=forecast_rate,
        scheduled_burn_rate_credits_per_hour=scheduled_burn_rate_credits_per_hour,
        headroom_percent=headroom_percent,
        headroom_credits=headroom_credits,
        burn_rate_recent_credits_per_hour=recent_burn_rate,
        depletion_eta_hours=depletion_eta_hours,
        next_relief_in_hours=next_relief_in_hours,
        next_relief_credits=next_relief_credits,
        reset_events=_reset_events(pace_accounts, now_ms),
        runway_status=runway_status,
        saturated_account_count=saturated_account_count,
        top_api_keys=top_api_keys or [],
        add_pro_accounts=add_pro_accounts,
        status=_legacy_status(runway_status),
        account_count=len(pace_accounts),
        stale_account_count=stale_account_count,
        inactive_account_count=inactive_account_count,
        confidence=_confidence(len(pace_accounts), rate_sample_count, stale_account_count),
    )


def _fleet_recent_burn_rate_credits_per_hour(
    accounts: list[_PaceAccount],
    secondary_history: dict[str, list[UsageHistory]],
    now: datetime,
) -> float | None:
    window_start = now - FLEET_BURN_WINDOW
    total_burn_credits = 0.0
    considered_recorded_at: list[datetime] = []

    for account in accounts:
        rows = sorted(
            (row for row in secondary_history.get(account.account_id, []) if window_start <= row.recorded_at <= now),
            key=lambda row: row.recorded_at,
        )
        if len(rows) < 2:
            continue

        considered_recorded_at.extend(row.recorded_at for row in rows)
        for previous, current in zip(rows, rows[1:]):
            delta_percent = current.used_percent - previous.used_percent
            if delta_percent > 0:
                total_burn_credits += account.full_credits * delta_percent / 100.0

    if not considered_recorded_at:
        return None

    observed_span_hours = (max(considered_recorded_at) - min(considered_recorded_at)).total_seconds() / 3600.0
    return total_burn_credits / max(0.5, observed_span_hours)


def _next_relief(accounts: list[_PaceAccount], now_ms: float) -> tuple[float, float]:
    cohort = [account for account in accounts if _used_percent(account) >= RELIEF_COHORT_USED_PERCENT]
    if not cohort:
        cohort = accounts

    soonest_reset_ms = min(account.reset_at_ms for account in cohort)
    cluster_end_ms = soonest_reset_ms + RELIEF_CLUSTER_WINDOW.total_seconds() * 1000.0
    relief_credits = sum(
        account.full_credits * _used_percent(account) / 100.0
        for account in cohort
        if account.reset_at_ms <= cluster_end_ms
    )
    return max(0.0, (soonest_reset_ms - now_ms) / 3_600_000.0), relief_credits


def _reset_events(accounts: list[_PaceAccount], now_ms: float) -> list[WeeklyCreditResetEvent]:
    horizon_ms = now_ms + DEMAND_WINDOW.total_seconds() * 1000.0
    return [
        WeeklyCreditResetEvent(
            at=datetime.fromtimestamp(account.reset_at_ms / 1000.0, UTC),
            credits_returned=account.full_credits * _used_percent(account) / 100.0,
        )
        for account in sorted(accounts, key=lambda item: item.reset_at_ms)
        if account.reset_at_ms <= horizon_ms
    ]


def _runway_status(
    *,
    depletion_eta_hours: float | None,
    next_relief_in_hours: float,
    headroom_percent: float,
) -> WeeklyCreditRunwayStatus:
    if depletion_eta_hours is not None and depletion_eta_hours < next_relief_in_hours:
        return "runs_dry"
    if (
        depletion_eta_hours is not None and depletion_eta_hours - next_relief_in_hours < TIGHT_MARGIN_HOURS
    ) or headroom_percent < TIGHT_HEADROOM_PERCENT:
        return "tight"
    return "safe"


def _used_percent(account: _PaceAccount) -> float:
    return 100.0 * (account.full_credits - account.remaining_credits) / account.full_credits


def _weekly_timing(summary: AccountSummary, now_ms: float) -> tuple[float, float, float, float] | None:
    raw_full_credits = summary.capacity_credits_secondary
    raw_remaining_credits = summary.remaining_credits_secondary
    reset_at = summary.reset_at_secondary
    raw_window_minutes = summary.window_minutes_secondary
    if (
        not isinstance(raw_full_credits, int | float)
        or raw_full_credits <= 0
        or not isinstance(raw_remaining_credits, int | float)
        or raw_remaining_credits < 0
        or reset_at is None
        or not isinstance(raw_window_minutes, int | float)
        or raw_window_minutes <= 0
    ):
        return None

    full_credits = float(raw_full_credits)
    remaining_credits = float(raw_remaining_credits)
    window_minutes = float(raw_window_minutes)
    reset_at_ms = naive_utc_to_epoch(reset_at) * 1000.0
    window_ms = window_minutes * 60_000.0
    if not _is_finite_positive(reset_at_ms) or not _is_finite_positive(window_ms):
        return None

    effective_reset_at_ms = _advance_reset_at(reset_at_ms, window_ms, now_ms)
    return (
        full_credits,
        _clamp(remaining_credits, 0.0, full_credits),
        effective_reset_at_ms,
        window_ms,
    )


def _recent_burn_rate_credits_per_hour(
    rows: list[UsageHistory],
    full_credits: float,
    now: datetime,
) -> float | None:
    recent_start = now - RECENT_BURN_WINDOW
    recent_rows = [row for row in rows if row.recorded_at >= recent_start and row.recorded_at <= now]
    if len(recent_rows) < 2:
        return None

    state: EWMAState | None = None
    for row in recent_rows:
        state = ewma_update(
            state,
            row.used_percent,
            float(naive_utc_to_epoch(row.recorded_at)),
            reset_at=row.reset_at,
        )
    if state is None or state.rate is None:
        return None
    return max(0.0, state.rate * full_credits * 36.0)


def _smoothed_remaining_credits(
    *,
    rows: list[UsageHistory],
    full_credits: float,
    current_remaining_credits: float,
    now: datetime,
    smoothing_window_minutes: int,
) -> float:
    smoothing_start = now - timedelta(minutes=smoothing_window_minutes)
    latest = rows[-1] if rows else None
    latest_reset_at = latest.reset_at if latest is not None else None
    latest_window_minutes = latest.window_minutes if latest is not None else None
    recent_rows = [
        row
        for row in rows
        if row.recorded_at >= smoothing_start
        and row.recorded_at <= now
        and (latest_reset_at is None or row.reset_at == latest_reset_at)
        and (latest_window_minutes is None or row.window_minutes == latest_window_minutes)
    ]
    if not recent_rows:
        return current_remaining_credits

    total_remaining = 0.0
    sample_count = 0
    for row in recent_rows:
        if not isinstance(row.used_percent, int | float) or not isfinite(row.used_percent):
            continue
        used_percent = _clamp(float(row.used_percent), 0.0, 100.0)
        total_remaining += full_credits * (1.0 - used_percent / 100.0)
        sample_count += 1

    if sample_count == 0:
        return current_remaining_credits
    return _clamp(total_remaining / sample_count, 0.0, full_credits)


def _project_weekly_pool(
    accounts: list[_PaceAccount],
    now_ms: float,
    forecast_burn_rate_credits_per_hour: float | None,
) -> _Projection:
    total_remaining = sum(account.remaining_credits for account in accounts)
    if forecast_burn_rate_credits_per_hour is None or forecast_burn_rate_credits_per_hour <= 0:
        return _Projection(
            projected_shortfall_credits=0.0,
            projected_depletion_hours=None,
            projected_minimum_remaining_credits=total_remaining,
        )

    burn_rate_credits_per_ms = forecast_burn_rate_credits_per_hour / 3_600_000.0
    simulation_accounts = [
        _SimulationAccount(
            full_credits=account.full_credits,
            balance_credits=account.remaining_credits,
            reset_at_ms=account.reset_at_ms,
            window_ms=account.window_ms,
        )
        for account in accounts
    ]
    horizon_ms = now_ms + (max(account.window_ms for account in accounts) * 2.0)
    cursor_ms = now_ms
    minimum_remaining = total_remaining

    while cursor_ms < horizon_ms:
        simulation_accounts.sort(key=lambda account: account.reset_at_ms)
        next_reset = simulation_accounts[0]
        next_event_at_ms = min(next_reset.reset_at_ms, horizon_ms)
        interval_ms = max(0.0, next_event_at_ms - cursor_ms)
        interval_burn = burn_rate_credits_per_ms * interval_ms
        total_balance = _total_balance(simulation_accounts)

        if interval_burn > total_balance:
            depletion_wait_ms = total_balance / burn_rate_credits_per_ms if burn_rate_credits_per_ms > 0 else 0.0
            return _Projection(
                projected_shortfall_credits=interval_burn - total_balance,
                projected_depletion_hours=(cursor_ms - now_ms + depletion_wait_ms) / 3_600_000.0,
                projected_minimum_remaining_credits=0.0,
            )

        _consume_balance(simulation_accounts, interval_burn)
        minimum_remaining = min(minimum_remaining, _total_balance(simulation_accounts))
        cursor_ms = next_event_at_ms
        if cursor_ms >= horizon_ms:
            break

        next_reset.balance_credits = next_reset.full_credits
        next_reset.reset_at_ms += next_reset.window_ms
        minimum_remaining = min(minimum_remaining, _total_balance(simulation_accounts))

    return _Projection(
        projected_shortfall_credits=0.0,
        projected_depletion_hours=None,
        projected_minimum_remaining_credits=minimum_remaining,
    )


def _consume_balance(accounts: list[_SimulationAccount], amount_credits: float) -> None:
    remaining_to_consume = amount_credits
    for account in sorted(accounts, key=lambda item: item.reset_at_ms):
        if remaining_to_consume <= 0:
            return
        consumed = min(account.balance_credits, remaining_to_consume)
        account.balance_credits -= consumed
        remaining_to_consume -= consumed


def _total_balance(accounts: list[_SimulationAccount]) -> float:
    return sum(account.balance_credits for account in accounts)


def _advance_reset_at(reset_at_ms: float, window_ms: float, now_ms: float) -> float:
    if reset_at_ms > now_ms:
        return reset_at_ms
    missed_windows = int((now_ms - reset_at_ms) // window_ms) + 1
    return reset_at_ms + (missed_windows * window_ms)


def _used_schedule_fraction(
    *,
    reset_at_ms: float,
    window_ms: float,
    now_ms: float,
    working_days: set[int] | None,
) -> float:
    window_start_ms = reset_at_ms - window_ms
    elapsed_ms = _clamp(now_ms - window_start_ms, 0.0, window_ms)
    if elapsed_ms <= 0:
        return 0.0
    if not working_days:
        return elapsed_ms / window_ms

    total_working_ms = _working_duration_ms(window_start_ms, reset_at_ms, working_days)
    if total_working_ms <= 0:
        return elapsed_ms / window_ms

    used_working_ms = _working_duration_ms(window_start_ms, window_start_ms + elapsed_ms, working_days)
    return _clamp(used_working_ms / total_working_ms, 0.0, 1.0)


def _working_schedule_share_per_hour(
    *,
    reset_at_ms: float,
    window_ms: float,
    working_days: set[int] | None,
) -> float:
    if not working_days:
        return 3_600_000.0 / window_ms

    window_start_ms = reset_at_ms - window_ms
    total_working_ms = _working_duration_ms(window_start_ms, reset_at_ms, working_days)
    if total_working_ms <= 0:
        return 3_600_000.0 / window_ms
    return 3_600_000.0 / total_working_ms


def _working_duration_ms(start_ms: float, end_ms: float, working_days: set[int]) -> float:
    if end_ms <= start_ms:
        return 0.0

    cursor_ms = start_ms
    total_ms = 0.0
    while cursor_ms < end_ms:
        next_day_ms = _day_start_ms(cursor_ms) + 86_400_000.0
        segment_end_ms = min(end_ms, next_day_ms)
        if _weekday(cursor_ms) in working_days:
            total_ms += segment_end_ms - cursor_ms
        cursor_ms = segment_end_ms
    return total_ms


def _day_start_ms(epoch_ms: float) -> float:
    return float(int(epoch_ms // 86_400_000.0) * 86_400_000)


def _weekday(epoch_ms: float) -> int:
    return datetime.fromtimestamp(epoch_ms / 1000.0, UTC).weekday()


def _legacy_status(runway_status: WeeklyCreditRunwayStatus) -> WeeklyCreditPaceStatus:
    if runway_status == "runs_dry":
        return "danger"
    if runway_status == "tight":
        return "ahead"
    return "on_track"


def _confidence(
    account_count: int,
    rate_sample_count: int,
    stale_account_count: int,
) -> Literal["high", "medium", "low"]:
    if rate_sample_count >= account_count and stale_account_count == 0:
        return "high"
    if rate_sample_count > 0:
        return "medium"
    return "low"


def _freshness_seconds(usage_refresh_interval_seconds: int) -> float:
    return max(MIN_FRESHNESS_SECONDS, float(usage_refresh_interval_seconds) * FRESHNESS_MISSED_REFRESH_CYCLES)


def _clamp(value: float, min_value: float, max_value: float) -> float:
    return min(max_value, max(min_value, value))


def _is_finite_positive(value: object) -> bool:
    return isinstance(value, int | float) and isfinite(value) and value > 0

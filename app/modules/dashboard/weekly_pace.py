from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil, isfinite
from typing import Literal

from app.core.usage.depletion import EWMAState, ewma_update
from app.core.utils.time import naive_utc_to_epoch
from app.db.models import Account, AccountStatus, UsageHistory
from app.modules.accounts.schemas import AccountSummary
from app.modules.dashboard.schemas import WeeklyCreditPaceResponse, WeeklyCreditPaceStatus

PRO_WEEKLY_CAPACITY_CREDITS = 50_400.0
RECENT_BURN_WINDOW = timedelta(hours=6)
MIN_FRESHNESS_SECONDS = 300.0
FRESHNESS_MISSED_REFRESH_CYCLES = 3.0


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
    working_days: set[int] | None = None,
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
    total_expected_remaining_credits = 0.0
    scheduled_burn_rate_credits_per_hour = 0.0
    forecast_burn_rate_credits_per_hour = 0.0

    for summary in account_summaries:
        timing = _weekly_timing(summary, now_ms)
        if timing is None:
            continue

        account = accounts_by_id.get(summary.account_id)
        if account is None or account.status != AccountStatus.ACTIVE:
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

        total_full_credits += full_credits
        total_actual_remaining_credits += actual_remaining_credits
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

    return WeeklyCreditPaceResponse(
        total_full_credits=total_full_credits,
        total_actual_remaining_credits=total_actual_remaining_credits,
        total_expected_remaining_credits=total_expected_remaining_credits,
        actual_used_percent=actual_used_percent,
        scheduled_used_percent=scheduled_used_percent,
        delta_percent=delta_percent,
        schedule_gap_credits=schedule_gap_credits,
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
        status=_weekly_pace_status(delta_percent, projected_shortfall_credits),
        account_count=len(pace_accounts),
        stale_account_count=stale_account_count,
        inactive_account_count=inactive_account_count,
        confidence=_confidence(len(pace_accounts), rate_sample_count, stale_account_count),
    )


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


def _weekly_pace_status(delta_percent: float, projected_shortfall_credits: float) -> WeeklyCreditPaceStatus:
    if projected_shortfall_credits > 0:
        return "danger"
    if delta_percent < -5:
        return "behind"
    if delta_percent > 5:
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

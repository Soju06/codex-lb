from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from datetime import time as dt_time
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.balancer import AccountState, RoutingCost, RoutingCostsByAccount
from app.db.models import AccountStatus

FIVE_HOUR_WINDOW_SECONDS = 5 * 60 * 60
EXPIRING_WINDOW_SECONDS = 60 * 60
STALE_USAGE_SECONDS = 15 * 60
DEFAULT_SLOT_SECONDS = 15 * 60
DEFAULT_PLANNING_HORIZON_HOURS = 36
DEFAULT_ACCOUNT_WINDOW_CAPACITY = 100.0
MIN_PEAK_EXCESS_UNITS = 1.0
_FORECAST_DEMAND_REQUEST_KINDS = frozenset({"normal", "real"})


@dataclass(frozen=True, slots=True)
class PlannerSettings:
    mode: str = "shadow"
    timezone: str = "UTC"
    working_days: tuple[int, ...] = (0, 1, 2, 3, 4)
    working_hours_start: str = "09:00"
    working_hours_end: str = "18:00"
    prewarm_enabled: bool = True
    prewarm_lead_minutes: int = 300
    max_warmups_per_day: int = 3
    max_warmup_credits_per_day: float = 0.0
    min_expected_gain: float = 1.0
    forecast_quantile: str = "p75"
    allow_synthetic_traffic: bool = False
    warmup_model_preference: str | None = None
    dry_run: bool = True


@dataclass(frozen=True, slots=True)
class PlannerAction:
    account_id: str
    action: str
    scheduled_at: datetime | None
    score: float
    reason: str
    target_peak_at: datetime | None = None
    expected_gain: float = 0.0
    expected_cost: float = 0.0
    warmup_cycle_key: str | None = None


class DemandBinLike(Protocol):
    slot_epoch: int
    api_key_id: str | None
    model: str
    reasoning_effort: str | None
    request_kind: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    cost_usd: float
    request_count: int


@dataclass(frozen=True, slots=True)
class DemandForecastSlot:
    slot_start: datetime
    demand_units: float
    request_count: float
    source: str


@dataclass(frozen=True, slots=True)
class SimulationResult:
    loss: float
    unmet_demand: float
    wasted_capacity: float
    cold_start_penalty: float
    synchronization_penalty: float
    forecast_units: float
    served_units: float


@dataclass(frozen=True, slots=True)
class PlannerForecast:
    generated_at: datetime
    horizon_hours: int
    slot_seconds: int
    total_demand_units: float
    peak_slot_start: datetime | None
    peak_demand_units: float
    slots: tuple[DemandForecastSlot, ...]


def _is_forecast_demand_request(request_kind: str | None) -> bool:
    return (request_kind or "real").lower() in _FORECAST_DEMAND_REQUEST_KINDS


def parse_working_days(raw: str | None) -> tuple[int, ...]:
    if not raw:
        return (0, 1, 2, 3, 4)
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return (0, 1, 2, 3, 4)
    days: list[int] = []
    if isinstance(decoded, list):
        for value in decoded:
            if isinstance(value, int) and 0 <= value <= 6 and value not in days:
                days.append(value)
    return tuple(days) or (0, 1, 2, 3, 4)


def encode_working_days(days: tuple[int, ...] | list[int]) -> str:
    normalized = sorted({int(day) for day in days if 0 <= int(day) <= 6})
    return json.dumps(normalized or [0, 1, 2, 3, 4], separators=(",", ":"))


def build_routing_costs(
    *,
    settings: PlannerSettings,
    states: list[AccountState],
    now: datetime | None = None,
) -> RoutingCostsByAccount:
    if settings.mode == "off":
        return {}
    current = now or datetime.now(timezone.utc)
    current_ts = current.timestamp()
    costs: RoutingCostsByAccount = {}
    for state in states:
        cost = 0.0
        reasons: list[str] = []

        if _is_active_window(state, current_ts):
            seconds_left = float(state.reset_at or 0) - current_ts
            if seconds_left <= EXPIRING_WINDOW_SECONDS:
                bonus = 20.0 * (1.0 - max(0.0, seconds_left) / EXPIRING_WINDOW_SECONDS)
                cost -= bonus
                reasons.append("expiring_active_window")
        elif _is_cold_window(state, current_ts):
            if _inside_work_block(current, settings):
                # In work hours, availability wins. The planner only nudges
                # routing toward already-active windows; it must not block work
                # just because there is no historical forecast yet.
                cost += 2.0
                reasons.append("soft_cold_start_during_work")
            elif _inside_prewarm_band(current, settings):
                cost += 12.0
                reasons.append("cold_start_in_prewarm_band")
            else:
                cost += 40.0
                reasons.append("cold_start_outside_work")

        if state.used_percent is None and state.secondary_used_percent is None:
            cost += 3.0
            reasons.append("unknown_usage")

        if cost != 0.0:
            costs[state.account_id] = RoutingCost(total=cost, reason=",".join(reasons))
    return costs


def plan_shadow_actions(
    *,
    settings: PlannerSettings,
    states: list[AccountState],
    demand_forecast: PlannerForecast | None = None,
    now: datetime | None = None,
) -> list[PlannerAction]:
    if settings.mode == "off":
        return []
    current = now or datetime.now(timezone.utc)
    if not settings.prewarm_enabled:
        return []
    if not _inside_prewarm_band(current, settings):
        return []

    actions: list[PlannerAction] = []
    skipped_candidates = 0
    current_ts = current.timestamp()
    active_resets = [
        float(state.reset_at) for state in states if state.reset_at is not None and _is_active_window(state, current_ts)
    ]
    for state in states:
        if not _is_warmup_candidate(state, current_ts):
            continue
        candidate_times = candidate_start_times(
            now=current,
            account=state,
            settings=settings,
            demand_forecast=demand_forecast,
            existing_reset_epochs=active_resets,
        )
        if not candidate_times:
            continue
        selected_reset_epochs = [
            (action.scheduled_at + timedelta(seconds=FIVE_HOUR_WINDOW_SECONDS)).timestamp()
            for action in actions
            if action.scheduled_at is not None
        ]
        scored_candidates = [
            (
                candidate,
                score_candidate_start(
                    scheduled_at=candidate,
                    settings=settings,
                    demand_forecast=demand_forecast,
                    existing_reset_epochs=[*active_resets, *selected_reset_epochs],
                ),
            )
            for candidate in candidate_times
        ]
        scheduled_at, score = max(scored_candidates, key=lambda item: (item[1], -item[0].timestamp()))
        if score < settings.min_expected_gain:
            skipped_candidates += 1
            continue
        reset_at = scheduled_at + timedelta(seconds=FIVE_HOUR_WINDOW_SECONDS)
        target_peak_at = demand_forecast.peak_slot_start if demand_forecast else None
        expected_cost = _candidate_cost(
            scheduled_at=scheduled_at,
            settings=settings,
            existing_reset_epochs=[*active_resets, *selected_reset_epochs],
        )
        actions.append(
            PlannerAction(
                account_id=state.account_id,
                action="warmup" if settings.allow_synthetic_traffic and not settings.dry_run else "reserve",
                scheduled_at=scheduled_at,
                score=score,
                reason=(
                    "peak_phase_alignment"
                    f";target_peak_at={_format_dt(target_peak_at)}"
                    f";reset_at={_format_dt(reset_at)}"
                    f";expected_gain={score + expected_cost:.2f}"
                    f";expected_cost={expected_cost:.2f}"
                    f";warmup_cycle={_warmup_cycle_key(scheduled_at, settings)}"
                ),
                target_peak_at=target_peak_at,
                expected_gain=score + expected_cost,
                expected_cost=expected_cost,
                warmup_cycle_key=_warmup_cycle_key(scheduled_at, settings),
            )
        )
    if skipped_candidates and not actions:
        return []
    actions.sort(key=lambda action: (-action.score, action.scheduled_at or current, action.account_id))
    return actions[: max(0, settings.max_warmups_per_day or 0)]


def build_demand_forecast(
    *,
    settings: PlannerSettings,
    bins: Sequence[DemandBinLike],
    now: datetime | None = None,
    horizon_hours: int = DEFAULT_PLANNING_HORIZON_HOURS,
    slot_seconds: int = DEFAULT_SLOT_SECONDS,
) -> PlannerForecast:
    current = _floor_datetime(now or datetime.now(timezone.utc), slot_seconds)
    history_by_weekday_slot: dict[tuple[int, int], list[float]] = {}
    history_by_work_hour: dict[int, list[float]] = {}
    units_by_slot_epoch: dict[int, float] = {}
    recent_units = 0.0
    recent_cutoff = current.timestamp() - 24 * 60 * 60
    for row in bins:
        if not _is_forecast_demand_request(row.request_kind):
            continue
        slot_epoch = int(row.slot_epoch)
        units_by_slot_epoch[slot_epoch] = units_by_slot_epoch.get(slot_epoch, 0.0) + _bin_demand_units(row)

    for slot_epoch, units in units_by_slot_epoch.items():
        slot = datetime.fromtimestamp(slot_epoch, tz=timezone.utc)
        local = _to_planner_tz(slot, settings.timezone)
        slot_index = local.hour * 3600 // slot_seconds + local.minute * 60 // slot_seconds
        history_by_weekday_slot.setdefault((local.weekday(), slot_index), []).append(units)
        if local.weekday() in settings.working_days:
            history_by_work_hour.setdefault(local.hour, []).append(units)
        if slot_epoch >= recent_cutoff:
            recent_units += units

    recent_per_slot = recent_units / max(1, int(24 * 60 * 60 / slot_seconds))
    slots: list[DemandForecastSlot] = []
    slot_count = int(horizon_hours * 60 * 60 / slot_seconds)
    for offset in range(slot_count):
        slot_start = current + timedelta(seconds=offset * slot_seconds)
        local = _to_planner_tz(slot_start, settings.timezone)
        slot_index = local.hour * 3600 // slot_seconds + local.minute * 60 // slot_seconds
        same_weekday = _quantile(
            history_by_weekday_slot.get((local.weekday(), slot_index), []),
            settings.forecast_quantile,
        )
        same_work_hour = _quantile(history_by_work_hour.get(local.hour, []), settings.forecast_quantile)
        calendar = _calendar_prior_units(slot_start, settings)
        demand_units = 0.50 * same_weekday + 0.25 * same_work_hour + 0.15 * recent_per_slot + 0.10 * calendar
        slots.append(
            DemandForecastSlot(
                slot_start=slot_start,
                demand_units=max(0.0, demand_units),
                request_count=max(0.0, demand_units / 10.0),
                source="history_calendar_blend",
            )
        )

    peak = max(slots, key=lambda slot: slot.demand_units, default=None)
    total = sum(slot.demand_units for slot in slots)
    return PlannerForecast(
        generated_at=current,
        horizon_hours=horizon_hours,
        slot_seconds=slot_seconds,
        total_demand_units=total,
        peak_slot_start=peak.slot_start if peak and peak.demand_units > 0 else None,
        peak_demand_units=peak.demand_units if peak else 0.0,
        slots=tuple(slots),
    )


def simulate_pool(
    *,
    settings: PlannerSettings,
    states: list[AccountState],
    demand_forecast: PlannerForecast,
    planned_warmups: list[PlannerAction] | None = None,
    now: datetime | None = None,
) -> SimulationResult:
    current = now or demand_forecast.generated_at
    current_ts = current.timestamp()
    warmups = planned_warmups or []
    active_windows: list[tuple[float, float, float]] = []
    for state in states:
        if state.status not in {AccountStatus.ACTIVE, AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED}:
            continue
        if _is_active_window(state, current_ts):
            remaining_pct = _remaining_percent(state)
            active_windows.append(
                (current_ts, float(state.reset_at or 0), DEFAULT_ACCOUNT_WINDOW_CAPACITY * remaining_pct / 100.0)
            )
    for action in warmups:
        if action.scheduled_at is None:
            continue
        start = action.scheduled_at.timestamp()
        active_windows.append((start, start + FIVE_HOUR_WINDOW_SECONDS, DEFAULT_ACCOUNT_WINDOW_CAPACITY))

    unmet = 0.0
    served = 0.0
    wasted_capacity = 0.0
    remaining_by_window: dict[tuple[float, float], float] = {}
    for start_at, reset_at, capacity in active_windows:
        if start_at >= reset_at:
            continue
        remaining_by_window[(start_at, reset_at)] = remaining_by_window.get((start_at, reset_at), 0.0) + capacity
    for slot in demand_forecast.slots:
        slot_ts = slot.slot_start.timestamp()
        demand = slot.demand_units
        usable_windows = sorted((start, reset) for start, reset in remaining_by_window if start <= slot_ts < reset)
        for start, reset in usable_windows:
            if demand <= 0:
                break
            key = (start, reset)
            take = min(demand, remaining_by_window[key])
            remaining_by_window[key] -= take
            demand -= take
            served += take
        unmet += max(0.0, demand)

    for (_, reset), remaining in remaining_by_window.items():
        if reset <= (current + timedelta(hours=demand_forecast.horizon_hours)).timestamp():
            wasted_capacity += max(0.0, remaining) * 0.05

    sync_penalty = _synchronization_penalty([reset for _, reset, _ in active_windows])
    cold_penalty = sum(
        2.0
        for action in warmups
        if action.action == "warmup" and action.scheduled_at and action.scheduled_at <= current
    )
    loss = unmet + wasted_capacity + sync_penalty + cold_penalty
    return SimulationResult(
        loss=loss,
        unmet_demand=unmet,
        wasted_capacity=wasted_capacity,
        cold_start_penalty=cold_penalty,
        synchronization_penalty=sync_penalty,
        forecast_units=demand_forecast.total_demand_units,
        served_units=served,
    )


def candidate_start_times(
    *,
    now: datetime,
    account: AccountState,
    settings: PlannerSettings,
    demand_forecast: PlannerForecast | None,
    existing_reset_epochs: list[float] | None = None,
) -> list[datetime]:
    del account
    candidates: list[datetime] = []
    if _inside_work_block(now, settings):
        candidates.append(now)
    candidates.extend(_next_work_starts(now, settings, count=2, offsets=(-4, -3, -2)))
    if demand_forecast and demand_forecast.peak_slot_start is not None:
        peak_start = demand_forecast.peak_slot_start - timedelta(seconds=FIVE_HOUR_WINDOW_SECONDS)
        candidates.extend(peak_start + timedelta(minutes=offset) for offset in (-60, -30, 0, 30, 60))
        candidates.extend(_demand_capacity_crossing_starts(demand_forecast))
    for reset_epoch in existing_reset_epochs or []:
        reset_at = datetime.fromtimestamp(reset_epoch, tz=timezone.utc)
        candidates.append(reset_at + timedelta(minutes=30) - timedelta(seconds=FIVE_HOUR_WINDOW_SECONDS))
        candidates.append(reset_at - timedelta(minutes=30) - timedelta(seconds=FIVE_HOUR_WINDOW_SECONDS))

    normalized: list[datetime] = []
    seen: set[int] = set()
    for candidate in candidates:
        if candidate < now:
            candidate = now
        candidate = _floor_datetime(candidate, DEFAULT_SLOT_SECONDS)
        if candidate < now:
            candidate = now
        key = int(candidate.timestamp())
        if key in seen:
            continue
        seen.add(key)
        if _inside_prewarm_band(candidate, settings):
            normalized.append(candidate)
    normalized.sort()
    return normalized[:10]


def score_candidate_start(
    *,
    scheduled_at: datetime,
    settings: PlannerSettings,
    demand_forecast: PlannerForecast | None,
    existing_reset_epochs: list[float] | None = None,
) -> float:
    candidate_cost = _candidate_cost(
        scheduled_at=scheduled_at,
        settings=settings,
        existing_reset_epochs=existing_reset_epochs,
    )
    if demand_forecast is None:
        return 0.0
    peak_gain = _peak_aligned_gain(
        scheduled_at=scheduled_at,
        settings=settings,
        demand_forecast=demand_forecast,
    )
    return max(0.0, peak_gain - candidate_cost)


def _candidate_cost(
    *,
    scheduled_at: datetime,
    settings: PlannerSettings,
    existing_reset_epochs: list[float] | None = None,
) -> float:
    window_end = scheduled_at + timedelta(seconds=FIVE_HOUR_WINDOW_SECONDS)
    sync_cost = _synchronization_penalty([*(existing_reset_epochs or []), window_end.timestamp()])
    synthetic_cost = 0.5 if settings.allow_synthetic_traffic and not settings.dry_run else 0.0
    return sync_cost + synthetic_cost


def _peak_aligned_gain(
    *,
    scheduled_at: datetime,
    settings: PlannerSettings,
    demand_forecast: PlannerForecast,
) -> float:
    work_slots = [slot for slot in demand_forecast.slots if _inside_work_block(slot.slot_start, settings)]
    if not work_slots or demand_forecast.peak_slot_start is None:
        return 0.0
    baseline = _quantile([slot.demand_units for slot in work_slots], "p50")
    peak_excess = max(0.0, demand_forecast.peak_demand_units - baseline)
    if peak_excess < MIN_PEAK_EXCESS_UNITS:
        return 0.0

    window_end = scheduled_at + timedelta(seconds=FIVE_HOUR_WINDOW_SECONDS)
    window_slots = [slot for slot in work_slots if scheduled_at <= slot.slot_start < window_end]
    reset_aligns_peak = (
        abs((window_end - demand_forecast.peak_slot_start).total_seconds()) <= demand_forecast.slot_seconds
    )
    if not window_slots and not reset_aligns_peak:
        return 0.0

    window_units = sum(slot.demand_units for slot in window_slots)
    baseline_units = baseline * len(window_slots)
    window_excess = max(0.0, window_units - baseline_units)
    peak_covered = scheduled_at <= demand_forecast.peak_slot_start < window_end
    reset_bonus = peak_excess if reset_aligns_peak else 0.0
    if not peak_covered:
        return max(0.0, window_excess - peak_excess) + reset_bonus
    return window_excess + peak_excess + reset_bonus


def _demand_capacity_crossing_starts(demand_forecast: PlannerForecast) -> list[datetime]:
    candidates: list[datetime] = []
    cumulative = 0.0
    next_bucket = DEFAULT_ACCOUNT_WINDOW_CAPACITY
    for slot in demand_forecast.slots:
        cumulative += slot.demand_units
        if cumulative < next_bucket:
            continue
        candidates.append(slot.slot_start - timedelta(seconds=FIVE_HOUR_WINDOW_SECONDS))
        next_bucket += DEFAULT_ACCOUNT_WINDOW_CAPACITY
    return candidates[:4]


def _is_active_window(state: AccountState, current_ts: float) -> bool:
    return state.reset_at is not None and float(state.reset_at) > current_ts


def _is_cold_window(state: AccountState, current_ts: float) -> bool:
    return state.reset_at is None or float(state.reset_at) <= current_ts


def _is_warmup_candidate(state: AccountState, current_ts: float) -> bool:
    if state.status != AccountStatus.ACTIVE:
        return False
    return _is_cold_window(state, current_ts)


def _inside_work_block(value: datetime, settings: PlannerSettings) -> bool:
    local = _to_planner_tz(value, settings.timezone)
    if local.weekday() not in settings.working_days:
        return False
    start = _parse_hhmm(settings.working_hours_start, dt_time(9, 0))
    end = _parse_hhmm(settings.working_hours_end, dt_time(18, 0))
    current_time = local.time()
    if start <= end:
        return start <= current_time < end
    return current_time >= start or current_time < end


def _inside_prewarm_band(value: datetime, settings: PlannerSettings) -> bool:
    if _inside_work_block(value, settings):
        return True
    lead = timedelta(minutes=max(0, settings.prewarm_lead_minutes))
    return _inside_work_block(value + lead, settings)


def _to_planner_tz(value: datetime, timezone_name: str) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        tz = timezone.utc
    return value.astimezone(tz)


def _parse_hhmm(raw: str, fallback: dt_time) -> dt_time:
    try:
        hours, minutes = raw.split(":", 1)
        return dt_time(int(hours), int(minutes))
    except (TypeError, ValueError):
        return fallback


def _bin_demand_units(row: DemandBinLike) -> float:
    token_units = (
        max(0, row.input_tokens) + 0.25 * max(0, row.cached_input_tokens) + 4.0 * max(0, row.output_tokens)
    ) / 1000.0
    cost_units = max(0.0, row.cost_usd) * 100.0
    request_units = max(0, row.request_count) * 5.0
    return max(token_units, cost_units, request_units)


def _quantile(values: list[float], quantile: str) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    q = {"p50": 0.50, "p75": 0.75, "p90": 0.90}.get(quantile, 0.75)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[index]


def _calendar_prior_units(value: datetime, settings: PlannerSettings) -> float:
    if _inside_work_block(value, settings):
        return 6.0
    if _inside_prewarm_band(value, settings):
        return 2.0
    return 0.2


def _remaining_percent(state: AccountState) -> float:
    if state.used_percent is None:
        return 100.0
    return max(0.0, 100.0 - min(100.0, state.used_percent))


def _floor_datetime(value: datetime, slot_seconds: int) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    epoch = int(value.timestamp())
    return datetime.fromtimestamp(epoch - (epoch % slot_seconds), tz=timezone.utc)


def _next_work_starts(
    now: datetime,
    settings: PlannerSettings,
    *,
    count: int,
    offsets: tuple[int, ...],
) -> list[datetime]:
    local_now = _to_planner_tz(now, settings.timezone)
    start = _parse_hhmm(settings.working_hours_start, dt_time(9, 0))
    results: list[datetime] = []
    for day_offset in range(0, 14):
        candidate_day = local_now.date() + timedelta(days=day_offset)
        local_start = datetime.combine(candidate_day, start, tzinfo=local_now.tzinfo)
        if local_start.weekday() not in settings.working_days or local_start < local_now:
            continue
        for hours in offsets:
            results.append((local_start + timedelta(hours=hours)).astimezone(timezone.utc))
        if len(results) >= count * len(offsets):
            break
    return results


def _synchronization_penalty(reset_epochs: list[float]) -> float:
    penalty = 0.0
    ordered = sorted(reset_epochs)
    for left, right in zip(ordered, ordered[1:]):
        delta = abs(right - left)
        if delta < 30 * 60:
            penalty += 4.0
        elif delta < 60 * 60:
            penalty += 1.0
    return penalty


def _warmup_cycle_key(scheduled_at: datetime, settings: PlannerSettings) -> str:
    local = _to_planner_tz(scheduled_at, settings.timezone)
    lead = max(1, settings.prewarm_lead_minutes)
    cycle_minute = (local.hour * 60 + local.minute) // lead
    return f"{local:%Y%m%d}:warmup_cycle:{cycle_minute}"


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "none"
    return value.astimezone(timezone.utc).isoformat()

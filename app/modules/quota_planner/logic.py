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
        scored_candidates = (
            (
                candidate,
                score_candidate_start(
                    scheduled_at=candidate,
                    settings=settings,
                    demand_forecast=demand_forecast,
                    existing_reset_epochs=active_resets,
                ),
            )
            for candidate in candidate_times
        )
        scheduled_at, score = max(scored_candidates, key=lambda item: (item[1], -item[0].timestamp()))
        if score < settings.min_expected_gain:
            continue
        actions.append(
            PlannerAction(
                account_id=state.account_id,
                action="warmup" if settings.allow_synthetic_traffic and not settings.dry_run else "reserve",
                scheduled_at=scheduled_at,
                score=score,
                reason="forecast_phase_alignment",
            )
        )
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
    recent_units = 0.0
    recent_cutoff = current.timestamp() - 24 * 60 * 60
    for row in bins:
        if row.request_kind != "real":
            continue
        slot = datetime.fromtimestamp(row.slot_epoch, tz=timezone.utc)
        local = _to_planner_tz(slot, settings.timezone)
        slot_index = local.hour * 3600 // slot_seconds + local.minute * 60 // slot_seconds
        units = _bin_demand_units(row)
        history_by_weekday_slot.setdefault((local.weekday(), slot_index), []).append(units)
        if local.weekday() in settings.working_days:
            history_by_work_hour.setdefault(local.hour, []).append(units)
        if row.slot_epoch >= recent_cutoff:
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
    active_windows: list[tuple[float, float]] = []
    for state in states:
        if state.status not in {AccountStatus.ACTIVE, AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED}:
            continue
        if _is_active_window(state, current_ts):
            remaining_pct = _remaining_percent(state)
            active_windows.append((float(state.reset_at or 0), DEFAULT_ACCOUNT_WINDOW_CAPACITY * remaining_pct / 100.0))
    for action in warmups:
        if action.scheduled_at is None:
            continue
        start = action.scheduled_at.timestamp()
        active_windows.append((start + FIVE_HOUR_WINDOW_SECONDS, DEFAULT_ACCOUNT_WINDOW_CAPACITY))

    unmet = 0.0
    served = 0.0
    wasted_capacity = 0.0
    remaining_by_reset = {reset_at: capacity for reset_at, capacity in active_windows}
    for slot in demand_forecast.slots:
        slot_ts = slot.slot_start.timestamp()
        demand = slot.demand_units
        usable_resets = sorted(reset for reset in remaining_by_reset if reset > slot_ts)
        for reset in usable_resets:
            if demand <= 0:
                break
            take = min(demand, remaining_by_reset[reset])
            remaining_by_reset[reset] -= take
            demand -= take
            served += take
        unmet += max(0.0, demand)

    for reset, remaining in remaining_by_reset.items():
        if reset <= (current + timedelta(hours=demand_forecast.horizon_hours)).timestamp():
            wasted_capacity += max(0.0, remaining) * 0.05

    sync_penalty = _synchronization_penalty([reset for reset, _ in active_windows])
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
        candidates.append(demand_forecast.peak_slot_start - timedelta(seconds=FIVE_HOUR_WINDOW_SECONDS))
    for reset_epoch in existing_reset_epochs or []:
        candidates.append(datetime.fromtimestamp(reset_epoch, tz=timezone.utc) + timedelta(minutes=30))

    normalized: list[datetime] = []
    seen: set[int] = set()
    for candidate in candidates:
        if candidate < now:
            candidate = now
        candidate = _floor_datetime(candidate, DEFAULT_SLOT_SECONDS)
        key = int(candidate.timestamp())
        if key in seen:
            continue
        seen.add(key)
        if _inside_prewarm_band(candidate, settings):
            normalized.append(candidate)
    normalized.sort()
    return normalized[:6]


def score_candidate_start(
    *,
    scheduled_at: datetime,
    settings: PlannerSettings,
    demand_forecast: PlannerForecast | None,
    existing_reset_epochs: list[float] | None = None,
) -> float:
    window_end = scheduled_at + timedelta(seconds=FIVE_HOUR_WINDOW_SECONDS)
    demand_gain = 0.0
    if demand_forecast is not None:
        demand_gain = sum(
            slot.demand_units
            for slot in demand_forecast.slots
            if scheduled_at <= slot.slot_start < window_end and _inside_work_block(slot.slot_start, settings)
        )
    else:
        target = scheduled_at + timedelta(minutes=settings.prewarm_lead_minutes)
        demand_gain = 10.0 if _inside_work_block(target, settings) else 2.0
    sync_cost = _synchronization_penalty([*(existing_reset_epochs or []), window_end.timestamp()])
    synthetic_cost = 0.5 if settings.allow_synthetic_traffic and not settings.dry_run else 0.0
    return max(0.0, demand_gain - sync_cost - synthetic_cost)


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

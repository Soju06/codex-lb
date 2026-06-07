from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.balancer import AccountState
from app.db.models import AccountStatus
from app.modules.quota_planner.logic import (
    DemandForecastSlot,
    PlannerAction,
    PlannerForecast,
    PlannerSettings,
    build_demand_forecast,
    build_routing_costs,
    candidate_start_times,
    parse_working_days,
    plan_shadow_actions,
    simulate_pool,
)
from app.modules.quota_planner.repository import DemandBin

pytestmark = pytest.mark.unit


def _forecast(
    now: datetime,
    *,
    peak_at: datetime,
    peak_units: float = 80.0,
    flat_units: float = 0.0,
    hours: int = 14,
) -> PlannerForecast:
    slots = []
    for offset in range(hours * 4):
        slot_start = now + timedelta(minutes=15 * offset)
        units = flat_units
        if slot_start == peak_at:
            units = peak_units
        slots.append(
            DemandForecastSlot(
                slot_start=slot_start,
                demand_units=units,
                request_count=units / 10,
                source="test",
            )
        )
    return PlannerForecast(
        generated_at=now,
        horizon_hours=hours,
        slot_seconds=15 * 60,
        total_demand_units=sum(slot.demand_units for slot in slots),
        peak_slot_start=peak_at,
        peak_demand_units=peak_units,
        slots=tuple(slots),
    )


def test_parse_working_days_falls_back_for_invalid_json() -> None:
    assert parse_working_days("not json") == (0, 1, 2, 3, 4)
    assert parse_working_days("[1,1,8,2]") == (1, 2)


def test_build_routing_costs_penalizes_cold_accounts_outside_work() -> None:
    settings = PlannerSettings(
        mode="shadow",
        timezone="UTC",
        working_days=(0,),
        working_hours_start="09:00",
        working_hours_end="18:00",
    )
    now = datetime(2026, 5, 18, 3, 0, tzinfo=timezone.utc)
    states = [
        AccountState("cold", AccountStatus.ACTIVE, used_percent=0.0, reset_at=None),
        AccountState("active", AccountStatus.ACTIVE, used_percent=50.0, reset_at=now.timestamp() + 1800),
    ]

    costs = build_routing_costs(settings=settings, states=states, now=now)

    assert costs["cold"].total == 40.0
    assert costs["cold"].reason == "cold_start_outside_work"
    assert costs["active"].total < 0.0
    assert costs["active"].reason == "expiring_active_window"


def test_planner_settings_default_to_nonblocking_shadow_mode() -> None:
    settings = PlannerSettings()

    assert settings.mode == "shadow"
    assert settings.prewarm_enabled is True
    assert settings.allow_synthetic_traffic is False
    assert settings.dry_run is True


def test_candidate_start_times_do_not_floor_now_into_the_past() -> None:
    settings = PlannerSettings(
        mode="shadow",
        timezone="UTC",
        working_days=(0,),
        working_hours_start="09:00",
        working_hours_end="18:00",
    )
    now = datetime(2026, 5, 18, 9, 7, tzinfo=timezone.utc)

    candidates = candidate_start_times(
        now=now,
        account=AccountState("cold", AccountStatus.ACTIVE, used_percent=0.0, reset_at=None),
        settings=settings,
        demand_forecast=None,
    )

    assert candidates
    assert all(candidate >= now for candidate in candidates)


def test_simulation_sums_capacity_for_matching_reset_epochs() -> None:
    settings = PlannerSettings()
    now = datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc)
    reset_at = now.timestamp() + 3600
    forecast = PlannerForecast(
        generated_at=now,
        horizon_hours=1,
        slot_seconds=15 * 60,
        total_demand_units=120.0,
        peak_slot_start=now,
        peak_demand_units=120.0,
        slots=(
            DemandForecastSlot(
                slot_start=now,
                demand_units=120.0,
                request_count=12.0,
                source="test",
            ),
        ),
    )
    states = [
        AccountState("a", AccountStatus.ACTIVE, used_percent=40.0, reset_at=reset_at),
        AccountState("b", AccountStatus.ACTIVE, used_percent=40.0, reset_at=reset_at),
    ]

    simulation = simulate_pool(settings=settings, states=states, demand_forecast=forecast, now=now)

    assert simulation.served_units == pytest.approx(120.0)
    assert simulation.unmet_demand == pytest.approx(0.0)


def test_simulate_pool_does_not_use_future_warmup_before_start() -> None:
    settings = PlannerSettings()
    now = datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc)
    planned = datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    forecast = PlannerForecast(
        generated_at=now,
        horizon_hours=1,
        slot_seconds=15 * 60,
        total_demand_units=120.0,
        peak_slot_start=now,
        peak_demand_units=120.0,
        slots=(
            DemandForecastSlot(
                slot_start=now,
                demand_units=60.0,
                request_count=6.0,
                source="test",
            ),
            DemandForecastSlot(
                slot_start=now + timedelta(minutes=15),
                demand_units=60.0,
                request_count=6.0,
                source="test",
            ),
        ),
    )

    simulation = simulate_pool(
        settings=settings,
        states=[],
        demand_forecast=forecast,
        planned_warmups=[
            PlannerAction(
                account_id="acc",
                action="reserve",
                scheduled_at=planned,
                score=1.0,
                reason="unit",
            )
        ],
        now=now,
    )

    assert simulation.served_units == pytest.approx(0.0)
    assert simulation.unmet_demand == pytest.approx(120.0)


def test_plan_shadow_actions_reserves_cold_accounts_for_peak_aligned_staggered_windows() -> None:
    settings = PlannerSettings(
        mode="shadow",
        timezone="UTC",
        working_days=(0,),
        working_hours_start="09:00",
        working_hours_end="18:00",
        prewarm_enabled=True,
        prewarm_lead_minutes=300,
        max_warmups_per_day=2,
        min_expected_gain=1.0,
    )
    now = datetime(2026, 5, 18, 5, 0, tzinfo=timezone.utc)
    peak_at = datetime(2026, 5, 18, 13, 0, tzinfo=timezone.utc)
    states = [
        AccountState("cold-a", AccountStatus.ACTIVE, used_percent=0.0, reset_at=None),
        AccountState("cold-b", AccountStatus.ACTIVE, used_percent=0.0, reset_at=now.timestamp() - 1),
        AccountState("active", AccountStatus.ACTIVE, used_percent=10.0, reset_at=now.timestamp() + 60),
    ]

    actions = plan_shadow_actions(
        settings=settings,
        states=states,
        demand_forecast=_forecast(now, peak_at=peak_at),
        now=now,
    )

    assert [action.account_id for action in actions] == ["cold-a", "cold-b"]
    assert {action.action for action in actions} == {"reserve"}
    assert len({action.scheduled_at for action in actions}) == 2
    assert all(action.target_peak_at == peak_at for action in actions)
    assert all(action.warmup_cycle_key for action in actions)
    assert actions[0].scheduled_at != now
    assert all(action.scheduled_at is not None for action in actions)
    now_reset_gap = abs((peak_at - (now + timedelta(hours=5))).total_seconds())
    planned_reset_gaps = [
        abs((peak_at - (action.scheduled_at + timedelta(hours=5))).total_seconds())
        for action in actions
        if action.scheduled_at is not None
    ]
    assert max(planned_reset_gaps) < now_reset_gap
    assert actions[0].score > 0


def test_plan_shadow_actions_keeps_accounts_cold_when_flat_demand_has_no_peak_gain() -> None:
    settings = PlannerSettings(
        mode="shadow",
        timezone="UTC",
        working_days=(0,),
        working_hours_start="09:00",
        working_hours_end="18:00",
        prewarm_enabled=True,
        prewarm_lead_minutes=300,
        max_warmups_per_day=2,
        min_expected_gain=1.0,
    )
    now = datetime(2026, 5, 18, 5, 0, tzinfo=timezone.utc)
    peak_at = datetime(2026, 5, 18, 13, 0, tzinfo=timezone.utc)
    states = [
        AccountState("cold-a", AccountStatus.ACTIVE, used_percent=0.0, reset_at=None),
        AccountState("cold-b", AccountStatus.ACTIVE, used_percent=0.0, reset_at=None),
    ]

    actions = plan_shadow_actions(
        settings=settings,
        states=states,
        demand_forecast=_forecast(now, peak_at=peak_at, peak_units=5.0, flat_units=5.0),
        now=now,
    )

    assert actions == []


def test_plan_shadow_actions_rejects_start_that_misses_peak() -> None:
    settings = PlannerSettings(
        mode="shadow",
        timezone="UTC",
        working_days=(0,),
        working_hours_start="09:00",
        working_hours_end="18:00",
        prewarm_enabled=True,
        prewarm_lead_minutes=300,
        max_warmups_per_day=1,
        min_expected_gain=100.0,
    )
    now = datetime(2026, 5, 18, 5, 0, tzinfo=timezone.utc)
    peak_at = datetime(2026, 5, 18, 23, 0, tzinfo=timezone.utc)
    states = [AccountState("cold", AccountStatus.ACTIVE, used_percent=0.0, reset_at=None)]

    actions = plan_shadow_actions(
        settings=settings,
        states=states,
        demand_forecast=_forecast(now, peak_at=peak_at, peak_units=80.0),
        now=now,
    )

    assert actions == []


def test_forecast_and_simulation_use_history_without_requiring_user_input() -> None:
    settings = PlannerSettings(
        mode="shadow",
        timezone="UTC",
        working_days=(0,),
        working_hours_start="09:00",
        working_hours_end="18:00",
        prewarm_enabled=True,
        max_warmups_per_day=1,
    )
    now = datetime(2026, 5, 18, 5, 0, tzinfo=timezone.utc)
    history_slot = int(datetime(2026, 5, 11, 10, 0, tzinfo=timezone.utc).timestamp())
    bins = [
        DemandBin(
            slot_epoch=history_slot,
            account_id="acc-history",
            api_key_id="key",
            model="gpt-5.4",
            reasoning_effort=None,
            request_kind="real",
            status="ok",
            input_tokens=20_000,
            cached_input_tokens=0,
            output_tokens=2_000,
            cost_usd=0.0,
            request_count=3,
        )
    ]
    states = [
        AccountState("cold", AccountStatus.ACTIVE, used_percent=0.0, reset_at=None),
        AccountState("active", AccountStatus.ACTIVE, used_percent=40.0, reset_at=now.timestamp() + 3600),
    ]

    forecast = build_demand_forecast(settings=settings, bins=bins, now=now, horizon_hours=12)
    actions = plan_shadow_actions(settings=settings, states=states, demand_forecast=forecast, now=now)
    simulation = simulate_pool(
        settings=settings,
        states=states,
        demand_forecast=forecast,
        planned_warmups=actions,
        now=now,
    )

    assert forecast.total_demand_units > 0
    assert actions
    assert actions[0].action == "reserve"
    assert simulation.forecast_units == forecast.total_demand_units


def test_build_demand_forecast_aggregates_same_slot_rows_before_quantile() -> None:
    settings = PlannerSettings(
        mode="shadow",
        timezone="UTC",
        working_days=(0,),
        working_hours_start="09:00",
        working_hours_end="18:00",
        forecast_quantile="p50",
    )
    now = datetime(2026, 5, 18, 5, 0, tzinfo=timezone.utc)
    history_slot = int(datetime(2026, 5, 11, 10, 0, tzinfo=timezone.utc).timestamp())

    bins = [
        DemandBin(
            slot_epoch=history_slot,
            account_id="acc-one",
            api_key_id="key-a",
            model="gpt-5.4",
            reasoning_effort=None,
            request_kind="real",
            status="ok",
            input_tokens=50_000,
            cached_input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            request_count=1,
        ),
        DemandBin(
            slot_epoch=history_slot,
            account_id="acc-two",
            api_key_id="key-b",
            model="gpt-5.4",
            reasoning_effort=None,
            request_kind="real",
            status="ok",
            input_tokens=50_000,
            cached_input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            request_count=1,
        ),
    ]

    forecast = build_demand_forecast(settings=settings, bins=bins, now=now, horizon_hours=6)
    peak_slot = next(slot for slot in forecast.slots if slot.slot_start.hour == 10)

    assert peak_slot.demand_units == pytest.approx(75.6)


def test_build_demand_forecast_uses_current_proxy_history_rows() -> None:
    settings = PlannerSettings(
        mode="shadow",
        timezone="UTC",
        working_days=(0,),
        working_hours_start="09:00",
        working_hours_end="18:00",
        forecast_quantile="p50",
    )
    now = datetime(2026, 5, 18, 5, 0, tzinfo=timezone.utc)
    history_slot = int(datetime(2026, 5, 11, 10, 0, tzinfo=timezone.utc).timestamp())

    bins = [
        DemandBin(
            slot_epoch=history_slot,
            account_id="acc-active",
            api_key_id="key-a",
            model="gpt-5.4",
            reasoning_effort=None,
            request_kind="normal",
            status="ok",
            input_tokens=40_000,
            cached_input_tokens=0,
            output_tokens=10_000,
            cost_usd=0.0,
            request_count=2,
        ),
        DemandBin(
            slot_epoch=history_slot,
            account_id="acc-warm",
            api_key_id="key-b",
            model="gpt-5.4-mini",
            reasoning_effort=None,
            request_kind="warmup",
            status="ok",
            input_tokens=90_000,
            cached_input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            request_count=1,
        ),
    ]

    forecast = build_demand_forecast(settings=settings, bins=bins, now=now, horizon_hours=6)
    peak_slot = next(slot for slot in forecast.slots if slot.slot_start.hour == 10)

    assert peak_slot.demand_units == pytest.approx(60.6)

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.balancer import AccountState
from app.db.models import AccountStatus
from app.modules.quota_planner.logic import (
    PlannerSettings,
    build_demand_forecast,
    build_routing_costs,
    parse_working_days,
    plan_shadow_actions,
    simulate_pool,
)
from app.modules.quota_planner.repository import DemandBin

pytestmark = pytest.mark.unit


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


def test_plan_shadow_actions_reserves_cold_accounts_in_prewarm_band() -> None:
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
    states = [
        AccountState("cold-a", AccountStatus.ACTIVE, used_percent=0.0, reset_at=None),
        AccountState("cold-b", AccountStatus.ACTIVE, used_percent=0.0, reset_at=now.timestamp() - 1),
        AccountState("active", AccountStatus.ACTIVE, used_percent=10.0, reset_at=now.timestamp() + 60),
    ]

    actions = plan_shadow_actions(settings=settings, states=states, now=now)

    assert [action.account_id for action in actions] == ["cold-a", "cold-b"]
    assert {action.action for action in actions} == {"reserve"}


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

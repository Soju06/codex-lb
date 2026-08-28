from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.balancer import AccountState
from app.db.models import Account, AccountStatus, RequestLog
from app.db.session import SessionLocal
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
from app.modules.quota_planner.repository import DemandBin, QuotaPlannerRepository, _to_db_naive_utc

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
        AccountState("cold", AccountStatus.ACTIVE, used_percent=0.0, primary_window_minutes=300),
        AccountState(
            "active",
            AccountStatus.ACTIVE,
            used_percent=50.0,
            primary_reset_at=int(now.timestamp() + 1800),
            primary_window_minutes=300,
        ),
    ]

    costs = build_routing_costs(settings=settings, states=states, now=now)

    assert costs["cold"].total == 40.0
    assert costs["cold"].reason == "cold_start_outside_work"
    assert costs["active"].total < 0.0
    assert costs["active"].reason == "expiring_active_window"


def test_build_routing_costs_skips_accounts_without_short_windows() -> None:
    settings = PlannerSettings(
        mode="shadow",
        timezone="UTC",
        working_days=(0,),
        working_hours_start="09:00",
        working_hours_end="18:00",
    )
    now = datetime(2026, 5, 18, 3, 0, tzinfo=timezone.utc)
    states = [
        # Weekly-only account: the weekly-primary remap clears primary fields.
        AccountState("weekly-only", AccountStatus.ACTIVE, secondary_used_percent=40.0),
        # Short-window account stays cold-costed.
        AccountState("cold-5h", AccountStatus.ACTIVE, used_percent=0.0, primary_window_minutes=300),
    ]

    costs = build_routing_costs(settings=settings, states=states, now=now)

    assert "weekly-only" not in costs
    assert costs["cold-5h"].total == 40.0


def test_build_routing_costs_treats_live_primary_window_as_active() -> None:
    settings = PlannerSettings(
        mode="shadow",
        timezone="UTC",
        working_days=(0,),
        working_hours_start="09:00",
        working_hours_end="18:00",
    )
    now = datetime(2026, 5, 18, 3, 0, tzinfo=timezone.utc)
    # A healthy ACTIVE account mid-way through a live window carries the
    # window state in primary_reset_at (reset_at is blocked-status only).
    states = [
        AccountState(
            "mid-window",
            AccountStatus.ACTIVE,
            used_percent=50.0,
            primary_reset_at=int(now.timestamp() + 4 * 3600),
            primary_window_minutes=300,
        ),
    ]

    costs = build_routing_costs(settings=settings, states=states, now=now)

    assert "mid-window" not in costs


def test_build_routing_costs_ignores_long_window_primary_samples() -> None:
    settings = PlannerSettings(
        mode="shadow",
        timezone="UTC",
        working_days=(0,),
        working_hours_start="09:00",
        working_hours_end="18:00",
    )
    now = datetime(2026, 5, 18, 3, 0, tzinfo=timezone.utc)
    # A long unremapped primary window (25h) is not phase-plannable: it must
    # neither earn an expiring-active-window bonus nor a cold-start cost.
    states = [
        AccountState(
            "long-window",
            AccountStatus.ACTIVE,
            used_percent=50.0,
            primary_reset_at=int(now.timestamp() + 1800),
            primary_window_minutes=1500,
        ),
    ]

    costs = build_routing_costs(settings=settings, states=states, now=now)

    assert "long-window" not in costs


def test_plan_shadow_actions_excludes_long_window_resets_from_stagger_math() -> None:
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
    long_reset = int(now.timestamp() + 1800)
    states = [
        AccountState(
            "long-window",
            AccountStatus.ACTIVE,
            used_percent=50.0,
            primary_reset_at=long_reset,
            primary_window_minutes=1500,
        ),
        AccountState("cold-5h", AccountStatus.ACTIVE, used_percent=0.0, primary_window_minutes=300),
    ]

    actions = plan_shadow_actions(
        settings=settings,
        states=states,
        demand_forecast=_forecast(now, peak_at=peak_at),
        now=now,
    )

    assert actions
    assert {action.account_id for action in actions} == {"cold-5h"}
    # The long window's reset must not act as an active-reset stagger
    # anchor: no candidate is derived from it.
    for action in actions:
        assert action.scheduled_at is not None
        anchored = abs((action.scheduled_at + timedelta(seconds=action.window_seconds)).timestamp() - long_reset) in (
            1800.0,
        )
        assert not anchored


def test_plan_shadow_actions_skips_weekly_only_accounts() -> None:
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
        AccountState("weekly-only", AccountStatus.ACTIVE, secondary_used_percent=10.0),
        AccountState("cold-5h", AccountStatus.ACTIVE, used_percent=0.0, primary_window_minutes=300),
    ]

    actions = plan_shadow_actions(
        settings=settings,
        states=states,
        demand_forecast=_forecast(now, peak_at=peak_at),
        now=now,
    )

    assert actions
    assert {action.account_id for action in actions} == {"cold-5h"}


def test_plan_shadow_actions_uses_observed_window_duration() -> None:
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
        AccountState("cold-1h", AccountStatus.ACTIVE, used_percent=0.0, primary_window_minutes=60),
    ]

    actions = plan_shadow_actions(
        settings=settings,
        states=states,
        demand_forecast=_forecast(now, peak_at=peak_at),
        now=now,
    )

    assert actions
    action = actions[0]
    assert action.window_seconds == pytest.approx(3600.0)
    assert action.scheduled_at is not None
    planned_reset = action.scheduled_at + timedelta(seconds=action.window_seconds)
    # The one-hour window is planned so its span or reset aligns with the
    # peak, which a 5h assumption could not do from these candidates.
    assert abs((planned_reset - peak_at).total_seconds()) <= 3600.0


def test_planner_settings_default_to_nonblocking_shadow_mode() -> None:
    settings = PlannerSettings()

    assert settings.mode == "shadow"
    assert settings.prewarm_enabled is True
    assert settings.allow_synthetic_traffic is False
    assert settings.dry_run is True


def test_to_db_naive_utc_normalizes_aware_datetimes() -> None:
    scheduled_at = datetime(2026, 6, 18, 8, 30, tzinfo=timezone(timedelta(hours=3)))

    assert _to_db_naive_utc(scheduled_at) == datetime(2026, 6, 18, 5, 30)
    assert _to_db_naive_utc(None) is None


@pytest.mark.asyncio
async def test_quota_planner_repository_normalizes_aware_datetimes_at_session_boundary(db_setup) -> None:
    del db_setup
    tz_plus_3 = timezone(timedelta(hours=3))
    scheduled_at = datetime(2026, 6, 18, 8, 30, tzinfo=tz_plus_3)
    executed_at = datetime(2026, 6, 18, 9, 0, tzinfo=tz_plus_3)
    observed_at = datetime(2026, 6, 18, 9, 15, tzinfo=tz_plus_3)
    aware_since = datetime(2026, 6, 18, 8, 30, tzinfo=tz_plus_3)

    async with SessionLocal() as session:
        session.add(
            Account(
                id="quota-planner-aware-account",
                email="quota-planner-aware@example.com",
                plan_type="plus",
                access_token_encrypted=b"access",
                refresh_token_encrypted=b"refresh",
                id_token_encrypted=b"id",
                last_refresh=datetime(2026, 6, 18, 5, 0),
                status=AccountStatus.ACTIVE,
            )
        )
        await session.commit()

        repo = QuotaPlannerRepository(session)
        decision = await repo.log_decision(
            mode="shadow",
            action="warmup",
            idempotency_key="quota-planner-aware-boundary",
            account_id="quota-planner-aware-account",
            scheduled_at=scheduled_at,
            status="planned",
        )

        assert decision.scheduled_at == datetime(2026, 6, 18, 5, 30)

        updated = await repo.update_decision_status(
            decision.id,
            status="executed",
            executed_at=executed_at,
            expected_status="planned",
        )

        assert updated is not None
        assert updated.executed_at == datetime(2026, 6, 18, 6, 0)

        observation = await repo.add_window_observation(
            account_id="quota-planner-aware-account",
            source="warmup_probe",
            observed_at=observed_at,
            model="gpt-5.4-mini",
            confidence="observed",
        )

        assert observation.observed_at == datetime(2026, 6, 18, 6, 15)

        session.add(
            RequestLog(
                request_id="quota-planner-aware-warmup",
                request_kind="warmup",
                requested_at=datetime(2026, 6, 18, 6, 30),
                model="gpt-5.4-mini",
                status="ok",
                input_tokens=12,
                cached_input_tokens=3,
                output_tokens=4,
                cost_usd=0.25,
            )
        )
        await session.commit()

        assert await repo.count_executed_warmups_since(aware_since) == 1
        assert await repo.warmup_cost_since(aware_since) == pytest.approx(0.25)

        bins = await repo.aggregate_demand_bins(since=aware_since)

        assert len(bins) == 1
        assert bins[0].request_count == 1
        assert bins[0].cost_usd == pytest.approx(0.25)


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
        AccountState(
            "a",
            AccountStatus.ACTIVE,
            used_percent=40.0,
            primary_reset_at=int(reset_at),
            primary_window_minutes=300,
        ),
        AccountState(
            "b",
            AccountStatus.ACTIVE,
            used_percent=40.0,
            primary_reset_at=int(reset_at),
            primary_window_minutes=300,
        ),
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
        AccountState("cold-a", AccountStatus.ACTIVE, used_percent=0.0, primary_window_minutes=300),
        AccountState(
            "cold-b",
            AccountStatus.ACTIVE,
            used_percent=0.0,
            primary_reset_at=int(now.timestamp() - 1),
            primary_window_minutes=300,
        ),
        AccountState(
            "active",
            AccountStatus.ACTIVE,
            used_percent=10.0,
            primary_reset_at=int(now.timestamp() + 60),
            primary_window_minutes=300,
        ),
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
        AccountState("cold-a", AccountStatus.ACTIVE, used_percent=0.0, primary_window_minutes=300),
        AccountState("cold-b", AccountStatus.ACTIVE, used_percent=0.0, primary_window_minutes=300),
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
    states = [AccountState("cold", AccountStatus.ACTIVE, used_percent=0.0, primary_window_minutes=300)]

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
        AccountState("cold", AccountStatus.ACTIVE, used_percent=0.0, primary_window_minutes=300),
        AccountState(
            "active",
            AccountStatus.ACTIVE,
            used_percent=40.0,
            primary_reset_at=int(now.timestamp() + 3600),
            primary_window_minutes=300,
        ),
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

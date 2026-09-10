"""Per-(account, model) output-throughput cohort weight and its combination with the TTFT weight."""

from __future__ import annotations

import logging
import random
from collections import Counter
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.balancer.logic import AccountState
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, StickySessionKind
from app.modules.proxy._load_balancer.latency_cohort import (
    apply_latency_cohort_weights,
    combine_cohort_multipliers,
    last_cohort_weight,
)
from app.modules.proxy._load_balancer.opportunistic_admission import detached_runtime_snapshot
from app.modules.proxy._load_balancer.throughput_cohort import (
    TPS_MAX_SAMPLES,
    TPS_MIN_SAMPLES,
    TPS_SAMPLE_MIN_OUTPUT_TOKENS,
    TPS_SAMPLE_WINDOW_SECONDS,
    TPS_WEIGHT_DEADBAND,
    TPS_WEIGHT_FLOOR,
    account_tps_estimate,
    fleet_tps_estimates,
    record_tps_sample,
    tps_weight_multiplier,
)
from app.modules.proxy._load_balancer.ttft_cohort import TTFT_MIN_ACCOUNTS, TTFT_WEIGHT_FLOOR
from app.modules.proxy._load_balancer.types import RuntimeState
from app.modules.proxy.load_balancer import LoadBalancer, _build_states
from tests.simulation.virtual_time import VirtualClock
from tests.unit.test_load_balancer_concurrency import (
    _repo_factory,
    _StubAccountsRepository,
    _StubStickySessionsRepository,
    _StubUsageRepository,
)
from tests.unit.test_request_log_virtual_time import _RecordingVirtualScheduler, _RequestLogsRepo, _service

pytestmark = pytest.mark.unit

_NOW = 2_000_000_000.0
SOL = "gpt-5.6-sol"
ASTRA = "gpt-6-astra"


def _make_account(account_id: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        chatgpt_account_id=f"workspace-{account_id}",
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=datetime.now(tz=timezone.utc),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


def _state(account_id: str, *, multiplier: float = 1.0) -> AccountState:
    return AccountState(
        account_id=account_id,
        status=AccountStatus.ACTIVE,
        used_percent=0.0,
        secondary_used_percent=0.0,
        plan_type="plus",
        selection_weight_multiplier=multiplier,
    )


def _runtime(tps_by_model: dict[str, list[float]], *, ttft_ms: list[int] | None = None) -> RuntimeState:
    return RuntimeState(
        tps_samples={model: [(_NOW, tps) for tps in values] for model, values in tps_by_model.items()},
        ttft_samples=None if ttft_ms is None else [(_NOW, value) for value in ttft_ms],
    )


def _balancer_double(clock: VirtualClock | None = None) -> SimpleNamespace:
    return SimpleNamespace(_runtime={}, _clock=clock or VirtualClock(epoch_value=_NOW))


def _record(balancer: Any, account_id: str | None = "acc", **overrides: Any) -> None:
    kwargs: dict[str, Any] = {
        "status": "success",
        "request_kind": "normal",
        "model": SOL,
        "latency_ms": 12_000,
        "latency_first_token_ms": 2_000,
        "output_tokens": 400,  # 400 tokens over 10 s = 40 tok/s
        "queued_wait_ms": 0,
    }
    kwargs.update(overrides)
    record_tps_sample(balancer, account_id=account_id, **kwargs)


def _tps(balancer: Any, account_id: str = "acc", model: str = SOL) -> list[float]:
    return [tps for _, tps in balancer._runtime[account_id].tps_samples[model]]


def test_record_tps_sample_filters_ineligible_rows() -> None:
    balancer = _balancer_double()
    _record(balancer)
    # Any input size and reasoning effort: the throughput slice is not the TTFT slice.
    _record(balancer, model=ASTRA, latency_ms=7_000, output_tokens=TPS_SAMPLE_MIN_OUTPUT_TOKENS)  # 40 tok/s
    assert _tps(balancer) == [pytest.approx(40.0)]
    assert _tps(balancer, model=ASTRA) == [pytest.approx(40.0)]

    ineligible_rows: tuple[dict[str, Any], ...] = (
        {"status": "error"},
        {"request_kind": "warmup"},
        {"request_kind": "compaction"},
        {"request_kind": "realtime_live"},
        {"model": None},
        {"model": ""},  # direct WebSocket rows without a model carry ""
        {"model": "unknown"},
        {"latency_first_token_ms": None},
        {"latency_first_token_ms": -1},
        {"latency_ms": None},
        {"latency_ms": 2_000},  # no generation span
        {"latency_ms": 1_500},  # first token after the end: corrupt row
        {"output_tokens": None},
        {"output_tokens": TPS_SAMPLE_MIN_OUTPUT_TOKENS - 1},
        {"queued_wait_ms": 5},
        {"retried": True},
    )
    for ineligible in ineligible_rows:
        _record(balancer, **ineligible)
    assert len(_tps(balancer)) == 1 and len(_tps(balancer, model=ASTRA)) == 1
    assert set(balancer._runtime["acc"].tps_samples) == {SOL, ASTRA}

    _record(balancer, account_id=None)
    _record(balancer, account_id="")
    assert set(balancer._runtime) == {"acc"}

    # Balancer doubles without a runtime map or clock are ignored, never raise.
    _record(SimpleNamespace())
    _record(SimpleNamespace(_runtime={}))
    _record(None)


def test_tps_samples_prune_by_age_and_cap_per_model() -> None:
    clock = VirtualClock(epoch_value=_NOW)
    balancer = _balancer_double(clock)
    for _ in range(TPS_MAX_SAMPLES + 6):
        _record(balancer)
    _record(balancer, model=ASTRA)
    assert len(_tps(balancer)) == TPS_MAX_SAMPLES
    assert len(_tps(balancer, model=ASTRA)) == 1

    # Reads ignore aged-out samples; the next write (on any model) drops them
    # and forgets models left without in-window samples.
    clock.advance(TPS_SAMPLE_WINDOW_SECONDS + 1.0)
    runtime = balancer._runtime["acc"]
    assert account_tps_estimate(runtime, SOL, clock.time()) is None
    _record(balancer, model=ASTRA, output_tokens=800)
    assert set(runtime.tps_samples) == {ASTRA}
    assert _tps(balancer, model=ASTRA) == [pytest.approx(80.0)]


def test_estimate_is_a_median_and_neutral_on_thin_evidence() -> None:
    assert account_tps_estimate(None, SOL, _NOW) is None
    assert account_tps_estimate(RuntimeState(), SOL, _NOW) is None
    thin = _runtime({SOL: [40.0] * (TPS_MIN_SAMPLES - 1)})
    assert account_tps_estimate(thin, SOL, _NOW) is None
    assert account_tps_estimate(_runtime({SOL: [40.0] * TPS_MIN_SAMPLES}), ASTRA, _NOW) is None
    assert account_tps_estimate(_runtime({SOL: [40.0] * TPS_MIN_SAMPLES}), None, _NOW) is None
    # A stalled stream (network pause, slow client) and a short burst are both
    # tails; the median reads the serving path's speed through them.
    noisy = _runtime({SOL: [68.0, 70.0, 66.0, 72.0, 69.0, 71.0, 67.0, 70.0, 4.0, 190.0]})
    assert account_tps_estimate(noisy, SOL, _NOW) == pytest.approx(69.5)
    aged = RuntimeState(tps_samples={SOL: [(_NOW - TPS_SAMPLE_WINDOW_SECONDS - 1.0, 40.0)] * 20})
    assert account_tps_estimate(aged, SOL, _NOW) is None


def test_multiplier_has_a_deadband_and_a_floor() -> None:
    assert tps_weight_multiplier(70.0, 70.0) == 1.0
    assert tps_weight_multiplier(70.0 * (1.0 - TPS_WEIGHT_DEADBAND), 70.0) == 1.0
    assert tps_weight_multiplier(40.0, 66.0) == pytest.approx(40.0 / 66.0)
    assert tps_weight_multiplier(5.0, 70.0) == TPS_WEIGHT_FLOOR
    assert tps_weight_multiplier(108.0, 70.0) == 1.0  # faster than the fleet is never boosted
    assert tps_weight_multiplier(40.0, 0.0) == 1.0
    assert tps_weight_multiplier(0.0, 70.0) == 1.0


def test_per_model_isolation_a_slow_sol_account_is_not_discounted_on_astra() -> None:
    runtime = {
        "slow-on-sol": _runtime({SOL: [40.0] * 20, ASTRA: [50.0] * 20}),
        "fast-a": _runtime({SOL: [70.0] * 20, ASTRA: [50.0] * 20}),
        "fast-b": _runtime({SOL: [68.0] * 20, ASTRA: [50.0] * 20}),
    }

    def multiplier_for(model: str | None) -> float:
        state = _state("slow-on-sol")
        apply_latency_cohort_weights([state, _state("fast-a"), _state("fast-b")], runtime, now=_NOW, model=model)
        return state.selection_weight_multiplier

    assert multiplier_for(SOL) == pytest.approx(40.0 / 68.0)
    assert multiplier_for(ASTRA) == 1.0
    assert multiplier_for(None) == 1.0  # builds without a requested model never consult throughput
    assert multiplier_for("gpt-5.4-mini") == 1.0  # a model without evidence is neutral
    assert last_cohort_weight(runtime["slow-on-sol"], SOL) == pytest.approx(40.0 / 68.0)
    assert last_cohort_weight(runtime["slow-on-sol"], ASTRA) == 1.0
    # Only the non-neutral weight is kept: neutral builds (astra, no model, a
    # model without evidence) leave nothing behind under their key.
    assert runtime["slow-on-sol"].tps_weights == {SOL: pytest.approx(40.0 / 68.0)}
    assert all(runtime[account_id].tps_weights is None for account_id in ("fast-a", "fast-b"))


def test_neutral_builds_for_unknown_models_leave_no_weight_behind() -> None:
    runtime = {
        "slow": _runtime({SOL: [40.0] * 20}),
        "fast-a": _runtime({SOL: [70.0] * 20}),
        "fast-b": _runtime({SOL: [68.0] * 20}),
    }
    states = [_state(account_id) for account_id in runtime]
    # The model key is the client's requested string, validated only later:
    # a neutral build for an unknown model must not allocate a key for it.
    for model in ("gpt-5.4-mini", "totally-made-up-model", "unknown", "", None):
        apply_latency_cohort_weights(states, runtime, now=_NOW, model=model)
    assert all(state.selection_weight_multiplier == 1.0 for state in states)
    assert all(entry.tps_weights is None for entry in runtime.values())
    assert all(entry.ttft_weight == 1.0 for entry in runtime.values())

    apply_latency_cohort_weights([_state(account_id) for account_id in runtime], runtime, now=_NOW, model=SOL)
    assert runtime["slow"].tps_weights == {SOL: pytest.approx(40.0 / 68.0)}
    # Lifting back to neutral drops the key and the empty map with it.
    later = _NOW + TPS_SAMPLE_WINDOW_SECONDS + 1.0
    apply_latency_cohort_weights([_state(account_id) for account_id in runtime], runtime, now=later, model=SOL)
    assert runtime["slow"].tps_weights is None
    assert last_cohort_weight(runtime["slow"], SOL) == 1.0


def test_prod_shaped_cohorts() -> None:
    # 2026-09-09, gpt-5.6-sol: nine accounts near 39-40 tok/s, ten near 66-75
    # (one at 108); gpt-6-astra: everybody within 44-56 tok/s.
    slow_sol = [39.0, 39.5, 40.0, 39.2, 39.8, 40.0, 39.6, 39.4, 39.9]
    fast_sol = [66.0, 68.0, 70.0, 72.0, 75.0, 67.0, 69.0, 71.0, 74.0, 108.0]
    astra = [44.0 + index * (12.0 / 18.0) for index in range(19)]
    runtime = {
        **{
            f"slow-{index}": _runtime({SOL: [tps] * 20, ASTRA: [astra[index]] * 20})
            for index, tps in enumerate(slow_sol)
        },
        **{
            f"fast-{index}": _runtime({SOL: [tps] * 20, ASTRA: [astra[9 + index]] * 20})
            for index, tps in enumerate(fast_sol)
        },
    }
    estimates, fleet_sol = fleet_tps_estimates(runtime, SOL, _NOW)
    assert len(estimates) == 19 and fleet_sol == pytest.approx(66.0)

    sol_states = [_state(account_id) for account_id in runtime]
    apply_latency_cohort_weights(sol_states, runtime, now=_NOW, model=SOL)
    by_id = {state.account_id: state.selection_weight_multiplier for state in sol_states}
    assert all(by_id[f"fast-{index}"] == 1.0 for index in range(len(fast_sol)))
    assert all(TPS_WEIGHT_FLOOR < by_id[f"slow-{index}"] < 0.62 for index in range(len(slow_sol)))
    assert by_id["slow-0"] == pytest.approx(39.0 / 66.0)

    astra_states = [_state(account_id) for account_id in runtime]
    apply_latency_cohort_weights(astra_states, runtime, now=_NOW, model=ASTRA)
    assert all(state.selection_weight_multiplier == 1.0 for state in astra_states)


def test_uniform_fleet_is_neutral() -> None:
    runtime = {f"acc-{index}": _runtime({SOL: [70.0 + index * 0.1] * 20}) for index in range(20)}
    states = [_state(account_id) for account_id in runtime]
    apply_latency_cohort_weights(states, runtime, now=_NOW, model=SOL)
    assert all(state.selection_weight_multiplier == 1.0 for state in states)


def test_neutral_below_min_samples_and_min_accounts_for_the_model() -> None:
    assert TTFT_MIN_ACCOUNTS == 3
    runtime = {
        "slow": _runtime({SOL: [40.0] * 20}),
        "fast": _runtime({SOL: [70.0] * 20}),
        # Evidence on another model does not count toward the sol fleet.
        "astra-only": _runtime({ASTRA: [70.0] * 20}),
        # Nor does thin evidence on sol.
        "quiet": _runtime({SOL: [70.0] * (TPS_MIN_SAMPLES - 1)}),
    }
    states = [_state(account_id) for account_id in runtime]
    apply_latency_cohort_weights(states, runtime, now=_NOW, model=SOL)
    assert all(state.selection_weight_multiplier == 1.0 for state in states)
    assert fleet_tps_estimates(runtime, SOL, _NOW) == ({"slow": 40.0, "fast": 70.0}, None)
    assert fleet_tps_estimates(runtime, None, _NOW) == ({}, None)

    # One more account with sol evidence and the fleet exists.
    runtime["third"] = _runtime({SOL: [68.0] * 20})
    states = [_state(account_id) for account_id in runtime]
    apply_latency_cohort_weights(states, runtime, now=_NOW, model=SOL)
    assert {state.account_id: state.selection_weight_multiplier for state in states} == {
        "slow": pytest.approx(40.0 / 68.0),
        "fast": 1.0,
        "astra-only": 1.0,
        "quiet": 1.0,
        "third": 1.0,
    }


def test_combination_takes_the_minimum_and_names_the_signal_never_compounds() -> None:
    assert combine_cohort_multipliers(1.0, 1.0) == (1.0, "none")
    assert combine_cohort_multipliers(0.5, 0.6) == (0.5, "ttft")
    assert combine_cohort_multipliers(0.9, 0.6) == (0.6, "tps")
    assert combine_cohort_multipliers(0.7, 0.7) == (0.7, "both")
    assert combine_cohort_multipliers(1.0, 0.8) == (0.8, "tps")

    runtime = {
        # Slow first token (30 s vs 1.7 s -> TTFT floor) and slow throughput (0.6).
        "slow-both": _runtime({SOL: [40.0] * 20}, ttft_ms=[30_000] * 20),
        # Slow throughput only.
        "slow-tps": _runtime({SOL: [40.0] * 20}, ttft_ms=[1_700] * 20),
        # Slow first token only.
        "slow-ttft": _runtime({SOL: [70.0] * 20}, ttft_ms=[30_000] * 20),
        "fast-a": _runtime({SOL: [66.0] * 20}, ttft_ms=[1_700] * 20),
        "fast-b": _runtime({SOL: [68.0] * 20}, ttft_ms=[1_700] * 20),
        "fast-c": _runtime({SOL: [70.0] * 20}, ttft_ms=[1_700] * 20),
    }
    states = [_state(account_id) for account_id in runtime]
    apply_latency_cohort_weights(states, runtime, now=_NOW, model=SOL)
    by_id = {state.account_id: state.selection_weight_multiplier for state in states}
    assert by_id["slow-both"] == TTFT_WEIGHT_FLOOR  # min(0.5, 40/67), not 0.5 * 40/67
    assert by_id["slow-tps"] == pytest.approx(40.0 / 67.0)
    assert by_id["slow-ttft"] == TTFT_WEIGHT_FLOOR
    assert by_id["fast-a"] == by_id["fast-b"] == by_id["fast-c"] == 1.0

    # The error-rate multiplier still compounds; the two latency signals do not.
    state = _state("slow-both", multiplier=0.75)
    apply_latency_cohort_weights([state], runtime, now=_NOW, model=SOL)
    assert state.selection_weight_multiplier == pytest.approx(0.75 * TTFT_WEIGHT_FLOOR)


@pytest.mark.asyncio
async def test_balancer_steers_fresh_selection_by_the_requested_model() -> None:
    clock = VirtualClock(epoch_value=_NOW)
    fast_a, fast_b, slow = _make_account("acc-fast-a"), _make_account("acc-fast-b"), _make_account("acc-slow-sol")
    balancer = LoadBalancer(
        lambda: _repo_factory(_StubAccountsRepository([fast_a, fast_b, slow]), _StubUsageRepository({}, {})),
        clock=clock,
    )
    for _ in range(20):
        _record(balancer, account_id=fast_a.id, output_tokens=700)  # 70 tok/s on sol
        _record(balancer, account_id=fast_b.id, output_tokens=680)
        _record(balancer, account_id=slow.id, output_tokens=400)  # 40 tok/s on sol
        for account in (fast_a, fast_b, slow):
            _record(balancer, account_id=account.id, model=ASTRA, output_tokens=500)  # uniform on astra

    random.seed(20260909)
    sol_picks: Counter[str] = Counter()
    for _ in range(300):
        result = await balancer.select_account(model=SOL)
        assert result.account is not None, result.error_message
        sol_picks[result.account.id] += 1
    assert sol_picks[slow.id] > 0
    assert sol_picks[slow.id] < min(sol_picks[fast_a.id], sol_picks[fast_b.id])
    assert last_cohort_weight(balancer._runtime[slow.id], SOL) == pytest.approx(40.0 / 68.0)

    # The same account is not discounted for astra, where it is as fast as its siblings.
    result = await balancer.select_account(model=ASTRA)
    assert result.account is not None, result.error_message
    assert last_cohort_weight(balancer._runtime[slow.id], ASTRA) == 1.0

    # The window clears with time and the discount lifts without any new sample.
    clock.advance(TPS_SAMPLE_WINDOW_SECONDS + 1.0)
    result = await balancer.select_account(model=SOL)
    assert result.account is not None
    assert last_cohort_weight(balancer._runtime[slow.id], SOL) == 1.0


def test_build_states_threads_the_model_and_uses_the_full_runtime() -> None:
    runtime = {
        **{f"fast-{index}": _runtime({SOL: [70.0] * 20}) for index in range(4)},
        "slow": _runtime({SOL: [40.0] * 20}),
    }
    common: dict[str, Any] = {
        "accounts": [_make_account("slow")],
        "latest_primary": {},
        "latest_secondary": {},
        "latest_monthly": {},
        "now": _NOW,
    }
    # Selection narrowed to the slow account alone: the fleet reference still comes from the map.
    states, _ = _build_states(runtime=runtime, model=SOL, **common)
    assert [state.selection_weight_multiplier for state in states] == [pytest.approx(40.0 / 70.0)]
    states, _ = _build_states(runtime=runtime, model=ASTRA, **common)
    assert [state.selection_weight_multiplier for state in states] == [1.0]
    # Quota-planner style builds pass no model and stay neutral on throughput.
    states, _ = _build_states(runtime=runtime, **common)
    assert [state.selection_weight_multiplier for state in states] == [1.0]


@pytest.mark.asyncio
async def test_write_request_log_records_tps_only_for_qualifying_rows() -> None:
    scheduler = _RecordingVirtualScheduler(VirtualClock(epoch_value=_NOW))
    service = _service(scheduler, _RequestLogsRepo())
    common: dict[str, Any] = {
        "account_id": "acc-log",
        "api_key": None,
        "model": SOL,
        "latency_ms": 12_000,
        "latency_first_token_ms": 2_000,
        "input_tokens": 60_000,  # large cached input is fine for throughput
        "cached_input_tokens": 55_000,
        "output_tokens": 400,
        "reasoning_tokens": 150,  # not an input: the sample is the total output_tokens over the span
        "reasoning_effort": "high",
    }

    def row(**overrides: Any) -> dict[str, Any]:
        return {**common, **overrides}

    await service._write_request_log(request_id="ok", status="success", **common)
    await service._write_request_log(request_id="failed", status="error", **common)
    await service._write_request_log(request_id="warm", status="success", request_kind="warmup", **common)
    await service._write_request_log(request_id="queued", status="success", latency_bridge_queue_wait_ms=5, **common)
    await service._write_request_log(
        request_id="gated", status="success", latency_response_create_gate_wait_ms=12, **common
    )
    await service._write_request_log(request_id="replayed", status="success", upstream_retried=True, **common)
    await service._write_request_log(
        request_id="short", status="success", **row(output_tokens=TPS_SAMPLE_MIN_OUTPUT_TOKENS - 1)
    )
    await service._write_request_log(request_id="no-model", status="success", **row(model=None))
    await service._write_request_log(request_id="blank-model", status="success", **row(model=""))
    await service._write_request_log(request_id="astra", status="success", **row(model=ASTRA, output_tokens=700))
    await scheduler.drain()
    runtime = service._load_balancer._runtime["acc-log"]
    assert runtime.tps_samples == {SOL: [(_NOW, pytest.approx(40.0))], ASTRA: [(_NOW, pytest.approx(70.0))]}
    # The same rows are outside the TTFT slice (60k input, effort high): the two funnels are independent.
    assert runtime.ttft_samples is None


def test_detached_runtime_snapshot_copies_tps_samples_and_cohort_weights() -> None:
    live = _runtime({SOL: [40.0] * 8})
    live.tps_weights = {SOL: 0.6}
    live.ttft_weight = 0.7
    snapshot = detached_runtime_snapshot({"acc": live}, now=_NOW, stale_lease_ttl_seconds=lambda _kind: 60.0)
    copied = snapshot["acc"]
    assert copied.tps_samples == live.tps_samples and copied.tps_samples is not live.tps_samples
    assert live.tps_samples is not None and copied.tps_samples is not None
    assert copied.tps_samples[SOL] is not live.tps_samples[SOL]
    assert copied.tps_weights == {SOL: 0.6} and copied.tps_weights is not live.tps_weights
    assert copied.ttft_weight == 0.7
    copied.tps_samples[SOL].append((_NOW, 1.0))
    copied.tps_samples[ASTRA] = []
    copied.tps_weights[ASTRA] = 0.5
    copied.ttft_weight = 1.0
    assert len(live.tps_samples[SOL]) == 8 and set(live.tps_samples) == {SOL}
    assert live.tps_weights == {SOL: 0.6} and live.ttft_weight == 0.7
    bare = detached_runtime_snapshot({"acc": RuntimeState()}, now=_NOW, stale_lease_ttl_seconds=lambda _kind: 60.0)
    assert bare["acc"].tps_samples is None and bare["acc"].tps_weights is None and bare["acc"].ttft_weight == 1.0


def test_snapshot_build_writes_the_snapshot_weight_not_the_live_one() -> None:
    live = {
        "fast-a": _runtime({SOL: [70.0] * 20}),
        "fast-b": _runtime({SOL: [70.0] * 20}),
        "slow": _runtime({SOL: [40.0] * 20}),
    }
    accounts = [_make_account(account_id) for account_id in live]
    common: dict[str, Any] = {"latest_primary": {}, "latest_secondary": {}, "latest_monthly": {}, "now": _NOW}
    snapshot = detached_runtime_snapshot(live, now=_NOW, stale_lease_ttl_seconds=lambda _kind: 60.0)
    observed, _ = _build_states(accounts=accounts, runtime=snapshot, model=SOL, log_weight_transitions=False, **common)
    assert last_cohort_weight(live["slow"], SOL) == 1.0
    assert last_cohort_weight(snapshot["slow"], SOL) == pytest.approx(40.0 / 70.0)
    selected, _ = _build_states(accounts=accounts, runtime=live, model=SOL, **common)
    assert [state.selection_weight_multiplier for state in observed] == [
        state.selection_weight_multiplier for state in selected
    ]


def test_transition_log_names_the_signal_and_model_and_is_gated_per_model(caplog: pytest.LogCaptureFixture) -> None:
    runtime = {
        "fast-a": _runtime({SOL: [70.0] * 20, ASTRA: [50.0] * 20}),
        "fast-b": _runtime({SOL: [70.0] * 20, ASTRA: [50.0] * 20}),
        "slow-account-secret": _runtime({SOL: [40.0] * 20, ASTRA: [50.0] * 20}),
    }
    logger_name = "app.modules.proxy._load_balancer.latency_cohort"
    with caplog.at_level(logging.INFO, logger=logger_name):
        # Alternating sol/astra builds: the gate is per model, so the discount
        # is logged once and the astra builds neither log nor reset the gate.
        for model in (SOL, ASTRA, SOL, ASTRA, SOL):
            apply_latency_cohort_weights([_state(account_id) for account_id in runtime], runtime, now=_NOW, model=model)
    transitions = [
        record.getMessage() for record in caplog.records if "latency_cohort_weight_change" in record.getMessage()
    ]
    assert len(transitions) == 1
    message = transitions[0]
    assert "slow-account-secret" not in message and "fast-a" not in message
    assert f"multiplier=0.57 signal=tps model={SOL} changed=tps" in message
    assert "ttft_multiplier=1.00 tps_multiplier=0.57" in message
    assert "tps=40.0 fleet_tps=70.0" in message
    assert "tps_samples=20" in message and "accounts_with_tps_evidence=3" in message

    # Lifting back to neutral on sol is a transition too and is logged once.
    caplog.clear()
    later = _NOW + TPS_SAMPLE_WINDOW_SECONDS + 1.0
    with caplog.at_level(logging.INFO, logger=logger_name):
        for _ in range(2):
            apply_latency_cohort_weights([_state(account_id) for account_id in runtime], runtime, now=later, model=SOL)
    lifted = [record.getMessage() for record in caplog.records if "latency_cohort_weight_change" in record.getMessage()]
    assert len(lifted) == 1 and f"multiplier=1.00 signal=none model={SOL}" in lifted[0]


def test_a_ttft_transition_is_logged_once_across_models(caplog: pytest.LogCaptureFixture) -> None:
    # The first-token signal is model-agnostic, so its gate is per account: one
    # TTFT transition is one line however many models (or none) are requested.
    runtime = {
        "fast-a": _runtime({}, ttft_ms=[1_700] * 20),
        "fast-b": _runtime({}, ttft_ms=[1_700] * 20),
        "slow-account-secret": _runtime({}, ttft_ms=[6_000] * 20),
    }
    logger_name = "app.modules.proxy._load_balancer.latency_cohort"
    with caplog.at_level(logging.INFO, logger=logger_name):
        for model in (SOL, ASTRA, None, "gpt-5.4-mini", SOL):
            apply_latency_cohort_weights([_state(account_id) for account_id in runtime], runtime, now=_NOW, model=model)
    transitions = [
        record.getMessage() for record in caplog.records if "latency_cohort_weight_change" in record.getMessage()
    ]
    assert len(transitions) == 1
    assert f"multiplier=0.50 signal=ttft model={SOL} changed=ttft" in transitions[0]
    assert "slow-account-secret" not in transitions[0]
    assert runtime["slow-account-secret"].ttft_weight == TTFT_WEIGHT_FLOOR
    assert runtime["slow-account-secret"].tps_weights is None  # the TTFT gate never allocates model keys
    assert last_cohort_weight(runtime["slow-account-secret"], ASTRA) == TTFT_WEIGHT_FLOOR

    # A throughput discount arriving on sol is its own transition even though
    # the combined multiplier (still the TTFT floor) does not move.
    caplog.clear()
    for account_id, tps in (("fast-a", 70.0), ("fast-b", 70.0), ("slow-account-secret", 40.0)):
        runtime[account_id].tps_samples = {SOL: [(_NOW, tps)] * 20}
    with caplog.at_level(logging.INFO, logger=logger_name):
        for model in (SOL, ASTRA, SOL):
            apply_latency_cohort_weights([_state(account_id) for account_id in runtime], runtime, now=_NOW, model=model)
    added = [record.getMessage() for record in caplog.records if "latency_cohort_weight_change" in record.getMessage()]
    assert len(added) == 1
    assert f"multiplier=0.50 signal=ttft model={SOL} changed=tps" in added[0]
    assert "ttft_multiplier=0.50 tps_multiplier=0.57" in added[0]


def _fleet_with_sol_cohorts(
    clock: VirtualClock, sticky_repo: _StubStickySessionsRepository | None = None
) -> tuple[LoadBalancer, Account, Account, Account]:
    fast_a, fast_b, slow = _make_account("acc-fast-a"), _make_account("acc-fast-b"), _make_account("acc-slow-sol")
    balancer = LoadBalancer(
        lambda: _repo_factory(
            _StubAccountsRepository([fast_a, fast_b, slow]), _StubUsageRepository({}, {}), sticky_repo
        ),
        clock=clock,
    )
    for _ in range(20):
        _record(balancer, account_id=fast_a.id, output_tokens=700)  # 70 tok/s on sol
        _record(balancer, account_id=fast_b.id, output_tokens=680)
        _record(balancer, account_id=slow.id, output_tokens=400)  # 40 tok/s on sol
        for account in (fast_a, fast_b, slow):
            _record(balancer, account_id=account.id, model=ASTRA, output_tokens=500)  # uniform on astra
    return balancer, fast_a, fast_b, slow


@pytest.mark.asyncio
async def test_sticky_fresh_draw_is_weighted_for_the_requested_model() -> None:
    # A sticky key without an owner takes the sticky selection path
    # (run_sticky_selection_path -> _prepare_sticky_selection_states); the
    # requested model must reach that build too, not only the unbound one.
    sticky_repo = _StubStickySessionsRepository()
    sticky_repo.account_ids_by_key = {}  # per-key ownership: no owner yet for either key below
    balancer, fast_a, fast_b, slow = _fleet_with_sol_cohorts(VirtualClock(epoch_value=_NOW), sticky_repo)
    assert all(entry.tps_weights is None for entry in balancer._runtime.values())

    result = await balancer.select_account(
        sticky_key="fresh-sol-session",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        model=SOL,
    )
    assert result.account is not None, result.error_message
    assert sticky_repo.account_ids_by_key["fresh-sol-session"] == result.account.id
    assert last_cohort_weight(balancer._runtime[slow.id], SOL) == pytest.approx(40.0 / 68.0)
    assert last_cohort_weight(balancer._runtime[fast_a.id], SOL) == 1.0
    assert last_cohort_weight(balancer._runtime[fast_b.id], SOL) == 1.0

    result = await balancer.select_account(
        sticky_key="fresh-astra-session",
        sticky_kind=StickySessionKind.CODEX_SESSION,
        model=ASTRA,
    )
    assert result.account is not None, result.error_message
    assert last_cohort_weight(balancer._runtime[slow.id], ASTRA) == 1.0
    assert balancer._runtime[slow.id].tps_weights == {SOL: pytest.approx(40.0 / 68.0)}

    # The established owner is kept on later turns whatever its weight.
    random.seed(20260909)
    owner = sticky_repo.account_ids_by_key["fresh-sol-session"]
    for _ in range(20):
        result = await balancer.select_account(
            sticky_key="fresh-sol-session", sticky_kind=StickySessionKind.CODEX_SESSION, model=SOL
        )
        assert result.account is not None and result.account.id == owner


@pytest.mark.asyncio
async def test_opportunistic_admission_builds_states_for_the_requested_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.modules.proxy.load_balancer as load_balancer_module

    balancer, fast_a, fast_b, slow = _fleet_with_sol_cohorts(VirtualClock(epoch_value=_NOW))
    original_build_states = load_balancer_module._build_states
    builds: list[tuple[str | None, bool, dict[str, float]]] = []

    def recording_build_states(*args: Any, **kwargs: Any) -> tuple[list[AccountState], dict[str, Account]]:
        states, account_map = original_build_states(*args, **kwargs)
        builds.append(
            (
                kwargs.get("model"),
                kwargs.get("log_weight_transitions", True),
                {state.account_id: state.selection_weight_multiplier for state in states},
            )
        )
        return states, account_map

    monkeypatch.setattr(load_balancer_module, "_build_states", recording_build_states)
    admission: dict[str, Any] = {
        "account_ids": None,
        "prefer_earlier_reset_accounts": False,
        "routing_strategy": "capacity_weighted",
        "budget_threshold_pct": 95.0,
        "lease_kind": None,
    }

    # Observe-only: the build is for the requested model on a detached
    # snapshot; the live runtime is untouched by it.
    observed = await balancer.check_opportunistic_admission(model=SOL, observe_only=True, **admission)
    assert observed.account is not None, observed.error_message
    assert builds == [(SOL, False, {fast_a.id: 1.0, fast_b.id: 1.0, slow.id: pytest.approx(40.0 / 68.0)})]
    assert all(entry.tps_weights is None and entry.ttft_weight == 1.0 for entry in balancer._runtime.values())

    # Live: the same model reaches the live build and its weight is recorded.
    live = await balancer.check_opportunistic_admission(model=SOL, **admission)
    assert live.account is not None, live.error_message
    assert builds[1] == (SOL, True, {fast_a.id: 1.0, fast_b.id: 1.0, slow.id: pytest.approx(40.0 / 68.0)})
    assert last_cohort_weight(balancer._runtime[slow.id], SOL) == pytest.approx(40.0 / 68.0)
    # The admission probe is deterministic: the weight informs the log and the
    # multiplier, never the pick, so the observation matches the live answer.
    assert observed.account.id == live.account.id

    live_astra = await balancer.check_opportunistic_admission(model=ASTRA, **admission)
    assert live_astra.account is not None, live_astra.error_message
    assert builds[2][0] == ASTRA and builds[2][2] == {fast_a.id: 1.0, fast_b.id: 1.0, slow.id: 1.0}
    assert last_cohort_weight(balancer._runtime[slow.id], ASTRA) == 1.0

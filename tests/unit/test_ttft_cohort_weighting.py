from __future__ import annotations

import logging
import random
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from app.core.balancer.logic import AccountState, _select_capacity_weighted, select_account
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, StickySessionKind
from app.modules.proxy._load_balancer import latency_cohort as latency_cohort_module
from app.modules.proxy._load_balancer.latency_cohort import apply_latency_cohort_weights, last_cohort_weight
from app.modules.proxy._load_balancer.opportunistic_admission import (
    _observe_selection_states,
    detached_runtime_snapshot,
)
from app.modules.proxy._load_balancer.ttft_cohort import (
    TTFT_MAX_SAMPLES,
    TTFT_MIN_ACCOUNTS,
    TTFT_MIN_SAMPLES,
    TTFT_SAMPLE_MAX_INPUT_TOKENS,
    TTFT_SAMPLE_WINDOW_SECONDS,
    TTFT_WEIGHT_DEADBAND,
    TTFT_WEIGHT_FLOOR,
    account_ttft_estimate_ms,
    record_ttft_sample,
    ttft_weight_multiplier,
)
from app.modules.proxy._load_balancer.tunables import RoutingTunables
from app.modules.proxy._load_balancer.types import RuntimeState
from app.modules.proxy.load_balancer import LoadBalancer, _build_states
from tests.simulation.virtual_time import VirtualClock
from tests.unit.test_load_balancer_concurrency import (
    _repo_factory,
    _StubAccountsRepository,
    _StubUsageRepository,
)
from tests.unit.test_request_log_virtual_time import _RecordingVirtualScheduler, _RequestLogsRepo, _service

pytestmark = pytest.mark.unit

_NOW = 2_000_000_000.0


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


def _runtime_with_samples(values_ms: list[int], *, now: float = _NOW) -> RuntimeState:
    return RuntimeState(ttft_samples=[(now, value) for value in values_ms])


def _bimodal(count: int, *, fast_ms: int, slow_ms: int, slow_share: float) -> list[int]:
    slow = round(count * slow_share)
    return [slow_ms] * slow + [fast_ms] * (count - slow)


def _balancer_double(clock: VirtualClock | None = None) -> SimpleNamespace:
    return SimpleNamespace(_runtime={}, _clock=clock or VirtualClock(epoch_value=_NOW))


def _record(balancer: Any, account_id: str | None = "acc", **overrides: Any) -> None:
    kwargs: dict[str, Any] = {
        "status": "success",
        "request_kind": "normal",
        "latency_first_token_ms": 1_700,
        "input_tokens": 4_000,
        "reasoning_effort": None,
        "latency_upstream_send_ms": 0,
        "queued_wait_ms": 0,
    }
    kwargs.update(overrides)
    record_ttft_sample(balancer, account_id=account_id, **kwargs)


def test_record_ttft_sample_filters_ineligible_rows() -> None:
    balancer = _balancer_double()
    _record(balancer)
    _record(balancer, reasoning_effort="minimal")
    _record(balancer, reasoning_effort="low")
    # The input cap applies to uncached tokens: a long cached prefix keeps sampling.
    _record(
        balancer, input_tokens=TTFT_SAMPLE_MAX_INPUT_TOKENS + 5_000, cached_input_tokens=TTFT_SAMPLE_MAX_INPUT_TOKENS
    )
    # The sample starts at the upstream send: 3 s of bridge pre-send work is not the account's.
    _record(balancer, latency_first_token_ms=4_700, latency_upstream_send_ms=3_000)
    assert [ttft for _, ttft in balancer._runtime["acc"].ttft_samples] == [1_700] * 5

    ineligible_rows: tuple[dict[str, Any], ...] = (
        {"status": "error"},
        {"request_kind": "warmup"},
        {"request_kind": "compaction"},
        {"request_kind": "realtime_live"},
        {"latency_first_token_ms": None},
        {"latency_first_token_ms": -1},
        {"input_tokens": None},
        {"input_tokens": TTFT_SAMPLE_MAX_INPUT_TOKENS},
        {"input_tokens": TTFT_SAMPLE_MAX_INPUT_TOKENS + 5_000, "cached_input_tokens": 1_000},
        {"reasoning_effort": "medium"},
        {"reasoning_effort": "high"},
        {"queued_wait_ms": 5},
        # A retried send / capacity wait leaves the failed attempt inside the TTFT.
        {"retried": True},
        # No send anchor (fail closed), or a send stamped after the first token.
        {"latency_upstream_send_ms": None},
        {"latency_upstream_send_ms": -1},
        {"latency_upstream_send_ms": 1_701},
    )
    for ineligible in ineligible_rows:
        _record(balancer, **ineligible)
    assert len(balancer._runtime["acc"].ttft_samples) == 5

    _record(balancer, account_id=None)
    _record(balancer, account_id="")
    assert set(balancer._runtime) == {"acc"}

    # Balancer doubles without a runtime map or clock are ignored, never raise.
    _record(SimpleNamespace())
    _record(SimpleNamespace(_runtime={}))
    _record(None)


def test_samples_prune_by_age_and_cap() -> None:
    clock = VirtualClock(epoch_value=_NOW)
    balancer = _balancer_double(clock)
    for _ in range(TTFT_MAX_SAMPLES + 6):
        _record(balancer)
    samples = balancer._runtime["acc"].ttft_samples
    assert len(samples) == TTFT_MAX_SAMPLES

    # Reads ignore aged-out samples; the next write drops them.
    clock.advance(TTFT_SAMPLE_WINDOW_SECONDS + 1.0)
    assert account_ttft_estimate_ms(balancer._runtime["acc"], clock.time()) is None
    _record(balancer, latency_first_token_ms=900)
    assert [ttft for _, ttft in balancer._runtime["acc"].ttft_samples] == [900]


def test_estimate_is_an_upper_trimmed_mean_and_neutral_on_thin_evidence() -> None:
    assert account_ttft_estimate_ms(None, _NOW) is None
    assert account_ttft_estimate_ms(RuntimeState(), _NOW) is None
    thin = _runtime_with_samples([1_700] * (TTFT_MIN_SAMPLES - 1))
    assert account_ttft_estimate_ms(thin, _NOW) is None
    # Eight samples with one 30 s stall: the stall is the top decile and is dropped.
    stalled = _runtime_with_samples([1_700] * (TTFT_MIN_SAMPLES - 1) + [30_000])
    assert account_ttft_estimate_ms(stalled, _NOW) == pytest.approx(1_700.0)
    # A 40% reasoning share survives trimming (unlike a median, which would sit at 2000).
    bimodal = _runtime_with_samples(_bimodal(64, fast_ms=2_000, slow_ms=6_000, slow_share=0.4))
    estimate = account_ttft_estimate_ms(bimodal, _NOW)
    assert estimate is not None and 3_000.0 < estimate < 3_600.0


def test_multiplier_has_a_deadband_and_a_floor() -> None:
    assert ttft_weight_multiplier(1_000.0, 1_000.0) == 1.0
    assert ttft_weight_multiplier(1_000.0 * (1.0 + TTFT_WEIGHT_DEADBAND), 1_000.0) == 1.0
    assert ttft_weight_multiplier(1_250.0, 1_000.0) == pytest.approx(0.8)
    assert ttft_weight_multiplier(30_000.0, 1_000.0) == TTFT_WEIGHT_FLOOR
    assert ttft_weight_multiplier(500.0, 1_000.0) == 1.0  # faster than the fleet is never boosted
    assert ttft_weight_multiplier(1_000.0, 0.0) == 1.0


def test_weights_neutral_on_thin_fleet_evidence() -> None:
    runtime = {
        "slow": _runtime_with_samples([6_000] * 20),
        "fast": _runtime_with_samples([1_700] * 20),
        "quiet": _runtime_with_samples([1_700] * (TTFT_MIN_SAMPLES - 1)),
    }
    assert TTFT_MIN_ACCOUNTS == 3
    states = [_state("slow"), _state("fast"), _state("quiet")]
    apply_latency_cohort_weights(states, runtime, now=_NOW)
    assert [state.selection_weight_multiplier for state in states] == [1.0, 1.0, 1.0]


def test_uniformly_slow_fleet_is_neutral() -> None:
    runtime = {f"acc-{index}": _runtime_with_samples([2_600 + index] * 20) for index in range(20)}
    states = [_state(account_id) for account_id in runtime]
    apply_latency_cohort_weights(states, runtime, now=_NOW)
    assert all(state.selection_weight_multiplier == 1.0 for state in states)


def test_prod_shaped_bimodal_cohorts() -> None:
    runtime = {
        **{
            f"fast-{index}": _runtime_with_samples(_bimodal(64, fast_ms=1_700, slow_ms=6_000, slow_share=0.05))
            for index in range(11)
        },
        **{
            f"slow-{index}": _runtime_with_samples(_bimodal(64, fast_ms=2_000, slow_ms=6_000, slow_share=0.4))
            for index in range(9)
        },
        "outlier": _runtime_with_samples([30_000] * 20),
        "nearby": _runtime_with_samples([1_800] * 20),  # inside the 15% deadband
    }
    states = [_state(account_id) for account_id in runtime]
    apply_latency_cohort_weights(states, runtime, now=_NOW)
    by_id = {state.account_id: state.selection_weight_multiplier for state in states}
    assert all(by_id[f"fast-{index}"] == 1.0 for index in range(11))
    assert all(TTFT_WEIGHT_FLOOR <= by_id[f"slow-{index}"] < 1.0 for index in range(9))
    assert by_id["outlier"] == TTFT_WEIGHT_FLOOR
    assert by_id["nearby"] == 1.0
    assert last_cohort_weight(runtime["slow-0"], None) == by_id["slow-0"]


def test_multiplier_compounds_with_error_rate_and_is_clamped() -> None:
    runtime = {
        "flaky-slow": _runtime_with_samples([2_500] * 20),
        "fast-a": _runtime_with_samples([1_000] * 20),
        "fast-b": _runtime_with_samples([1_000] * 20),
    }
    state = _state("flaky-slow", multiplier=0.75)  # error-rate discount already applied
    apply_latency_cohort_weights([state, _state("fast-a"), _state("fast-b")], runtime, now=_NOW)
    assert state.selection_weight_multiplier == pytest.approx(0.75 * TTFT_WEIGHT_FLOOR)
    random.seed(20260909)
    picks = Counter(_select_capacity_weighted([state, _state("fast-a")]).account_id for _ in range(400))
    assert picks["fast-a"] > picks["flaky-slow"] > 0


@pytest.mark.asyncio
async def test_balancer_steers_fresh_selection_away_from_slow_cohort() -> None:
    clock = VirtualClock(epoch_value=_NOW)
    fast_a, fast_b, slow = _make_account("acc-fast-a"), _make_account("acc-fast-b"), _make_account("acc-slow")
    balancer = LoadBalancer(
        lambda: _repo_factory(_StubAccountsRepository([fast_a, fast_b, slow]), _StubUsageRepository({}, {})),
        clock=clock,
    )
    for _ in range(20):
        _record(balancer, account_id=fast_a.id, latency_first_token_ms=1_700)
        _record(balancer, account_id=fast_b.id, latency_first_token_ms=1_800)
        _record(balancer, account_id=slow.id, latency_first_token_ms=6_000)

    random.seed(20260909)
    picks: Counter[str] = Counter()
    for _ in range(300):
        result = await balancer.select_account()
        assert result.account is not None, result.error_message
        picks[result.account.id] += 1
    # 0.5 vs 1.0 weight with equal credits: about a fifth of the draws, never zero.
    assert picks[slow.id] > 0
    assert picks[slow.id] < min(picks[fast_a.id], picks[fast_b.id])
    assert last_cohort_weight(balancer._runtime[slow.id], None) == TTFT_WEIGHT_FLOOR

    # The window clears with time and the discount lifts without any new sample.
    clock.advance(TTFT_SAMPLE_WINDOW_SECONDS + 1.0)
    result = await balancer.select_account()
    assert result.account is not None
    assert last_cohort_weight(balancer._runtime[slow.id], None) == 1.0


@asynccontextmanager
async def _mock_repo_factory():
    yield AsyncMock()


def _sticky_repo(existing_account_id: str | None) -> AsyncMock:
    repo = AsyncMock()
    repo.get_account_id = AsyncMock(return_value=existing_account_id)
    repo.upsert = AsyncMock()
    repo.delete = AsyncMock()
    return repo


@pytest.mark.asyncio
async def test_established_owner_on_slow_account_is_kept_and_deterministic_strategies_ignore_the_weight() -> None:
    clock = VirtualClock(epoch_value=_NOW)
    balancer = LoadBalancer(_mock_repo_factory, clock=clock)
    account_map = {account_id: cast(Account, AsyncMock()) for account_id in ("slow", "fast")}
    for _ in range(40):
        states = [_state("slow", multiplier=TTFT_WEIGHT_FLOOR), _state("fast")]
        outcome = await balancer._select_with_stickiness(
            states=states,
            account_map=account_map,
            sticky_key="owned-key",
            sticky_kind=StickySessionKind.PROMPT_CACHE,
            reallocate_sticky=False,
            sticky_max_age_seconds=600,
            prefer_earlier_reset_accounts=False,
            prefer_earlier_reset_window="secondary",
            routing_strategy="capacity_weighted",
            sticky_repo=_sticky_repo("slow"),
        )
        assert outcome.selection.account is not None
        assert outcome.selection.account.account_id == "slow"

    for strategy in (
        "round_robin",
        "sequential_drain",
        "fill_first",
        "usage_weighted",
        "reset_drain",
        "single_account",
    ):
        weighted = select_account(
            [_state("slow", multiplier=TTFT_WEIGHT_FLOOR), _state("fast")], _NOW, routing_strategy=strategy
        )
        neutral = select_account([_state("slow"), _state("fast")], _NOW, routing_strategy=strategy)
        assert weighted.account is not None and neutral.account is not None
        assert weighted.account.account_id == neutral.account.account_id


def test_build_states_uses_the_full_runtime_for_the_fleet_reference() -> None:
    runtime = {
        **{f"fast-{index}": _runtime_with_samples([1_700] * 20) for index in range(4)},
        "slow": _runtime_with_samples([6_000] * 20),
    }
    # Selection narrowed to the slow account alone (API-key scoping, model
    # catalog, continuity owner): the fleet reference still comes from the map.
    states, _ = _build_states(
        accounts=[_make_account("slow")],
        latest_primary={},
        latest_secondary={},
        latest_monthly={},
        runtime=runtime,
        now=_NOW,
    )
    assert [state.selection_weight_multiplier for state in states] == [TTFT_WEIGHT_FLOOR]

    # Quota-planner style builds pass an empty runtime and stay neutral.
    states, _ = _build_states(
        accounts=[_make_account("slow")],
        latest_primary={},
        latest_secondary={},
        latest_monthly={},
        runtime={},
        now=_NOW,
    )
    assert [state.selection_weight_multiplier for state in states] == [1.0]


@pytest.mark.asyncio
async def test_write_request_log_records_only_eligible_rows() -> None:
    scheduler = _RecordingVirtualScheduler(VirtualClock(epoch_value=_NOW))
    request_logs = _RequestLogsRepo()
    service = _service(scheduler, request_logs)
    common: dict[str, Any] = {
        "account_id": "acc-log",
        "api_key": None,
        "model": "gpt-5.5",
        "latency_ms": 2_000,
        "latency_first_token_ms": 1_700,
        "input_tokens": 3_000,
        "reasoning_effort": "low",
        "latency_upstream_send_ms": 0,
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
    # WebSocket/bridge rows retried after a capacity wait or an owner-pinned
    # quota error carry the failed attempt in their TTFT and are skipped.
    await service._write_request_log(request_id="retried", status="success", upstream_retried=True, **common)
    # A large cached prefix keeps a small turn eligible.
    cached: dict[str, Any] = {
        **common,
        "input_tokens": TTFT_SAMPLE_MAX_INPUT_TOKENS + 5_000,
        "cached_input_tokens": TTFT_SAMPLE_MAX_INPUT_TOKENS,
        "latency_first_token_ms": 1_650,
    }
    await service._write_request_log(request_id="cached", status="success", **cached)
    # Bridge rows: the sample runs from the ``response.create`` send, so the
    # pre-send local work inside the row's TTFT is dropped; a row without a send
    # anchor is not sampled at all.
    await service._write_request_log(
        request_id="bridge", status="success", **row(latency_first_token_ms=4_700, latency_upstream_send_ms=3_000)
    )
    await service._write_request_log(request_id="unanchored", status="success", **row(latency_upstream_send_ms=None))
    await scheduler.drain()
    runtime = service._load_balancer._runtime["acc-log"]
    assert runtime.ttft_samples == [(_NOW, 1_700), (_NOW, 1_650), (_NOW, 1_700)]
    assert [persisted["latency_first_token_ms"] for persisted in request_logs.rows[-2:]] == [4_700, 1_700]


def test_detached_runtime_snapshot_copies_ttft_samples() -> None:
    live = _runtime_with_samples([1_700] * 8)
    snapshot = detached_runtime_snapshot({"acc": live}, now=_NOW, stale_lease_ttl_seconds=lambda _kind: 60.0)
    copied = snapshot["acc"].ttft_samples
    assert copied is not None and live.ttft_samples is not None
    assert copied == live.ttft_samples and copied is not live.ttft_samples
    copied.append((_NOW, 30_000))
    assert len(live.ttft_samples) == 8
    assert (
        detached_runtime_snapshot({"acc": RuntimeState()}, now=_NOW, stale_lease_ttl_seconds=lambda _kind: 60.0)[
            "acc"
        ].ttft_samples
        is None
    )


def test_transition_log_is_gated_and_carries_no_account_identifiers(caplog: pytest.LogCaptureFixture) -> None:
    runtime = {
        "fast-a": _runtime_with_samples([1_700] * 20),
        "fast-b": _runtime_with_samples([1_700] * 20),
        "slow-account-secret": _runtime_with_samples([6_000] * 20),
    }
    states = [_state(account_id) for account_id in runtime]
    with caplog.at_level(logging.INFO, logger="app.modules.proxy._load_balancer.latency_cohort"):
        apply_latency_cohort_weights(states, runtime, now=_NOW)
        apply_latency_cohort_weights([_state(account_id) for account_id in runtime], runtime, now=_NOW)
    transitions = [record for record in caplog.records if "latency_cohort_weight_change" in record.getMessage()]
    assert len(transitions) == 1
    message = transitions[0].getMessage()
    assert "slow-account-secret" not in message and "fast-a" not in message
    assert "multiplier=0.50 signal=ttft model=None" in message and "accounts_with_ttft_evidence=3" in message

    # Lifting back to neutral is a transition too and is logged once.
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="app.modules.proxy._load_balancer.latency_cohort"):
        apply_latency_cohort_weights(
            [_state(account_id) for account_id in runtime], runtime, now=_NOW + TTFT_SAMPLE_WINDOW_SECONDS + 1.0
        )
        apply_latency_cohort_weights(
            [_state(account_id) for account_id in runtime], runtime, now=_NOW + TTFT_SAMPLE_WINDOW_SECONDS + 1.0
        )
    lifted = [record for record in caplog.records if "latency_cohort_weight_change" in record.getMessage()]
    assert len(lifted) == 1 and "multiplier=1.00" in lifted[0].getMessage()


def test_transition_gate_logs_a_move_of_exactly_the_configured_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    # 0.125 is exactly representable, so the boundary is a real equality, not a rounding artefact.
    monkeypatch.setattr(latency_cohort_module, "_LOG_DELTA", 0.125)
    assert latency_cohort_module._transition(0.75, 0.625) is True
    assert latency_cohort_module._transition(0.75, 0.875) is True
    assert latency_cohort_module._transition(0.75, 0.7) is False
    # Crossing 1.0 is always a transition, however small the move.
    assert latency_cohort_module._transition(1.0, 0.99) is True


def test_snapshot_build_does_not_repeat_the_transition_log(caplog: pytest.LogCaptureFixture) -> None:
    live = {
        "fast-a": _runtime_with_samples([1_700] * 20),
        "fast-b": _runtime_with_samples([1_700] * 20),
        "slow": _runtime_with_samples([6_000] * 20),
    }
    accounts = [_make_account(account_id) for account_id in live]
    common: dict[str, Any] = {"latest_primary": {}, "latest_secondary": {}, "latest_monthly": {}, "now": _NOW}
    with caplog.at_level(logging.INFO, logger="app.modules.proxy._load_balancer.latency_cohort"):
        snapshot = detached_runtime_snapshot(live, now=_NOW, stale_lease_ttl_seconds=lambda _kind: 60.0)
        observed, _ = _build_states(accounts=accounts, runtime=snapshot, log_weight_transitions=False, **common)
        assert last_cohort_weight(live["slow"], None) == 1.0  # the snapshot absorbed the write
        selected, _ = _build_states(accounts=accounts, runtime=live, **common)
    assert [state.selection_weight_multiplier for state in observed] == [
        state.selection_weight_multiplier for state in selected
    ]
    assert last_cohort_weight(live["slow"], None) == TTFT_WEIGHT_FLOOR
    transitions = [record for record in caplog.records if "latency_cohort_weight_change" in record.getMessage()]
    assert len(transitions) == 1


def test_observe_only_admission_builds_without_transition_logging() -> None:
    live = {"acc": _runtime_with_samples([1_700] * 8)}
    seen: dict[str, Any] = {}

    def build_states(**kwargs: Any) -> tuple[list[AccountState], dict[str, Account]]:
        seen.update(kwargs)
        return [], {}

    owner = SimpleNamespace(
        _clock=VirtualClock(epoch_value=_NOW),
        _encryptor=None,
        _detached_runtime_snapshot=lambda *, routing_tunables: detached_runtime_snapshot(
            live, now=_NOW, stale_lease_ttl_seconds=lambda _kind: 60.0
        ),
    )
    selection_inputs = SimpleNamespace(
        accounts=[],
        latest_primary={},
        latest_secondary={},
        latest_monthly={},
        routing_policy_override=None,
        ignore_standard_quota_account_ids=frozenset(),
    )
    _observe_selection_states(
        cast(Any, owner),
        cast(Any, selection_inputs),
        build_states=cast(Any, build_states),
        routing_tunables=RoutingTunables(),
    )
    assert seen["log_weight_transitions"] is False
    assert seen["runtime"]["acc"] is not live["acc"]

from __future__ import annotations

import random
from collections import Counter
from datetime import datetime, timezone

import pytest

from app.core.balancer.logic import AccountState, _select_capacity_weighted, _select_relative_availability
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus
from app.modules.proxy._load_balancer.error_rate import (
    ERROR_RATE_MIN_SAMPLES,
    ERROR_RATE_WEIGHT_FLOOR,
    ERROR_RATE_WINDOW_SECONDS,
    ErrorRateWeightingPolicy,
    error_rate_weight_multiplier,
    recent_outcomes,
    record_outcome_locked,
)
from app.modules.proxy._load_balancer.types import RuntimeState
from app.modules.proxy.load_balancer import LoadBalancer, effective_routing_tunables
from tests.simulation.virtual_time import VirtualClock
from tests.unit.test_load_balancer_concurrency import (
    _repo_factory,
    _StubAccountsRepository,
    _StubUsageRepository,
)

pytestmark = pytest.mark.unit

_POLICY = ErrorRateWeightingPolicy(enabled=True)


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


def test_window_prunes_old_minute_buckets_and_counts_inside_the_window() -> None:
    runtime = RuntimeState()
    now = 10_000.0
    record_outcome_locked(runtime, now, success=True, count=3, window_seconds=600.0)
    record_outcome_locked(runtime, now, success=False, window_seconds=600.0)
    assert recent_outcomes(runtime, now, window_seconds=600.0) == (3, 1)
    # Same window, later minute: both buckets count.
    record_outcome_locked(runtime, now + 300.0, success=False, count=2, window_seconds=600.0)
    assert recent_outcomes(runtime, now + 300.0, window_seconds=600.0) == (3, 3)
    # Once the first bucket ages out of the window it is dropped on the next write
    # and ignored by reads in between.
    assert recent_outcomes(runtime, now + 700.0, window_seconds=600.0) == (0, 2)
    record_outcome_locked(runtime, now + 700.0, success=True, window_seconds=600.0)
    assert runtime.outcome_buckets is not None and len(runtime.outcome_buckets) == 2
    assert recent_outcomes(runtime, now + 700.0, window_seconds=600.0) == (1, 2)
    record_outcome_locked(runtime, now, success=True, count=0, window_seconds=600.0)  # no-op


def test_multiplier_is_neutral_on_thin_evidence_and_discounts_by_error_rate() -> None:
    now = 5_000.0
    assert error_rate_weight_multiplier(None, now, policy=_POLICY) == 1.0
    runtime = RuntimeState()
    for _ in range(ERROR_RATE_MIN_SAMPLES - 1):
        record_outcome_locked(runtime, now, success=False, window_seconds=ERROR_RATE_WINDOW_SECONDS)
    assert error_rate_weight_multiplier(runtime, now, policy=_POLICY) == 1.0
    record_outcome_locked(runtime, now, success=False, window_seconds=ERROR_RATE_WINDOW_SECONDS)
    assert error_rate_weight_multiplier(runtime, now, policy=_POLICY) == pytest.approx(ERROR_RATE_WEIGHT_FLOOR)

    mixed = RuntimeState()
    record_outcome_locked(mixed, now, success=True, count=15, window_seconds=ERROR_RATE_WINDOW_SECONDS)
    record_outcome_locked(mixed, now, success=False, count=5, window_seconds=ERROR_RATE_WINDOW_SECONDS)
    assert error_rate_weight_multiplier(mixed, now, policy=_POLICY) == pytest.approx(0.75)
    # The window clears: full weight is restored without any success needed.
    assert error_rate_weight_multiplier(mixed, now + ERROR_RATE_WINDOW_SECONDS + 60.0, policy=_POLICY) == 1.0
    # Disabled knob: always neutral.
    assert error_rate_weight_multiplier(runtime, now, policy=ErrorRateWeightingPolicy(enabled=False)) == 1.0


def test_capacity_weighted_draw_honors_the_multiplier() -> None:
    random.seed(20260908)
    states = [_state("penalized", multiplier=ERROR_RATE_WEIGHT_FLOOR), _state("healthy")]
    picks = Counter(_select_capacity_weighted(states).account_id for _ in range(400))
    # Equal remaining credits: 5% vs 100% weight, so ~5% of draws.
    assert picks["healthy"] > 340
    assert 0 < picks["penalized"] < 60

    # Neutral multipliers keep the pre-existing even split.
    random.seed(20260908)
    even = Counter(_select_capacity_weighted([_state("a"), _state("b")]).account_id for _ in range(400))
    assert 150 < even["a"] < 250


def test_relative_availability_draw_honors_the_multiplier_without_changing_top_k() -> None:
    random.seed(20260908)
    states = [_state("penalized", multiplier=ERROR_RATE_WEIGHT_FLOOR), _state("healthy")]
    picks = Counter(
        _select_relative_availability(states, current=1_000.0, power=2.0, top_k=5, deterministic_probe=False).account_id
        for _ in range(400)
    )
    assert picks["healthy"] > 340
    assert 0 < picks["penalized"] < 60
    # Deterministic probe picks by availability alone (top of the ranked list).
    probe = _select_relative_availability(
        [_state("penalized", multiplier=ERROR_RATE_WEIGHT_FLOOR), _state("healthy")],
        current=1_000.0,
        power=2.0,
        top_k=5,
        deterministic_probe=True,
    )
    baseline = _select_relative_availability(
        [_state("penalized"), _state("healthy")],
        current=1_000.0,
        power=2.0,
        top_k=5,
        deterministic_probe=True,
    )
    assert probe.account_id == baseline.account_id


def test_bad_multipliers_are_clamped() -> None:
    random.seed(1)
    picks = Counter(
        _select_capacity_weighted([_state("neg", multiplier=-4.0), _state("big", multiplier=50.0)]).account_id
        for _ in range(200)
    )
    # Negative -> neutral, >1 -> 1.0: an even split, never a ValueError or a monopoly.
    assert 60 < picks["neg"] < 140
    nan_state = _state("nan", multiplier=float("nan"))
    assert _select_capacity_weighted([nan_state]).account_id == "nan"


@pytest.mark.asyncio
async def test_balancer_records_outcomes_and_steers_fresh_selection_away_from_a_flaky_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODEX_LB_PROXY_ACCOUNT_ERROR_RATE_WEIGHTING_ENABLED", "true")
    get_settings.cache_clear()
    try:
        clock = VirtualClock(epoch_value=2_000_000_000.0)
        flaky = _make_account("acc-flaky")
        steady = _make_account("acc-steady")
        balancer = LoadBalancer(
            lambda: _repo_factory(_StubAccountsRepository([flaky, steady]), _StubUsageRepository({}, {})),
            clock=clock,
        )
        # Interleave successes so ``error_count`` is reset every time and the
        # generic error backoff / drain tier never engage: only the window sees it.
        for _ in range(12):
            await balancer.record_error(flaky)
            await balancer.record_success(flaky)
            await balancer.record_success(steady)
        assert balancer._runtime[flaky.id].error_count == 0
        assert recent_outcomes(balancer._runtime[flaky.id], clock.time(), window_seconds=600.0) == (12, 12)

        random.seed(20260908)
        picks: Counter[str] = Counter()
        for _ in range(120):
            result = await balancer.select_account()
            assert result.account is not None, result.error_message
            picks[result.account.id] += 1
        # 0.5 vs 1.0 weight => about one third of the draws.
        assert picks[steady.id] > picks[flaky.id]
        assert picks[flaky.id] > 0

        # The window clears with time (minute-bucket granularity adds up to 60 s);
        # the discount lifts without any success needed.
        clock.advance(661.0)
        assert error_rate_weight_multiplier(balancer._runtime[flaky.id], clock.time(), policy=_POLICY) == 1.0
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_weighting_disabled_keeps_selection_neutral(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_LB_PROXY_ACCOUNT_ERROR_RATE_WEIGHTING_ENABLED", "false")
    get_settings.cache_clear()
    try:
        clock = VirtualClock(epoch_value=2_000_000_000.0)
        flaky = _make_account("acc-flaky-off")
        balancer = LoadBalancer(
            lambda: _repo_factory(_StubAccountsRepository([flaky]), _StubUsageRepository({}, {})),
            clock=clock,
        )
        for _ in range(20):
            await balancer.record_error(flaky)
        disabled = ErrorRateWeightingPolicy(enabled=effective_routing_tunables().error_rate_weighting_enabled)
        assert disabled.enabled is False
        assert error_rate_weight_multiplier(balancer._runtime[flaky.id], clock.time(), policy=disabled) == 1.0
    finally:
        get_settings.cache_clear()

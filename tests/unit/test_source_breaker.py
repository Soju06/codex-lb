"""Per-source overflow breaker and admission claims (#2123 WP-C2, design v3 §8.3, §8.4, CL-2).

Virtual time: every ``now`` is a number the test chooses and the breaker's own
clock is a ``VirtualClock`` the test advances, so the open window and the
trial lease are exercised deterministically. Each mutant of design §13.5 that
concerns the breaker is named in a test below: never opens (no token when
closed), counts 401/404, no lease, trial claimed without release on a decline,
never re-opens, pinned ignores the open breaker (the claim helper's answer).
"""

from __future__ import annotations

import logging

import pytest

from app.db.models import ModelSource
from app.modules.proxy import overflow as overflow_module
from app.modules.proxy.overflow import (
    BREAKER_FAILURE_THRESHOLD,
    BREAKER_OPEN_SECONDS,
    TRIAL_LEASE_TTL_SECONDS,
    BreakerToken,
    ClaimDenied,
    SourceBreaker,
    get_source_breaker,
    try_claim_overflow,
)
from app.modules.proxy.source_admission import SourceAdmission, SourceBulkhead
from tests.simulation.virtual_time import VirtualClock

pytestmark = pytest.mark.unit

SRC = "src_overflow"


def _source(source_id: str = SRC, max_concurrency: int | None = None) -> ModelSource:
    return ModelSource(
        id=source_id,
        name=source_id,
        kind="openai_compatible",
        base_url="http://127.0.0.1:9/v1",
        is_enabled=True,
        supports_chat_completions=False,
        supports_responses=True,
        max_concurrency=max_concurrency,
    )


class _Gauge:
    def __init__(self) -> None:
        self.values: dict[str, list[int]] = {}
        self._current: str | None = None

    def labels(self, **labels: str) -> _Gauge:
        self._current = labels["source_id"]
        return self

    def set(self, value: float) -> None:
        assert self._current is not None
        self.values.setdefault(self._current, []).append(int(value))


@pytest.fixture
def clock() -> VirtualClock:
    return VirtualClock()


@pytest.fixture
def gauge(monkeypatch: pytest.MonkeyPatch) -> _Gauge:
    recorder = _Gauge()
    monkeypatch.setattr(overflow_module, "model_source_breaker_state", recorder)
    return recorder


def _fail(breaker: SourceBreaker, clock: VirtualClock, times: int) -> None:
    for _ in range(times):
        token = breaker.claim(SRC, clock.monotonic())
        assert token is not None
        token.settle("failure")


def _open(breaker: SourceBreaker, clock: VirtualClock) -> None:
    _fail(breaker, clock, BREAKER_FAILURE_THRESHOLD)
    assert breaker.state(SRC, clock.monotonic()) == "open"


# --- closed state ----------------------------------------------------------------------


def test_closed_breaker_issues_a_token_and_opens_after_three_counted_failures(clock: VirtualClock) -> None:
    """Mutant: no token in the closed state => the breaker can never open."""

    breaker = SourceBreaker(clock=clock)
    assert breaker.state(SRC, clock.monotonic()) == "closed"
    token = breaker.claim(SRC, clock.monotonic())
    assert isinstance(token, BreakerToken)
    assert token.kind == "closed"

    _fail(breaker, clock, BREAKER_FAILURE_THRESHOLD - 1)
    assert breaker.state(SRC, clock.monotonic()) == "closed"
    assert breaker.failures(SRC) == BREAKER_FAILURE_THRESHOLD - 1
    _fail(breaker, clock, 1)
    assert breaker.state(SRC, clock.monotonic()) == "open"
    assert breaker.is_open(SRC, clock.monotonic())
    assert breaker.claim(SRC, clock.monotonic()) is None


def test_failures_are_consecutive_a_success_resets_the_count(clock: VirtualClock) -> None:
    breaker = SourceBreaker(clock=clock)
    _fail(breaker, clock, BREAKER_FAILURE_THRESHOLD - 1)
    success = breaker.claim(SRC, clock.monotonic())
    assert success is not None
    success.settle("success")
    assert breaker.failures(SRC) == 0
    _fail(breaker, clock, BREAKER_FAILURE_THRESHOLD - 1)
    assert breaker.state(SRC, clock.monotonic()) == "closed"


def test_inconclusive_outcomes_are_not_counted(clock: VirtualClock) -> None:
    """401/404, a pin failure, a client cancel before the first item and a decline settle ``inconclusive``."""

    breaker = SourceBreaker(clock=clock)
    for _ in range(10):
        token = breaker.claim(SRC, clock.monotonic())
        assert token is not None
        token.settle("inconclusive")
    assert breaker.failures(SRC) == 0
    assert breaker.state(SRC, clock.monotonic()) == "closed"


def test_a_token_settles_exactly_once(clock: VirtualClock) -> None:
    breaker = SourceBreaker(clock=clock)
    token = breaker.claim(SRC, clock.monotonic())
    assert token is not None
    token.settle("failure")
    token.settle("failure")
    token.settle("failure")
    assert token.settled is True
    assert breaker.failures(SRC) == 1
    assert breaker.state(SRC, clock.monotonic()) == "closed"


def test_a_closed_token_settled_after_the_trip_is_ignored(clock: VirtualClock) -> None:
    breaker = SourceBreaker(clock=clock)
    stale = breaker.claim(SRC, clock.monotonic())
    assert stale is not None
    _open(breaker, clock)
    # A success that predates the trip must not close an open breaker.
    stale.settle("success")
    assert breaker.state(SRC, clock.monotonic()) == "open"
    assert breaker.failures(SRC) == BREAKER_FAILURE_THRESHOLD


def test_sources_are_independent(clock: VirtualClock) -> None:
    breaker = SourceBreaker(clock=clock)
    _open(breaker, clock)
    other = breaker.claim("src_other", clock.monotonic())
    assert other is not None
    assert breaker.state("src_other", clock.monotonic()) == "closed"


# --- open window and half-open trial ------------------------------------------------


def test_open_window_lasts_thirty_seconds_then_one_leased_trial(clock: VirtualClock) -> None:
    breaker = SourceBreaker(clock=clock)
    _open(breaker, clock)
    clock.advance(BREAKER_OPEN_SECONDS - 0.001)
    assert breaker.state(SRC, clock.monotonic()) == "open"
    assert breaker.claim(SRC, clock.monotonic()) is None
    clock.advance(0.002)
    assert breaker.state(SRC, clock.monotonic()) == "half_open"
    assert breaker.is_open(SRC, clock.monotonic()) is False  # advisory read: half-open lets the claim decide

    trial = breaker.claim(SRC, clock.monotonic())
    assert trial is not None
    assert trial.kind == "trial"
    # Single leased trial: a second request in the same window gets nothing.
    assert breaker.claim(SRC, clock.monotonic()) is None


def test_trial_success_closes_and_failure_reopens(clock: VirtualClock) -> None:
    """Mutant: never re-opens."""

    breaker = SourceBreaker(clock=clock)
    _open(breaker, clock)
    clock.advance(BREAKER_OPEN_SECONDS)
    trial = breaker.claim(SRC, clock.monotonic())
    assert trial is not None
    trial.settle("failure")
    assert breaker.state(SRC, clock.monotonic()) == "open"
    assert breaker.claim(SRC, clock.monotonic()) is None

    clock.advance(BREAKER_OPEN_SECONDS)
    trial = breaker.claim(SRC, clock.monotonic())
    assert trial is not None
    trial.settle("success")
    assert breaker.state(SRC, clock.monotonic()) == "closed"
    assert breaker.failures(SRC) == 0
    # Closed again: tokens flow and the threshold counts from zero.
    _fail(breaker, clock, BREAKER_FAILURE_THRESHOLD - 1)
    assert breaker.state(SRC, clock.monotonic()) == "closed"


def test_trial_released_by_a_decline_makes_the_next_request_the_trial(clock: VirtualClock) -> None:
    """CL-2: a portability decline / route-latch release settles ``inconclusive`` and frees the trial."""

    breaker = SourceBreaker(clock=clock)
    _open(breaker, clock)
    clock.advance(BREAKER_OPEN_SECONDS)
    first = breaker.claim(SRC, clock.monotonic())
    assert first is not None
    assert breaker.claim(SRC, clock.monotonic()) is None
    first.settle("inconclusive")
    assert breaker.state(SRC, clock.monotonic()) == "half_open"
    second = breaker.claim(SRC, clock.monotonic())
    assert second is not None and second is not first
    second.settle("success")
    assert breaker.state(SRC, clock.monotonic()) == "closed"


def test_trial_lease_expiry_readmits_and_the_stale_lease_is_ignored(clock: VirtualClock, caplog) -> None:
    """Mutant: no lease => a lost trial claim wedges the breaker half-open forever."""

    breaker = SourceBreaker(clock=clock)
    _open(breaker, clock)
    clock.advance(BREAKER_OPEN_SECONDS)
    lost = breaker.claim(SRC, clock.monotonic())
    assert lost is not None
    clock.advance(TRIAL_LEASE_TTL_SECONDS - 0.001)
    assert breaker.claim(SRC, clock.monotonic()) is None
    clock.advance(0.002)
    caplog.set_level(logging.WARNING, logger="app.modules.proxy.overflow")
    replacement = breaker.claim(SRC, clock.monotonic())
    assert replacement is not None and replacement is not lost
    assert "trial=lease_expired" in caplog.text
    # The lost lease turning up late changes nothing: only the current trial decides.
    lost.settle("success")
    assert breaker.state(SRC, clock.monotonic()) == "half_open"
    replacement.settle("failure")
    assert breaker.state(SRC, clock.monotonic()) == "open"


def test_trial_first_output_item_closes_the_breaker_while_the_stream_runs(clock: VirtualClock, caplog) -> None:
    """Design §8.3: the first output item yielded closes the breaker. Mutant (the trial settles only at the
    terminal): a recovered source streaming a long answer keeps the breaker half-open with the lease held, and every
    other overflow request is ``breaker_open`` until that stream ends or the 120 s lease expires."""

    breaker = SourceBreaker(clock=clock)
    _open(breaker, clock)
    clock.advance(BREAKER_OPEN_SECONDS)
    trial = breaker.claim(SRC, clock.monotonic())
    assert trial is not None and trial.kind == "trial"
    assert breaker.claim(SRC, clock.monotonic()) is None
    caplog.set_level(logging.WARNING, logger="app.modules.proxy.overflow")

    trial.observe_first_output_item()

    assert breaker.state(SRC, clock.monotonic()) == "closed"
    assert "model_source_breaker state=closed" in caplog.text and "cause=trial_first_output_item" in caplog.text
    # The trial's stream is still running: other requests flow with closed-state tokens.
    concurrent = breaker.claim(SRC, clock.monotonic())
    assert concurrent is not None and concurrent.kind == "closed"
    assert trial.settled is False
    trial.settle("success")
    concurrent.settle("success")
    assert breaker.state(SRC, clock.monotonic()) == "closed" and breaker.failures(SRC) == 0


def test_trial_that_stalls_after_its_first_item_counts_one_closed_failure(clock: VirtualClock) -> None:
    """A post-item idle timeout or transport drop is a counted failure (design §8.3): the trial closed the breaker
    at its first item and its terminal ``failure`` counts toward the next trip like any closed-state token."""

    breaker = SourceBreaker(clock=clock)
    _open(breaker, clock)
    clock.advance(BREAKER_OPEN_SECONDS)
    trial = breaker.claim(SRC, clock.monotonic())
    assert trial is not None
    trial.observe_first_output_item()
    trial.settle("failure")
    assert breaker.state(SRC, clock.monotonic()) == "closed"
    assert breaker.failures(SRC) == 1
    _fail(breaker, clock, BREAKER_FAILURE_THRESHOLD - 1)
    assert breaker.state(SRC, clock.monotonic()) == "open"


def test_first_output_item_on_a_closed_token_leaves_the_terminal_result_to_decide(clock: VirtualClock) -> None:
    """Mutant: an early ``success`` at the first item resets the consecutive count, so three item-then-stall
    streams -- each a counted failure -- could never open the breaker."""

    breaker = SourceBreaker(clock=clock)
    for _ in range(BREAKER_FAILURE_THRESHOLD):
        token = breaker.claim(SRC, clock.monotonic())
        assert token is not None and token.kind == "closed"
        token.observe_first_output_item()
        assert breaker.state(SRC, clock.monotonic()) == "closed"
        token.settle("failure")
    assert breaker.state(SRC, clock.monotonic()) == "open"


def test_first_output_item_is_idempotent_and_ignored_for_settled_or_superseded_tokens(clock: VirtualClock) -> None:
    breaker = SourceBreaker(clock=clock)
    _open(breaker, clock)
    clock.advance(BREAKER_OPEN_SECONDS)
    lost = breaker.claim(SRC, clock.monotonic())
    assert lost is not None
    clock.advance(TRIAL_LEASE_TTL_SECONDS + 1)
    replacement = breaker.claim(SRC, clock.monotonic())
    assert replacement is not None and replacement is not lost
    # A superseded lease turning up with an item changes nothing and stays superseded for its terminal settle.
    lost.observe_first_output_item()
    assert breaker.state(SRC, clock.monotonic()) == "half_open" and lost.kind == "trial"
    lost.settle("failure")
    assert breaker.state(SRC, clock.monotonic()) == "half_open"
    replacement.observe_first_output_item()
    replacement.observe_first_output_item()
    assert breaker.state(SRC, clock.monotonic()) == "closed"
    replacement.settle("success")
    replacement.observe_first_output_item()
    assert breaker.state(SRC, clock.monotonic()) == "closed" and breaker.failures(SRC) == 0


def test_gauge_follows_the_transitions(clock: VirtualClock, gauge: _Gauge) -> None:
    breaker = SourceBreaker(clock=clock)
    _open(breaker, clock)
    clock.advance(BREAKER_OPEN_SECONDS)
    trial = breaker.claim(SRC, clock.monotonic())
    assert trial is not None
    trial.settle("success")
    values = gauge.values[SRC]
    assert values[0] == 0  # first claim publishes closed
    assert 1 in values  # opened
    assert 2 in values  # half-open claim
    assert values[-1] == 0  # closed by the trial
    assert values.index(1) < values.index(2) < len(values) - 1


def test_breaker_rejects_degenerate_tunables(clock: VirtualClock) -> None:
    with pytest.raises(ValueError):
        SourceBreaker(clock=clock, failure_threshold=0)
    with pytest.raises(ValueError):
        SourceBreaker(clock=clock, open_seconds=0)
    with pytest.raises(ValueError):
        SourceBreaker(clock=clock, trial_lease_seconds=-1)


def test_get_source_breaker_is_a_process_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(overflow_module, "_BREAKER", None)
    first = get_source_breaker()
    assert get_source_breaker() is first
    assert first.state(SRC, 0.0) == "closed"


# --- try_claim_overflow ------------------------------------------------------------------------


def test_try_claim_overflow_holds_the_slot_and_the_token_until_one_release(clock: VirtualClock) -> None:
    breaker = SourceBreaker(clock=clock)
    bulkhead = SourceBulkhead()
    source = _source(max_concurrency=1)
    claims = try_claim_overflow(source, breaker=breaker, now=clock.monotonic(), bulkhead=bulkhead)
    assert isinstance(claims, SourceAdmission)
    assert bulkhead.in_flight(SRC) == 1
    assert isinstance(claims.trial, BreakerToken)
    claims.release("failure")
    claims.release("failure")
    assert bulkhead.in_flight(SRC) == 0
    assert breaker.failures(SRC) == 1


def test_try_claim_overflow_denies_while_open_without_touching_the_bulkhead(clock: VirtualClock) -> None:
    """Pinned/anchored contexts answer 503 from this denial; fresh declines ``breaker_open``."""

    breaker = SourceBreaker(clock=clock)
    _open(breaker, clock)
    bulkhead = SourceBulkhead()
    denied = try_claim_overflow(_source(), breaker=breaker, now=clock.monotonic(), bulkhead=bulkhead)
    assert denied == ClaimDenied("breaker_open")
    assert bulkhead.in_flight(SRC) == 0


def test_try_claim_overflow_releases_the_trial_when_the_bulkhead_is_saturated(clock: VirtualClock) -> None:
    """Mutant: trial claimed without release on a bulkhead denial wedges the half-open breaker."""

    breaker = SourceBreaker(clock=clock)
    _open(breaker, clock)
    clock.advance(BREAKER_OPEN_SECONDS)
    bulkhead = SourceBulkhead()
    source = _source(max_concurrency=1)
    occupant = bulkhead.try_acquire(SRC, 1)
    assert occupant is not None

    denied = try_claim_overflow(source, breaker=breaker, now=clock.monotonic(), bulkhead=bulkhead)
    assert denied == ClaimDenied("source_busy")
    assert breaker.state(SRC, clock.monotonic()) == "half_open"
    # The trial is free again: the next request becomes it.
    bulkhead.release(occupant)
    claims = try_claim_overflow(source, breaker=breaker, now=clock.monotonic(), bulkhead=bulkhead)
    assert isinstance(claims, SourceAdmission)
    assert isinstance(claims.trial, BreakerToken) and claims.trial.kind == "trial"
    claims.release_if_unowned()
    assert breaker.state(SRC, clock.monotonic()) == "half_open"
    assert bulkhead.in_flight(SRC) == 0


def test_route_helper_latch_release_is_inconclusive_for_the_trial(clock: VirtualClock) -> None:
    breaker = SourceBreaker(clock=clock)
    _open(breaker, clock)
    clock.advance(BREAKER_OPEN_SECONDS)
    claims = try_claim_overflow(_source(), breaker=breaker, now=clock.monotonic(), bulkhead=SourceBulkhead())
    assert isinstance(claims, SourceAdmission)
    claims.release_if_unowned()
    assert breaker.state(SRC, clock.monotonic()) == "half_open"
    assert breaker.claim(SRC, clock.monotonic()) is not None

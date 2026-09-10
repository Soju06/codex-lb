from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

import app.modules.proxy._service.streaming.helpers as streaming_helpers_module
from app.core.balancer import ERROR_BACKOFF_THRESHOLD
from app.core.balancer.logic import AccountState
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, StickySessionKind
from app.modules.proxy._load_balancer.overload_backoff import (
    BURST_BACKOFF_DEFAULT_SECONDS,
    BURST_BACKOFF_MAX_SECONDS,
    OVERLOAD_BACKOFF_BASE_SECONDS,
    OVERLOAD_BACKOFF_MAX_SECONDS,
    OVERLOAD_ISOLATION_TRIP_LEVEL,
    OVERLOAD_LEVEL_DECAY_SECONDS,
    OVERLOAD_MAX_LEVEL,
    OVERLOAD_TRIP_COUNT,
    OVERLOAD_WINDOW_SECONDS,
    OverloadIsolationPolicy,
    filter_overload_backoff_candidates,
    overload_backoff_active,
    overload_backoff_seconds,
    overload_isolation_active,
    record_burst_rejection_locked,
    record_overload_rejection_locked,
    record_upstream_burst_rejection,
    record_upstream_overload,
    sticky_owner_isolation_reroute_pool,
)
from app.modules.proxy._load_balancer.types import RuntimeState
from app.modules.proxy._service.streaming.retry import _transient_retry_error_code
from app.modules.proxy._service.support import _TransientStreamError
from app.modules.proxy.load_balancer import LoadBalancer
from tests.simulation.virtual_time import VirtualClock
from tests.unit.test_load_balancer_concurrency import (
    _repo_factory,
    _StubAccountsRepository,
    _StubUsageRepository,
)

pytestmark = pytest.mark.unit


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


def _state(account_id: str) -> AccountState:
    return AccountState(account_id=account_id, status=AccountStatus.ACTIVE, used_percent=0.0)


def test_window_trips_only_on_the_third_rejection_inside_the_window() -> None:
    runtime = RuntimeState()
    assert record_overload_rejection_locked(runtime, 1000.0) is None
    assert record_overload_rejection_locked(runtime, 1010.0) is None
    assert not overload_backoff_active(runtime, 1010.0)

    deadline = record_overload_rejection_locked(runtime, 1020.0)

    assert deadline == pytest.approx(1020.0 + OVERLOAD_BACKOFF_BASE_SECONDS)
    assert runtime.overload_backoff_level == 1
    assert runtime.overload_rejections == []
    assert overload_backoff_active(runtime, 1020.0 + OVERLOAD_BACKOFF_BASE_SECONDS - 1)
    assert not overload_backoff_active(runtime, 1020.0 + OVERLOAD_BACKOFF_BASE_SECONDS)


def test_rejections_outside_the_window_do_not_count() -> None:
    runtime = RuntimeState()
    record_overload_rejection_locked(runtime, 0.0)
    record_overload_rejection_locked(runtime, 1.0)
    # Two stale rejections plus one fresh one: below the trip count.
    assert record_overload_rejection_locked(runtime, OVERLOAD_WINDOW_SECONDS + 5.0) is None
    assert runtime.overload_rejections == [OVERLOAD_WINDOW_SECONDS + 5.0]


def test_repeated_trips_grow_exponentially_and_are_capped() -> None:
    runtime = RuntimeState()
    now = 0.0
    deadlines: list[float] = []
    for _ in range(6):
        for _ in range(OVERLOAD_TRIP_COUNT - 1):
            assert record_overload_rejection_locked(runtime, now) is None
        deadline = record_overload_rejection_locked(runtime, now)
        assert deadline is not None
        deadlines.append(deadline - now)
        now = deadline  # next burst starts right when the backoff expires

    assert deadlines[:4] == pytest.approx(
        [
            OVERLOAD_BACKOFF_BASE_SECONDS,
            OVERLOAD_BACKOFF_BASE_SECONDS * 2,
            OVERLOAD_BACKOFF_BASE_SECONDS * 4,
            OVERLOAD_BACKOFF_BASE_SECONDS * 8,
        ]
    )
    assert deadlines[-1] == pytest.approx(OVERLOAD_BACKOFF_MAX_SECONDS)


def test_level_saturates_so_sustained_overload_cannot_overflow() -> None:
    assert overload_backoff_seconds(10_000) == OVERLOAD_BACKOFF_MAX_SECONDS
    runtime = RuntimeState(overload_backoff_level=OVERLOAD_MAX_LEVEL, overload_last_trip_at=0.0)
    now = 10.0
    for _ in range(OVERLOAD_TRIP_COUNT - 1):
        record_overload_rejection_locked(runtime, now)
    deadline = record_overload_rejection_locked(runtime, now)
    assert runtime.overload_backoff_level == OVERLOAD_MAX_LEVEL
    assert deadline == pytest.approx(now + OVERLOAD_BACKOFF_MAX_SECONDS)


def test_trip_while_deprioritized_never_shortens_the_deadline() -> None:
    runtime = RuntimeState(overload_backoff_until=5000.0, overload_backoff_level=5, overload_last_trip_at=4000.0)
    for _ in range(OVERLOAD_TRIP_COUNT - 1):
        record_overload_rejection_locked(runtime, 4500.0)
    deadline = record_overload_rejection_locked(runtime, 4500.0)
    # Level 6 => 60 * 2**5 = 1920 s, capped at 600 s => 5100 > 5000.
    assert deadline == pytest.approx(4500.0 + OVERLOAD_BACKOFF_MAX_SECONDS)

    runtime = RuntimeState(overload_backoff_until=9000.0, overload_backoff_level=1, overload_last_trip_at=4000.0)
    for _ in range(OVERLOAD_TRIP_COUNT - 1):
        record_overload_rejection_locked(runtime, 4500.0)
    assert record_overload_rejection_locked(runtime, 4500.0) == 9000.0


def test_level_decays_after_a_quiet_period() -> None:
    runtime = RuntimeState(overload_backoff_level=4, overload_last_trip_at=0.0)
    now = OVERLOAD_LEVEL_DECAY_SECONDS + 1.0
    for _ in range(OVERLOAD_TRIP_COUNT - 1):
        record_overload_rejection_locked(runtime, now)
    deadline = record_overload_rejection_locked(runtime, now)
    assert runtime.overload_backoff_level == 1
    assert deadline == pytest.approx(now + OVERLOAD_BACKOFF_BASE_SECONDS)


def _filter(states: list[AccountState], runtime: dict[str, RuntimeState], now: float) -> list[AccountState]:
    return filter_overload_backoff_candidates(states, runtime, now=now)


def test_filter_returns_the_overload_free_remainder_or_the_pool_itself() -> None:
    now = 1000.0
    runtime = {
        "hot": RuntimeState(overload_backoff_until=now + 30.0),
        "expired": RuntimeState(overload_backoff_until=now - 1.0),
        "clean": RuntimeState(),
    }
    states = [_state("hot"), _state("expired"), _state("clean"), _state("unknown")]

    kept = _filter(states, runtime, now)
    assert [state.account_id for state in kept] == ["expired", "clean", "unknown"]

    only_hot = [_state("hot")]
    assert _filter(only_hot, runtime, now) is only_hot

    all_hot = [_state("hot"), _state("hot2")]
    runtime["hot2"] = RuntimeState(overload_backoff_until=now + 5.0)
    assert _filter(all_hot, runtime, now) is all_hot

    untouched = [_state("clean"), _state("expired")]
    assert _filter(untouched, runtime, now) is untouched


def test_transient_retry_error_code_keeps_overload_codes_from_http_status_failures() -> None:
    def _http_failure(code: str | None) -> SimpleNamespace:
        error: dict[str, object] = {"message": "Our servers are currently overloaded.", "type": "server_error"}
        if code is not None:
            error["code"] = code
        return SimpleNamespace(payload={"error": error}, status_code=500)

    assert _transient_retry_error_code(cast(BaseException, _http_failure("server_is_overloaded"))) == (
        "server_is_overloaded"
    )
    assert _transient_retry_error_code(cast(BaseException, _http_failure("unknown_thing"))) == "server_error"
    assert _transient_retry_error_code(cast(BaseException, _http_failure(None))) == "server_error"
    assert _transient_retry_error_code(RuntimeError("no payload at all")) == "server_error"
    framed = _TransientStreamError("stream_incomplete", {"message": "cut"})
    assert _transient_retry_error_code(framed) == "stream_incomplete"


@pytest.mark.asyncio
async def test_record_upstream_overload_writes_runtime_under_the_account_lock(caplog: pytest.LogCaptureFixture) -> None:
    clock = VirtualClock(epoch_value=2_000_000_000.0)
    balancer = LoadBalancer(cast(Any, None), clock=clock)
    account = _make_account("acc-overloaded")

    with caplog.at_level(logging.WARNING, logger="app.modules.proxy._load_balancer.overload_backoff"):
        for _ in range(OVERLOAD_TRIP_COUNT - 1):
            await record_upstream_overload(balancer, account)
            clock.advance(1.0)
        assert not overload_backoff_active(balancer._runtime[account.id], clock.time())
        assert "overload backoff engaged" not in caplog.text

        await record_upstream_overload(balancer, account, redact_account_id=True)

    runtime = balancer._runtime[account.id]
    assert runtime.overload_backoff_level == 1
    assert runtime.overload_backoff_until == pytest.approx(clock.time() + OVERLOAD_BACKOFF_BASE_SECONDS)
    assert "Account overload backoff engaged account_id=<redacted> level=1" in caplog.text
    # The generic error counters are untouched: this window is independent of
    # ``record_success`` resetting ``error_count``.
    assert runtime.error_count == 0


@pytest.mark.asyncio
async def test_record_upstream_overload_ignores_balancers_without_a_runtime_map() -> None:
    balancer = SimpleNamespace(record_error=AsyncMock())
    await record_upstream_overload(balancer, _make_account("acc-double"))


@pytest.mark.asyncio
async def test_handle_stream_error_feeds_the_overload_window_for_overload_codes_only() -> None:
    clock = VirtualClock(epoch_value=2_000_000_000.0)
    balancer = LoadBalancer(cast(Any, None), clock=clock)
    account = _make_account("acc-stream")
    proxy = SimpleNamespace(_load_balancer=balancer)
    # Keep the generic path inert: this test pins the overload hook only.
    balancer.record_error = AsyncMock()  # type: ignore[method-assign]

    classified = await streaming_helpers_module._handle_stream_error(
        proxy,
        account,
        {"message": "Our servers are currently overloaded. Please try again later."},
        "server_is_overloaded",
        None,
    )
    assert classified["failure_class"] == "retryable_transient"
    assert balancer._runtime[account.id].overload_rejections == [clock.time()]
    balancer.record_error.assert_awaited_once()

    await streaming_helpers_module._handle_stream_error(
        proxy,
        account,
        {"message": "upstream hiccup"},
        "server_error",
        None,
    )
    assert balancer._runtime[account.id].overload_rejections == [clock.time()]
    assert balancer.record_error.await_count == 2
    # Neither code carried an HTTP 429, so the burst cooldown stays disengaged.
    assert balancer._runtime[account.id].burst_backoff_until is None


@pytest.mark.asyncio
async def test_select_account_skips_backed_off_account_while_a_healthy_sibling_exists() -> None:
    clock = VirtualClock(epoch_value=2_000_000_000.0)
    hot = _make_account("acc-hot")
    clean = _make_account("acc-clean")
    balancer = LoadBalancer(
        lambda: _repo_factory(_StubAccountsRepository([hot, clean]), _StubUsageRepository({}, {})),
        clock=clock,
    )
    balancer._runtime[hot.id] = RuntimeState(overload_backoff_until=clock.time() + OVERLOAD_BACKOFF_BASE_SECONDS)

    # Equal weights: 40 draws all landing on ``clean`` is 2**-40 by chance.
    for _ in range(40):
        result = await balancer.select_account()
        assert result.account is not None
        assert result.account.id == clean.id

    clock.advance(OVERLOAD_BACKOFF_BASE_SECONDS + 1.0)
    selected: set[str] = set()
    for _ in range(40):
        result = await balancer.select_account()
        assert result.account is not None
        selected.add(result.account.id)
    assert hot.id in selected


@pytest.mark.asyncio
async def test_select_account_still_uses_backed_off_account_when_every_sibling_is_in_error_backoff() -> None:
    clock = VirtualClock(epoch_value=2_000_000_000.0)
    hot = _make_account("acc-hot-only")
    erroring = _make_account("acc-erroring")
    balancer = LoadBalancer(
        lambda: _repo_factory(_StubAccountsRepository([hot, erroring]), _StubUsageRepository({}, {})),
        clock=clock,
    )
    balancer._runtime[hot.id] = RuntimeState(overload_backoff_until=clock.time() + OVERLOAD_BACKOFF_BASE_SECONDS)
    balancer._runtime[erroring.id] = RuntimeState(error_count=ERROR_BACKOFF_THRESHOLD, last_error_at=clock.time())

    result = await balancer.select_account(lease_kind="stream")

    assert result.error_code is None, result.error_message
    assert result.account is not None
    assert result.account.id == hot.id


@asynccontextmanager
async def _mock_repo_factory():
    yield AsyncMock()


def _sticky_repo(existing_account_id: str | None) -> AsyncMock:
    repo = AsyncMock()
    repo.get_account_id = AsyncMock(return_value=existing_account_id)
    repo.upsert = AsyncMock()
    repo.delete = AsyncMock()
    return repo


async def _select_sticky(balancer: LoadBalancer, states: list[AccountState], repo: AsyncMock):
    account_map = {state.account_id: cast(Account, AsyncMock()) for state in states}
    outcome = await balancer._select_with_stickiness(
        states=states,
        account_map=account_map,
        sticky_key="fresh-or-owned-key",
        sticky_kind=StickySessionKind.PROMPT_CACHE,
        reallocate_sticky=False,
        sticky_max_age_seconds=600,
        prefer_earlier_reset_accounts=False,
        prefer_earlier_reset_window="secondary",
        routing_strategy="usage_weighted",
        sticky_repo=repo,
    )
    return outcome.selection


@pytest.mark.asyncio
async def test_fresh_sticky_binding_avoids_backed_off_account_but_established_owner_is_kept() -> None:
    clock = VirtualClock(epoch_value=2_000_000_000.0)
    balancer = LoadBalancer(_mock_repo_factory, clock=clock)
    balancer._runtime["hot"] = RuntimeState(overload_backoff_until=clock.time() + OVERLOAD_BACKOFF_BASE_SECONDS)
    states = [_state("hot"), _state("clean")]

    # A previously unseen key is a fresh upstream admission: bind away from the overloaded account.
    for _ in range(40):
        fresh = await _select_sticky(balancer, [_state("hot"), _state("clean")], _sticky_repo(None))
        assert fresh.account is not None
        assert fresh.account.account_id == "clean"

    # An established owner is warm-session reuse: the overload window never touches it.
    owned = await _select_sticky(balancer, states, _sticky_repo("hot"))
    assert owned.account is not None
    assert owned.account.account_id == "hot"

    # With no overload-free alternative the fresh binding still lands on the backed-off account.
    alone = await _select_sticky(balancer, [_state("hot")], _sticky_repo(None))
    assert alone.account is not None
    assert alone.account.account_id == "hot"


@pytest.mark.asyncio
async def test_fresh_sticky_binding_reports_the_pool_it_selected_from() -> None:
    """Probe reservation in the sticky run path reuses ``effective_states``; it
    must name the overload-free pool only when that pool produced the pick."""
    clock = VirtualClock(epoch_value=2_000_000_000.0)
    balancer = LoadBalancer(_mock_repo_factory, clock=clock)
    balancer._runtime["hot"] = RuntimeState(overload_backoff_until=clock.time() + OVERLOAD_BACKOFF_BASE_SECONDS)
    hot, clean = _state("hot"), _state("clean")
    account_map = {state.account_id: cast(Account, AsyncMock()) for state in (hot, clean)}

    async def _outcome(states: list[AccountState], existing: str | None):
        return await balancer._select_with_stickiness(
            states=states,
            account_map=account_map,
            sticky_key="key",
            sticky_kind=StickySessionKind.PROMPT_CACHE,
            reallocate_sticky=False,
            sticky_max_age_seconds=600,
            prefer_earlier_reset_accounts=False,
            prefer_earlier_reset_window="secondary",
            routing_strategy="usage_weighted",
            sticky_repo=_sticky_repo(existing),
        )

    filtered = await _outcome([hot, clean], None)
    assert filtered.selection.account is not None and filtered.selection.account.account_id == "clean"
    assert filtered.effective_states is not None
    assert [state.account_id for state in filtered.effective_states] == ["clean"]

    unfiltered = await _outcome([hot], None)
    assert unfiltered.selection.account is not None and unfiltered.selection.account.account_id == "hot"
    assert unfiltered.effective_states is None

    owned = await _outcome([hot, clean], "hot")
    assert owned.selection.account is not None and owned.selection.account.account_id == "hot"
    assert owned.effective_states is None


# --- isolation stage -------------------------------------------------------


def test_isolation_engages_at_the_trip_level_and_holds_for_the_configured_interval() -> None:
    policy = OverloadIsolationPolicy(seconds=1800.0)
    runtime = RuntimeState()
    now = 0.0
    for level in range(1, OVERLOAD_ISOLATION_TRIP_LEVEL + 1):
        for _ in range(OVERLOAD_TRIP_COUNT - 1):
            assert record_overload_rejection_locked(runtime, now, isolation=policy) is None
        deadline = record_overload_rejection_locked(runtime, now, isolation=policy)
        assert deadline is not None
        if level < OVERLOAD_ISOLATION_TRIP_LEVEL:
            assert deadline - now == pytest.approx(overload_backoff_seconds(level))
            assert not overload_isolation_active(runtime, now)
        else:
            assert deadline - now == pytest.approx(1800.0)
            assert overload_isolation_active(runtime, now)
            assert overload_backoff_active(runtime, now)
        now = deadline


def test_isolation_disabled_when_the_interval_is_zero() -> None:
    policy = OverloadIsolationPolicy(seconds=0.0)
    assert not policy.enabled
    runtime = RuntimeState(overload_backoff_level=OVERLOAD_MAX_LEVEL, overload_last_trip_at=0.0)
    for _ in range(OVERLOAD_TRIP_COUNT - 1):
        record_overload_rejection_locked(runtime, 10.0, isolation=policy)
    deadline = record_overload_rejection_locked(runtime, 10.0, isolation=policy)
    assert deadline == pytest.approx(10.0 + OVERLOAD_BACKOFF_MAX_SECONDS)
    assert runtime.overload_isolated_until is None


def test_level_does_not_decay_while_the_account_is_held_out() -> None:
    # Isolated for 1800 s: the quiet period is measured from the deadline, so a
    # trip right after isolation ends keeps the saturated level (re-isolates)
    # instead of dropping back to a 60 s soft backoff.
    policy = OverloadIsolationPolicy(seconds=1800.0)
    runtime = RuntimeState(
        overload_backoff_level=OVERLOAD_ISOLATION_TRIP_LEVEL,
        overload_last_trip_at=0.0,
        overload_backoff_until=1800.0,
        overload_isolated_until=1800.0,
    )
    now = 1800.0 + 60.0
    for _ in range(OVERLOAD_TRIP_COUNT - 1):
        record_overload_rejection_locked(runtime, now, isolation=policy)
    deadline = record_overload_rejection_locked(runtime, now, isolation=policy)
    assert runtime.overload_backoff_level == OVERLOAD_ISOLATION_TRIP_LEVEL + 1
    assert deadline == pytest.approx(now + 1800.0)
    assert overload_isolation_active(runtime, now)

    # A full quiet window after the deadline decays the level as before.
    quiet = RuntimeState(overload_backoff_level=4, overload_last_trip_at=0.0, overload_backoff_until=100.0)
    later = 100.0 + OVERLOAD_LEVEL_DECAY_SECONDS + 1.0
    for _ in range(OVERLOAD_TRIP_COUNT - 1):
        record_overload_rejection_locked(quiet, later, isolation=policy)
    record_overload_rejection_locked(quiet, later, isolation=policy)
    assert quiet.overload_backoff_level == 1


@pytest.mark.asyncio
async def test_record_upstream_overload_logs_isolation_at_the_trip_level(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_LB_PROXY_OVERLOAD_ISOLATION_SECONDS", "900")
    get_settings.cache_clear()
    try:
        clock = VirtualClock(epoch_value=2_000_000_000.0)
        balancer = LoadBalancer(cast(Any, None), clock=clock)
        account = _make_account("acc-sustained")
        runtime = balancer._runtime.setdefault(account.id, RuntimeState())
        runtime.overload_backoff_level = OVERLOAD_ISOLATION_TRIP_LEVEL - 1
        runtime.overload_last_trip_at = clock.time()
        with caplog.at_level(logging.WARNING, logger="app.modules.proxy._load_balancer.overload_backoff"):
            for _ in range(OVERLOAD_TRIP_COUNT):
                await record_upstream_overload(balancer, account)
        assert (
            "Account overload isolation engaged account_id=acc-sustained level=3 isolation_seconds=900" in caplog.text
        )
        assert overload_isolation_active(runtime, clock.time())
        assert runtime.overload_backoff_until == pytest.approx(clock.time() + 900.0)
    finally:
        get_settings.cache_clear()


def _isolated_runtime(now: float, *, seconds: float = 1800.0) -> RuntimeState:
    return RuntimeState(
        overload_backoff_until=now + seconds,
        overload_isolated_until=now + seconds,
        overload_backoff_level=OVERLOAD_ISOLATION_TRIP_LEVEL,
        overload_last_trip_at=now,
    )


def test_sticky_owner_reroute_pool_requires_isolation_and_an_overload_free_sibling() -> None:
    now = 1000.0
    states = [_state("hot"), _state("clean")]
    soft = {"hot": RuntimeState(overload_backoff_until=now + 60.0)}
    assert sticky_owner_isolation_reroute_pool(states, soft, owner_account_id="hot", now=now) is None
    isolated = {"hot": _isolated_runtime(now)}
    pool = sticky_owner_isolation_reroute_pool(states, isolated, owner_account_id="hot", now=now)
    assert pool is not None and [state.account_id for state in pool] == ["clean"]
    # Every sibling isolated too: the owner keeps its session.
    both = {"hot": _isolated_runtime(now), "clean": _isolated_runtime(now)}
    assert sticky_owner_isolation_reroute_pool(states, both, owner_account_id="hot", now=now) is None
    assert sticky_owner_isolation_reroute_pool([_state("hot")], isolated, owner_account_id="hot", now=now) is None
    assert sticky_owner_isolation_reroute_pool(states, None, owner_account_id="hot", now=now) is None
    # Expired isolation releases nothing.
    assert sticky_owner_isolation_reroute_pool(states, isolated, owner_account_id="hot", now=now + 1801.0) is None


async def _select_sticky_outcome(
    balancer: LoadBalancer,
    states: list[AccountState],
    repo: AsyncMock,
    *,
    kind: StickySessionKind = StickySessionKind.PROMPT_CACHE,
    initial_preferred_account_id: str | None = None,
):
    account_map = {state.account_id: cast(Account, AsyncMock()) for state in states}
    return await balancer._select_with_stickiness(
        states=states,
        account_map=account_map,
        sticky_key="owned-key",
        sticky_kind=kind,
        reallocate_sticky=False,
        sticky_max_age_seconds=600 if kind == StickySessionKind.PROMPT_CACHE else None,
        prefer_earlier_reset_accounts=False,
        prefer_earlier_reset_window="secondary",
        routing_strategy="usage_weighted",
        sticky_repo=repo,
        initial_preferred_account_id=initial_preferred_account_id,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [StickySessionKind.PROMPT_CACHE, StickySessionKind.STICKY_THREAD, StickySessionKind.CODEX_SESSION],
)
async def test_isolated_soft_sticky_owner_is_rerouted_and_rebound(kind: StickySessionKind) -> None:
    clock = VirtualClock(epoch_value=2_000_000_000.0)
    balancer = LoadBalancer(_mock_repo_factory, clock=clock)
    balancer._runtime["hot"] = _isolated_runtime(clock.time())

    outcome = await _select_sticky_outcome(balancer, [_state("hot"), _state("clean")], _sticky_repo("hot"), kind=kind)
    assert outcome.selection.account is not None
    assert outcome.selection.account.account_id == "clean"
    # The session is rebound to the replacement, not left flapping back to the
    # isolated owner on every request.
    assert outcome.mutation is not None and outcome.mutation.account_id == "clean"
    assert outcome.effective_states is not None
    assert [state.account_id for state in outcome.effective_states] == ["clean"]


@pytest.mark.asyncio
async def test_soft_backoff_below_isolation_keeps_the_established_owner() -> None:
    clock = VirtualClock(epoch_value=2_000_000_000.0)
    balancer = LoadBalancer(_mock_repo_factory, clock=clock)
    balancer._runtime["hot"] = RuntimeState(
        overload_backoff_until=clock.time() + OVERLOAD_BACKOFF_MAX_SECONDS,
        overload_backoff_level=OVERLOAD_ISOLATION_TRIP_LEVEL - 1,
    )
    outcome = await _select_sticky_outcome(balancer, [_state("hot"), _state("clean")], _sticky_repo("hot"))
    assert outcome.selection.account is not None
    assert outcome.selection.account.account_id == "hot"
    assert outcome.mutation is None or outcome.mutation.account_id in (None, "hot")


@pytest.mark.asyncio
async def test_isolated_owner_is_kept_when_no_overload_free_sibling_can_be_selected() -> None:
    clock = VirtualClock(epoch_value=2_000_000_000.0)
    balancer = LoadBalancer(_mock_repo_factory, clock=clock)
    balancer._runtime["hot"] = _isolated_runtime(clock.time())

    alone = await _select_sticky_outcome(balancer, [_state("hot")], _sticky_repo("hot"))
    assert alone.selection.account is not None and alone.selection.account.account_id == "hot"

    # The only sibling is rate-limited: the strategy rejects the overload-free
    # pool, so the owner is kept rather than failing the request.
    limited = AccountState(
        account_id="limited",
        status=AccountStatus.RATE_LIMITED,
        used_percent=100.0,
        reset_at=clock.time() + 3600.0,
    )
    kept = await _select_sticky_outcome(balancer, [_state("hot"), limited], _sticky_repo("hot"))
    assert kept.selection.account is not None and kept.selection.account.account_id == "hot"


@pytest.mark.asyncio
async def test_expired_isolation_returns_the_owner_to_normal_pinning() -> None:
    clock = VirtualClock(epoch_value=2_000_000_000.0)
    balancer = LoadBalancer(_mock_repo_factory, clock=clock)
    balancer._runtime["hot"] = _isolated_runtime(clock.time(), seconds=300.0)
    clock.advance(301.0)
    outcome = await _select_sticky_outcome(balancer, [_state("hot"), _state("clean")], _sticky_repo("hot"))
    assert outcome.selection.account is not None and outcome.selection.account.account_id == "hot"


@pytest.mark.asyncio
async def test_backed_off_process_session_preference_is_skipped_for_a_fresh_thread() -> None:
    clock = VirtualClock(epoch_value=2_000_000_000.0)
    balancer = LoadBalancer(_mock_repo_factory, clock=clock)
    balancer._runtime["hot"] = RuntimeState(overload_backoff_until=clock.time() + OVERLOAD_BACKOFF_BASE_SECONDS)

    outcome = await _select_sticky_outcome(
        balancer,
        [_state("hot"), _state("clean")],
        _sticky_repo(None),
        kind=StickySessionKind.CODEX_SESSION,
        initial_preferred_account_id="hot",
    )
    assert outcome.selection.account is not None and outcome.selection.account.account_id == "clean"

    # Without an overload-free alternative the preference is honored as before.
    alone = await _select_sticky_outcome(
        balancer,
        [_state("hot")],
        _sticky_repo(None),
        kind=StickySessionKind.CODEX_SESSION,
        initial_preferred_account_id="hot",
    )
    assert alone.selection.account is not None and alone.selection.account.account_id == "hot"


@pytest.mark.asyncio
async def test_fresh_thread_process_preference_bypass_never_fails_a_request_the_preference_would_serve() -> None:
    """Request path (``LoadBalancer.select_account`` with a thread affinity):
    the backed-off process preference is skipped only when the strategy can
    actually select an overload-free sibling; an unselectable sibling
    (cooldown) keeps the preference instead of surfacing its rate-limit error."""
    from app.modules.proxy.affinity import _thread_codex_session_affinity
    from tests.unit.test_load_balancer_concurrency import (
        _StubStickySessionsRepository,
        _usage_row_with_percent,
    )

    clock = VirtualClock(epoch_value=2_000_000_000.0)
    now = int(clock.time())
    preferred = _make_account("acc-preferred")
    sibling = _make_account("acc-sibling")
    affinity = _thread_codex_session_affinity(
        {"session_id": "process", "thread-id": "fresh"}, enabled=True, max_age_seconds=600
    )
    assert affinity is not None
    seed_key = affinity.seed_selection_key
    assert seed_key is not None

    def _balancer(*, sibling_in_cooldown: bool) -> LoadBalancer:
        sticky_repo = _StubStickySessionsRepository()
        sticky_repo.account_ids_by_key = {seed_key: preferred.id}
        usage = _StubUsageRepository(
            {
                preferred.id: _usage_row_with_percent(1, preferred.id, used_percent=96.0, reset_at=now + 3600),
                sibling.id: _usage_row_with_percent(2, sibling.id, used_percent=0.0, reset_at=now + 3600),
            },
            {},
        )
        balancer = LoadBalancer(
            lambda: _repo_factory(_StubAccountsRepository([preferred, sibling]), usage, sticky_repo),
            clock=clock,
        )
        balancer._runtime[preferred.id] = RuntimeState(overload_backoff_until=clock.time() + 60.0)
        if sibling_in_cooldown:
            balancer._runtime[sibling.id] = RuntimeState(cooldown_until=clock.time() + 3600.0)
        return balancer

    kept = await _balancer(sibling_in_cooldown=True).select_account(
        **affinity.selection_kwargs(), routing_strategy="sequential_drain"
    )
    assert kept.account is not None, kept.error_message
    assert kept.account.id == preferred.id

    moved = await _balancer(sibling_in_cooldown=False).select_account(
        **affinity.selection_kwargs(), routing_strategy="sequential_drain"
    )
    assert moved.account is not None, moved.error_message
    assert moved.account.id == sibling.id


def _isolate(balancer: LoadBalancer, account_id: str) -> None:
    balancer._runtime[account_id] = _isolated_runtime(balancer._clock.time())


@pytest.mark.asyncio
async def test_isolated_bare_session_owner_is_kept_when_the_only_sibling_is_at_cap_and_spillover_is_off() -> None:
    """Request path: with cap spillover disabled the owner keeps its cap
    exemption and the isolation reroute must not pick a saturated sibling
    that lease admission then rejects (``account_stream_cap``) while the
    isolated owner still had capacity."""
    from tests.unit.test_load_balancer_concurrency import (
        _codex_session_selection_key,
        _make_cap_spillover_balancer,
    )

    balancer, owner, alternate, sticky_repo = _make_cap_spillover_balancer("iso-cap-off")
    assert alternate is not None
    _isolate(balancer, owner.id)
    saturated = [await balancer.acquire_account_lease(alternate.id, kind="stream") for _ in range(8)]
    raw_session = "bare-session-iso-cap-off"
    sticky_repo.account_ids_by_key = {_codex_session_selection_key(raw_session): owner.id}

    selected = await balancer.select_account(
        sticky_key=_codex_session_selection_key(raw_session),
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_source="session_header",
        legacy_sticky_key=raw_session,
        spill_bare_session_on_account_cap=False,
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )
    assert selected.account is not None, selected.error_message
    assert selected.account.id == owner.id
    assert sticky_repo.upserts == []
    for lease in [*saturated, selected.lease]:
        await balancer.release_account_lease(lease)

    # With capacity on the sibling the isolated owner is released and rebound.
    moved = await balancer.select_account(
        sticky_key=_codex_session_selection_key(raw_session),
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_source="session_header",
        legacy_sticky_key=raw_session,
        spill_bare_session_on_account_cap=False,
        routing_strategy="usage_weighted",
        lease_kind="stream",
    )
    assert moved.account is not None, moved.error_message
    assert moved.account.id == alternate.id
    assert any(account_id == alternate.id for _, account_id, _ in sticky_repo.upserts)
    await balancer.release_account_lease(moved.lease)


@pytest.mark.asyncio
async def test_isolated_and_capped_bare_session_owner_is_rebound_instead_of_request_local_spillover() -> None:
    """Request path: cap spillover alone preserves the mapping (the session
    returns to its owner when the cap clears); an owner that is also isolated
    is rebound to the sibling so later turns do not bounce across accounts."""
    from tests.unit.test_load_balancer_concurrency import (
        _codex_session_selection_key,
        _make_cap_spillover_balancer,
    )

    balancer, owner, alternate, sticky_repo = _make_cap_spillover_balancer("iso-cap-spill")
    assert alternate is not None
    saturated = [await balancer.acquire_account_lease(owner.id, kind="stream") for _ in range(8)]
    raw_session = "bare-session-iso-cap-spill"
    sticky_repo.account_ids_by_key = {_codex_session_selection_key(raw_session): owner.id}

    def _select():
        return balancer.select_account(
            sticky_key=_codex_session_selection_key(raw_session),
            sticky_kind=StickySessionKind.CODEX_SESSION,
            sticky_source="session_header",
            legacy_sticky_key=raw_session,
            spill_bare_session_on_account_cap=True,
            routing_strategy="usage_weighted",
            lease_kind="stream",
        )

    # Capped but not isolated: request-local spillover, mapping preserved (unchanged behavior).
    spilled = await _select()
    assert spilled.account is not None and spilled.account.id == alternate.id
    assert sticky_repo.upserts == []
    await balancer.release_account_lease(spilled.lease)

    # Capped and isolated: the fallback is rebound to the sibling.
    _isolate(balancer, owner.id)
    rebound = await _select()
    assert rebound.account is not None and rebound.account.id == alternate.id
    assert any(account_id == alternate.id for _, account_id, _ in sticky_repo.upserts)
    for lease in [*saturated, rebound.lease]:
        await balancer.release_account_lease(lease)


@pytest.mark.asyncio
async def test_budget_pressured_isolated_owner_is_released_with_the_secondary_budget_filter() -> None:
    """Round-robin would otherwise take the least-recently-selected sibling
    (the equally pressured one), which the next turn's budget reallocation
    would move again; the reroute must honor the secondary-budget filter."""
    clock = VirtualClock(epoch_value=2_000_000_000.0)
    balancer = LoadBalancer(_mock_repo_factory, clock=clock)
    balancer._runtime["owner"] = _isolated_runtime(clock.time())

    def _st(account_id: str, secondary_used: float, last_selected: float | None) -> AccountState:
        return AccountState(
            account_id=account_id,
            status=AccountStatus.ACTIVE,
            used_percent=10.0,
            secondary_used_percent=secondary_used,
            last_selected_at=last_selected,
            plan_type="plus",
        )

    states = [_st("owner", 85.0, None), _st("pressured", 85.0, None), _st("safe", 20.0, clock.time())]
    account_map = {state.account_id: cast(Account, AsyncMock()) for state in states}
    outcome = await balancer._select_with_stickiness(
        states=states,
        account_map=account_map,
        sticky_key="budget-key",
        sticky_kind=StickySessionKind.PROMPT_CACHE,
        reallocate_sticky=False,
        sticky_max_age_seconds=600,
        budget_threshold_pct=80.0,
        secondary_budget_threshold_pct=80.0,
        prefer_earlier_reset_accounts=False,
        prefer_earlier_reset_window="secondary",
        routing_strategy="round_robin",
        sticky_repo=_sticky_repo("owner"),
    )
    assert outcome.selection.account is not None
    assert outcome.selection.account.account_id == "safe"
    assert outcome.mutation is not None and outcome.mutation.account_id == "safe"


# --- burst cooldown (code-less upstream HTTP 429) ----------------------------

_BURST_LOGGER = "app.modules.proxy._load_balancer.overload_backoff"


def _burst_proxy(clock: VirtualClock) -> tuple[SimpleNamespace, LoadBalancer, AsyncMock, AsyncMock]:
    """Proxy double over a real balancer; returns ``(proxy, balancer, record_error, mark_rate_limit)``.

    The generic health writes are pinned elsewhere; here they only need to be
    observable so the test can prove which one the burst path takes.
    """
    balancer = LoadBalancer(cast(Any, None), clock=clock)
    record_error, mark_rate_limit = AsyncMock(), AsyncMock()
    balancer.record_error = record_error  # type: ignore[method-assign]
    balancer.mark_rate_limit = mark_rate_limit  # type: ignore[method-assign]
    return SimpleNamespace(_load_balancer=balancer), balancer, record_error, mark_rate_limit


def test_burst_rejection_locked_clamps_retry_after_and_never_shortens() -> None:
    runtime = RuntimeState()
    assert record_burst_rejection_locked(runtime, 100.0, retry_after_seconds=None) == BURST_BACKOFF_DEFAULT_SECONDS
    assert runtime.burst_backoff_until == pytest.approx(100.0 + BURST_BACKOFF_DEFAULT_SECONDS)
    # Retry-After inside the bounds is honored as-is.
    assert record_burst_rejection_locked(runtime, 100.0, retry_after_seconds=12) == 12.0
    assert runtime.burst_backoff_until == pytest.approx(112.0)
    # A shorter follow-up rejection extends, never shortens.
    assert record_burst_rejection_locked(runtime, 101.0, retry_after_seconds=1) == BURST_BACKOFF_DEFAULT_SECONDS
    assert runtime.burst_backoff_until == pytest.approx(112.0)
    # A huge Retry-After is capped.
    assert record_burst_rejection_locked(runtime, 101.0, retry_after_seconds=900) == BURST_BACKOFF_MAX_SECONDS
    assert runtime.burst_backoff_until == pytest.approx(101.0 + BURST_BACKOFF_MAX_SECONDS)
    # The overload window is a separate mechanism and is never written here.
    assert runtime.overload_backoff_until is None
    assert runtime.overload_rejections is None
    assert runtime.overload_backoff_level == 0
    assert runtime.cooldown_until is None


def test_burst_deadline_activates_backoff_but_not_isolation() -> None:
    now = 1000.0
    runtime = RuntimeState(burst_backoff_until=now + 5.0)
    assert overload_backoff_active(runtime, now)
    assert overload_backoff_active(runtime, now + 4.9)
    assert not overload_backoff_active(runtime, now + 5.0)
    assert not overload_isolation_active(runtime, now)
    # The soft-owner reroute pool keys on isolation only: a burst keeps the owner.
    states = [_state("hot"), _state("clean")]
    assert sticky_owner_isolation_reroute_pool(states, {"hot": runtime}, owner_account_id="hot", now=now) is None
    # The candidate filter inherits the burst deadline.
    kept = filter_overload_backoff_candidates(states, {"hot": runtime}, now=now)
    assert [state.account_id for state in kept] == ["clean"]
    assert overload_backoff_active(None, now) is False


@pytest.mark.asyncio
async def test_handle_stream_error_codeless_429_engages_burst_cooldown_only(caplog: pytest.LogCaptureFixture) -> None:
    clock = VirtualClock(epoch_value=2_000_000_000.0)
    proxy, balancer, record_error, mark_rate_limit = _burst_proxy(clock)
    account = _make_account("acc-burst")

    with caplog.at_level(logging.WARNING, logger=_BURST_LOGGER):
        classified = await streaming_helpers_module._handle_stream_error(
            proxy,
            account,
            {"message": "Rate limit exceeded"},
            "upstream_error",
            429,
        )

    assert classified["failure_class"] == "retryable_transient"
    runtime = balancer._runtime[account.id]
    assert runtime.burst_backoff_until == pytest.approx(clock.time() + BURST_BACKOFF_DEFAULT_SECONDS)
    assert overload_backoff_active(runtime, clock.time())
    # Only the burst deadline is written: no overload window, no rate-limit cooldown, no status flip.
    assert runtime.overload_backoff_until is None
    assert runtime.overload_rejections is None
    assert runtime.overload_isolated_until is None
    assert runtime.cooldown_until is None
    assert account.status == AccountStatus.ACTIVE
    record_error.assert_awaited_once_with(account)
    mark_rate_limit.assert_not_awaited()
    assert (
        "Account burst backoff engaged account_id=acc-burst backoff_seconds=5.0 "
        "retry_after_seconds=None http_status=429"
    ) in caplog.text


@pytest.mark.asyncio
async def test_handle_stream_error_skips_burst_cooldown_when_already_recorded_at_rejection() -> None:
    """A keyed stream engages the cooldown at rejection time and defers only the penalty.

    The deferred write runs after usage settlement -- possibly after the stream
    succeeded -- so it must record the transient error without re-stamping the
    burst deadline from the later clock.
    """
    clock = VirtualClock(epoch_value=2_000_000_000.0)
    proxy, balancer, record_error, mark_rate_limit = _burst_proxy(clock)
    account = _make_account("acc-burst-deferred")
    rejected_at = clock.time()
    await record_upstream_burst_rejection(balancer, account, retry_after_seconds=None)
    clock.advance(40.0)

    classified = await streaming_helpers_module._handle_stream_error(
        proxy,
        account,
        {"message": "Rate limit exceeded"},
        "upstream_error",
        429,
        retry_after_seconds=None,
        burst_cooldown_recorded=True,
    )

    assert classified["failure_class"] == "retryable_transient"
    runtime = balancer._runtime[account.id]
    assert runtime.burst_backoff_until == pytest.approx(rejected_at + BURST_BACKOFF_DEFAULT_SECONDS)
    assert not overload_backoff_active(runtime, clock.time())
    record_error.assert_awaited_once_with(account)
    mark_rate_limit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("retry_after_seconds", "expected_seconds"),
    [(12, 12.0), (900, BURST_BACKOFF_MAX_SECONDS), (1, BURST_BACKOFF_DEFAULT_SECONDS)],
)
async def test_handle_stream_error_burst_cooldown_honors_clamped_retry_after(
    retry_after_seconds: int, expected_seconds: float, caplog: pytest.LogCaptureFixture
) -> None:
    clock = VirtualClock(epoch_value=2_000_000_000.0)
    proxy, balancer, record_error, mark_rate_limit = _burst_proxy(clock)
    account = _make_account("acc-burst-ra")

    with caplog.at_level(logging.WARNING, logger=_BURST_LOGGER):
        await streaming_helpers_module._handle_stream_error(
            proxy,
            account,
            {"message": "Rate limit exceeded"},
            "upstream_error",
            429,
            retry_after_seconds=retry_after_seconds,
        )

    assert balancer._runtime[account.id].burst_backoff_until == pytest.approx(clock.time() + expected_seconds)
    assert f"backoff_seconds={expected_seconds:.1f} retry_after_seconds={retry_after_seconds}" in caplog.text


@pytest.mark.asyncio
async def test_handle_stream_error_redacts_burst_log_under_privacy_policy(caplog: pytest.LogCaptureFixture) -> None:
    clock = VirtualClock(epoch_value=2_000_000_000.0)
    proxy, balancer, record_error, mark_rate_limit = _burst_proxy(clock)
    account = _make_account("acc-burst-private")

    with caplog.at_level(logging.WARNING, logger=_BURST_LOGGER):
        await streaming_helpers_module._handle_stream_error(
            proxy,
            account,
            {"message": "Rate limit exceeded"},
            "server_error",
            429,
            privacy_policy=streaming_helpers_module.CodexControlRequestPrivacyPolicy.PRIVATE_REALTIME,
        )

    # Keyed on the HTTP status, so a ``server_error`` rewrite still engages.
    deadline = balancer._runtime[account.id].burst_backoff_until
    assert deadline == pytest.approx(clock.time() + BURST_BACKOFF_DEFAULT_SECONDS)
    assert "Account burst backoff engaged account_id=<redacted>" in caplog.text
    assert "acc-burst-private" not in caplog.text


@pytest.mark.asyncio
async def test_handle_stream_error_coded_429_keeps_mark_rate_limit_and_no_burst_cooldown() -> None:
    clock = VirtualClock(epoch_value=2_000_000_000.0)
    proxy, balancer, record_error, mark_rate_limit = _burst_proxy(clock)
    account = _make_account("acc-coded-429")

    classified = await streaming_helpers_module._handle_stream_error(
        proxy,
        account,
        {"message": "Try again in 1.5s"},
        "rate_limit_exceeded",
        429,
        retry_after_seconds=12,
    )

    assert classified["failure_class"] == "rate_limit"
    mark_rate_limit.assert_awaited_once()
    record_error.assert_not_awaited()
    assert balancer._runtime.get(account.id) is None or balancer._runtime[account.id].burst_backoff_until is None


@pytest.mark.asyncio
async def test_handle_stream_error_non_429_upstream_error_records_error_without_burst_cooldown() -> None:
    clock = VirtualClock(epoch_value=2_000_000_000.0)
    proxy, balancer, record_error, mark_rate_limit = _burst_proxy(clock)
    account = _make_account("acc-400")

    await streaming_helpers_module._handle_stream_error(
        proxy,
        account,
        {"message": "something else went wrong"},
        "upstream_error",
        400,
        retry_after_seconds=12,
    )

    record_error.assert_awaited_once_with(account)
    mark_rate_limit.assert_not_awaited()
    assert balancer._runtime.get(account.id) is None or balancer._runtime[account.id].burst_backoff_until is None


@pytest.mark.asyncio
async def test_record_upstream_burst_rejection_ignores_balancers_without_a_runtime_map() -> None:
    balancer = SimpleNamespace(record_error=AsyncMock())
    await record_upstream_burst_rejection(balancer, _make_account("acc-double-burst"), retry_after_seconds=3)


@pytest.mark.asyncio
async def test_select_account_skips_bursting_account_while_a_healthy_sibling_exists() -> None:
    clock = VirtualClock(epoch_value=2_000_000_000.0)
    hot = _make_account("acc-burst-hot")
    clean = _make_account("acc-burst-clean")
    balancer = LoadBalancer(
        lambda: _repo_factory(_StubAccountsRepository([hot, clean]), _StubUsageRepository({}, {})),
        clock=clock,
    )
    await record_upstream_burst_rejection(balancer, hot)

    # Equal weights: 40 draws all landing on ``clean`` is 2**-40 by chance.
    for _ in range(40):
        result = await balancer.select_account(routing_strategy="capacity_weighted")
        assert result.account is not None
        assert result.account.id == clean.id

    clock.advance(BURST_BACKOFF_DEFAULT_SECONDS + 0.1)
    selected: set[str] = set()
    for _ in range(40):
        result = await balancer.select_account(routing_strategy="capacity_weighted")
        assert result.account is not None
        selected.add(result.account.id)
    assert hot.id in selected


@pytest.mark.asyncio
async def test_select_account_still_uses_bursting_account_when_it_is_the_only_candidate() -> None:
    clock = VirtualClock(epoch_value=2_000_000_000.0)
    hot = _make_account("acc-burst-alone")
    balancer = LoadBalancer(
        lambda: _repo_factory(_StubAccountsRepository([hot]), _StubUsageRepository({}, {})),
        clock=clock,
    )
    await record_upstream_burst_rejection(balancer, hot, retry_after_seconds=30)

    result = await balancer.select_account(lease_kind="stream", routing_strategy="capacity_weighted")

    assert result.error_code is None, result.error_message
    assert result.account is not None
    assert result.account.id == hot.id


@pytest.mark.asyncio
async def test_fresh_sticky_binding_avoids_bursting_account_but_established_owner_is_kept() -> None:
    clock = VirtualClock(epoch_value=2_000_000_000.0)
    balancer = LoadBalancer(_mock_repo_factory, clock=clock)
    balancer._runtime["hot"] = RuntimeState(burst_backoff_until=clock.time() + BURST_BACKOFF_DEFAULT_SECONDS)
    states = [_state("hot"), _state("clean")]

    # A previously unseen key is a fresh upstream admission: bind away from the bursting account.
    for _ in range(40):
        fresh = await _select_sticky(balancer, [_state("hot"), _state("clean")], _sticky_repo(None))
        assert fresh.account is not None
        assert fresh.account.account_id == "clean"

    # An established owner is warm-session reuse: a burst never moves it.
    owned = await _select_sticky(balancer, states, _sticky_repo("hot"))
    assert owned.account is not None
    assert owned.account.account_id == "hot"

    # With no alternative the fresh binding still lands on the bursting account.
    alone = await _select_sticky(balancer, [_state("hot")], _sticky_repo(None))
    assert alone.account is not None
    assert alone.account.account_id == "hot"

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
from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, StickySessionKind
from app.modules.proxy._load_balancer.overload_backoff import (
    OVERLOAD_BACKOFF_BASE_SECONDS,
    OVERLOAD_BACKOFF_MAX_SECONDS,
    OVERLOAD_LEVEL_DECAY_SECONDS,
    OVERLOAD_MAX_LEVEL,
    OVERLOAD_TRIP_COUNT,
    OVERLOAD_WINDOW_SECONDS,
    filter_overload_backoff_candidates,
    overload_backoff_active,
    overload_backoff_seconds,
    record_overload_rejection_locked,
    record_upstream_overload,
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

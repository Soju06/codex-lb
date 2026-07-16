from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock

import pytest

import app.modules.accounts.usage_rollup_scheduler as rollup_scheduler
from app.modules.accounts.usage_rollup_scheduler import AccountUsageRollupScheduler

pytestmark = pytest.mark.unit


class _GateLeader:
    """Leader stub mirroring ``run_if_leader``: heartbeat gate, not one-shot.

    A one-time ``try_acquire`` would leave a fold backfill that outlives the
    60s lease unprotected; ``try_acquire`` therefore asserts if the scheduler
    ever falls back to it.
    """

    def __init__(self, *, leader: bool) -> None:
        self.leader = leader
        self.run_if_leader_calls = 0

    async def run_if_leader(self, fn: Callable[[], Awaitable[object]]) -> object | None:
        self.run_if_leader_calls += 1
        if not self.leader:
            return None
        return await fn()

    async def try_acquire(self) -> bool:  # pragma: no cover - must not be used
        raise AssertionError("usage rollup scheduler must gate via run_if_leader, not try_acquire")


def test_build_account_usage_rollup_scheduler_uses_constant_interval() -> None:
    scheduler = rollup_scheduler.build_account_usage_rollup_scheduler()
    assert scheduler.interval_seconds == rollup_scheduler.FOLD_INTERVAL_SECONDS


@pytest.mark.asyncio
async def test_fold_once_skips_when_not_leader(monkeypatch) -> None:
    leader = _GateLeader(leader=False)
    monkeypatch.setattr(rollup_scheduler, "_get_leader_election", lambda: leader)
    fold = AsyncMock()
    monkeypatch.setattr(rollup_scheduler, "run_fold_pass", fold)

    await AccountUsageRollupScheduler(interval_seconds=1)._fold_once()

    fold.assert_not_called()
    assert leader.run_if_leader_calls == 1


@pytest.mark.asyncio
async def test_fold_once_gates_via_run_if_leader_heartbeat(monkeypatch) -> None:
    """The fold pass must run under the heartbeat-renewed ``run_if_leader``
    gate, not a one-time ``try_acquire`` that leaves a long backfill
    unprotected once the lease expires mid-pass."""
    leader = _GateLeader(leader=True)
    monkeypatch.setattr(rollup_scheduler, "_get_leader_election", lambda: leader)
    fold = AsyncMock(return_value=2)
    monkeypatch.setattr(rollup_scheduler, "run_fold_pass", fold)

    await AccountUsageRollupScheduler(interval_seconds=1)._fold_once()

    fold.assert_awaited_once()
    assert leader.run_if_leader_calls == 1


@pytest.mark.asyncio
async def test_fold_once_swallows_fold_errors(monkeypatch) -> None:
    leader = _GateLeader(leader=True)
    monkeypatch.setattr(rollup_scheduler, "_get_leader_election", lambda: leader)
    fold = AsyncMock(side_effect=RuntimeError("db down"))
    monkeypatch.setattr(rollup_scheduler, "run_fold_pass", fold)

    await AccountUsageRollupScheduler(interval_seconds=1)._fold_once()

    fold.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_folds_immediately_and_stop_cancels(monkeypatch) -> None:
    leader = _GateLeader(leader=True)
    monkeypatch.setattr(rollup_scheduler, "_get_leader_election", lambda: leader)
    folded = asyncio.Event()

    async def _fold(**_kwargs):
        folded.set()
        return 0

    monkeypatch.setattr(rollup_scheduler, "run_fold_pass", _fold)

    scheduler = AccountUsageRollupScheduler(interval_seconds=3600)
    await scheduler.start()
    await asyncio.wait_for(folded.wait(), timeout=5)
    await scheduler.stop()
    assert scheduler._task is None

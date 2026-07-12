from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.modules.accounts.usage_rollup_scheduler as rollup_scheduler
from app.modules.accounts.usage_rollup_scheduler import AccountUsageRollupScheduler

pytestmark = pytest.mark.unit


def test_build_account_usage_rollup_scheduler_uses_constant_interval() -> None:
    scheduler = rollup_scheduler.build_account_usage_rollup_scheduler()
    assert scheduler.interval_seconds == rollup_scheduler.FOLD_INTERVAL_SECONDS


@pytest.mark.asyncio
async def test_fold_once_skips_when_not_leader(monkeypatch) -> None:
    leader = SimpleNamespace(try_acquire=AsyncMock(return_value=False))
    monkeypatch.setattr(rollup_scheduler, "_get_leader_election", lambda: leader)
    fold = AsyncMock()
    monkeypatch.setattr(rollup_scheduler, "run_fold_pass", fold)

    await AccountUsageRollupScheduler(interval_seconds=1)._fold_once()

    fold.assert_not_called()


@pytest.mark.asyncio
async def test_fold_once_runs_fold_as_leader(monkeypatch) -> None:
    leader = SimpleNamespace(try_acquire=AsyncMock(return_value=True))
    monkeypatch.setattr(rollup_scheduler, "_get_leader_election", lambda: leader)
    fold = AsyncMock(return_value=2)
    monkeypatch.setattr(rollup_scheduler, "run_fold_pass", fold)

    await AccountUsageRollupScheduler(interval_seconds=1)._fold_once()

    fold.assert_awaited_once()


@pytest.mark.asyncio
async def test_fold_once_swallows_fold_errors(monkeypatch) -> None:
    leader = SimpleNamespace(try_acquire=AsyncMock(return_value=True))
    monkeypatch.setattr(rollup_scheduler, "_get_leader_election", lambda: leader)
    fold = AsyncMock(side_effect=RuntimeError("db down"))
    monkeypatch.setattr(rollup_scheduler, "run_fold_pass", fold)

    await AccountUsageRollupScheduler(interval_seconds=1)._fold_once()

    fold.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_folds_immediately_and_stop_cancels(monkeypatch) -> None:
    leader = SimpleNamespace(try_acquire=AsyncMock(return_value=True))
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

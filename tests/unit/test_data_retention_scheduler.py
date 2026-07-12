from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.core.retention.scheduler as retention_scheduler
from app.core.retention.scheduler import DataRetentionScheduler

pytestmark = pytest.mark.unit


def test_build_data_retention_scheduler_disabled_by_default(monkeypatch) -> None:
    settings = SimpleNamespace(request_log_retention_days=0, usage_history_retention_days=0)
    monkeypatch.setattr(retention_scheduler, "get_settings", lambda: settings)

    scheduler = retention_scheduler.build_data_retention_scheduler()

    assert scheduler.enabled is False
    assert scheduler.interval_seconds == retention_scheduler.RETENTION_INTERVAL_SECONDS


def test_build_data_retention_scheduler_enabled_when_any_retention_set(monkeypatch) -> None:
    settings = SimpleNamespace(request_log_retention_days=0, usage_history_retention_days=45)
    monkeypatch.setattr(retention_scheduler, "get_settings", lambda: settings)

    assert retention_scheduler.build_data_retention_scheduler().enabled is True


@pytest.mark.asyncio
async def test_prune_once_skips_when_not_leader(monkeypatch) -> None:
    leader = SimpleNamespace(try_acquire=AsyncMock(return_value=False))
    monkeypatch.setattr(retention_scheduler, "_get_leader_election", lambda: leader)
    prune = AsyncMock()
    monkeypatch.setattr(retention_scheduler, "run_retention_pass", prune)

    await DataRetentionScheduler(interval_seconds=1, enabled=True)._prune_once()

    prune.assert_not_called()


@pytest.mark.asyncio
async def test_prune_once_runs_and_swallows_errors(monkeypatch) -> None:
    leader = SimpleNamespace(try_acquire=AsyncMock(return_value=True))
    monkeypatch.setattr(retention_scheduler, "_get_leader_election", lambda: leader)
    prune = AsyncMock(side_effect=RuntimeError("db down"))
    monkeypatch.setattr(retention_scheduler, "run_retention_pass", prune)

    await DataRetentionScheduler(interval_seconds=1, enabled=True)._prune_once()

    prune.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_returns_immediately_when_disabled() -> None:
    scheduler = DataRetentionScheduler(interval_seconds=1, enabled=False)
    await scheduler.start()
    assert scheduler._task is None

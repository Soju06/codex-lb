from __future__ import annotations

from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.core.retention.scheduler as retention_scheduler
from app.core.retention.scheduler import DataRetentionScheduler

pytestmark = pytest.mark.unit


class _GateLeader:
    """Leader stub mirroring ``run_if_leader``: heartbeat gate, not one-shot.

    Records that the scheduler funnels through ``run_if_leader`` (the
    heartbeat-renewed gate) rather than a one-time ``try_acquire`` that would
    leave a long pass unprotected once the lease expires mid-pass.
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
        raise AssertionError("retention scheduler must gate via run_if_leader, not try_acquire")


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
    leader = _GateLeader(leader=False)
    monkeypatch.setattr(retention_scheduler, "_get_leader_election", lambda: leader)
    prune = AsyncMock()
    monkeypatch.setattr(retention_scheduler, "run_retention_pass", prune)

    await DataRetentionScheduler(interval_seconds=1, enabled=True)._prune_once()

    prune.assert_not_called()
    assert leader.run_if_leader_calls == 1


@pytest.mark.asyncio
async def test_prune_once_gates_via_run_if_leader_heartbeat(monkeypatch) -> None:
    """The pass must run under the heartbeat-renewed ``run_if_leader`` gate.

    A one-time ``try_acquire`` would leave a retention pass that outlives the
    60s lease unprotected; ``_GateLeader.try_acquire`` therefore asserts if the
    scheduler ever falls back to it.
    """
    leader = _GateLeader(leader=True)
    monkeypatch.setattr(retention_scheduler, "_get_leader_election", lambda: leader)
    prune = AsyncMock(side_effect=RuntimeError("db down"))
    monkeypatch.setattr(retention_scheduler, "run_retention_pass", prune)

    await DataRetentionScheduler(interval_seconds=1, enabled=True)._prune_once()

    prune.assert_awaited_once()
    assert leader.run_if_leader_calls == 1


@pytest.mark.asyncio
async def test_start_returns_immediately_when_disabled() -> None:
    scheduler = DataRetentionScheduler(interval_seconds=1, enabled=False)
    await scheduler.start()
    assert scheduler._task is None

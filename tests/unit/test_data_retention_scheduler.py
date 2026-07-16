from __future__ import annotations

from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock

import pytest

import app.core.retention.scheduler as retention_scheduler
from app.core.retention.job import EffectiveRetention
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


def _set_effective_retention(monkeypatch, *, request_log: int = 0, usage_history: int = 0) -> None:
    async def _resolve() -> EffectiveRetention:
        return EffectiveRetention(request_log_days=request_log, usage_history_days=usage_history)

    monkeypatch.setattr(retention_scheduler, "get_effective_retention", _resolve)


def test_build_data_retention_scheduler_uses_hourly_interval() -> None:
    scheduler = retention_scheduler.build_data_retention_scheduler()
    assert scheduler.interval_seconds == retention_scheduler.RETENTION_INTERVAL_SECONDS


@pytest.mark.asyncio
async def test_prune_once_skips_leader_election_when_retention_disabled(monkeypatch) -> None:
    """A disabled tick returns before leader election: retention is a runtime
    setting, so the always-on tick must stay cheap while it is off."""
    leader = _GateLeader(leader=True)
    monkeypatch.setattr(retention_scheduler, "_get_leader_election", lambda: leader)
    _set_effective_retention(monkeypatch, request_log=0, usage_history=0)
    prune = AsyncMock()
    monkeypatch.setattr(retention_scheduler, "run_retention_pass", prune)

    await DataRetentionScheduler(interval_seconds=1)._prune_once()

    prune.assert_not_called()
    assert leader.run_if_leader_calls == 0


@pytest.mark.asyncio
async def test_prune_once_runs_when_any_effective_retention_set(monkeypatch) -> None:
    """Each tick re-resolves the effective retention, so a dashboard change
    enables pruning without a restart."""
    leader = _GateLeader(leader=True)
    monkeypatch.setattr(retention_scheduler, "_get_leader_election", lambda: leader)
    _set_effective_retention(monkeypatch, usage_history=45)
    prune = AsyncMock()
    monkeypatch.setattr(retention_scheduler, "run_retention_pass", prune)

    await DataRetentionScheduler(interval_seconds=1)._prune_once()

    prune.assert_awaited_once()
    assert leader.run_if_leader_calls == 1


@pytest.mark.asyncio
async def test_prune_once_skips_when_not_leader(monkeypatch) -> None:
    leader = _GateLeader(leader=False)
    monkeypatch.setattr(retention_scheduler, "_get_leader_election", lambda: leader)
    _set_effective_retention(monkeypatch, request_log=30)
    prune = AsyncMock()
    monkeypatch.setattr(retention_scheduler, "run_retention_pass", prune)

    await DataRetentionScheduler(interval_seconds=1)._prune_once()

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
    _set_effective_retention(monkeypatch, request_log=30)
    prune = AsyncMock(side_effect=RuntimeError("db down"))
    monkeypatch.setattr(retention_scheduler, "run_retention_pass", prune)

    await DataRetentionScheduler(interval_seconds=1)._prune_once()

    prune.assert_awaited_once()
    assert leader.run_if_leader_calls == 1


@pytest.mark.asyncio
async def test_prune_once_survives_settings_resolution_failure(monkeypatch) -> None:
    """A settings read failure (e.g. DB blip) must not kill the tick loop."""
    leader = _GateLeader(leader=True)
    monkeypatch.setattr(retention_scheduler, "_get_leader_election", lambda: leader)

    async def _boom() -> EffectiveRetention:
        raise RuntimeError("db down")

    monkeypatch.setattr(retention_scheduler, "get_effective_retention", _boom)
    prune = AsyncMock()
    monkeypatch.setattr(retention_scheduler, "run_retention_pass", prune)

    await DataRetentionScheduler(interval_seconds=1)._prune_once()

    prune.assert_not_called()
    assert leader.run_if_leader_calls == 0


def test_effective_retention_enabled_property() -> None:
    assert EffectiveRetention(request_log_days=0, usage_history_days=0).enabled is False
    assert EffectiveRetention(request_log_days=30, usage_history_days=0).enabled is True
    assert EffectiveRetention(request_log_days=0, usage_history_days=45).enabled is True

from __future__ import annotations

from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock

import pytest

import app.core.retention.job as retention_job
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

    monkeypatch.setattr(retention_job, "get_effective_retention", _resolve)


def test_build_data_retention_scheduler_uses_hourly_interval() -> None:
    scheduler = retention_scheduler.build_data_retention_scheduler()
    assert scheduler.interval_seconds == retention_scheduler.RETENTION_INTERVAL_SECONDS


@pytest.mark.asyncio
async def test_prune_once_runs_the_pin_purge_when_retention_is_disabled(monkeypatch) -> None:
    """The tick runs the leader-gated pass even with both retention windows off.

    Model-source pins carry their own ``purge_at`` (design §8.8), so their
    purge is not opt-in; the request-log and usage-history pruning inside the
    pass stay gated by their windows.
    """
    leader = _GateLeader(leader=True)
    monkeypatch.setattr(retention_scheduler, "_get_leader_election", lambda: leader)
    _set_effective_retention(monkeypatch, request_log=0, usage_history=0)
    prune_pins = AsyncMock(return_value=3)
    prune_request_logs = AsyncMock(return_value=0)
    prune_usage_history = AsyncMock(return_value=0)
    prune_additional = AsyncMock(return_value=0)
    monkeypatch.setattr(retention_job, "prune_model_source_pins", prune_pins)
    monkeypatch.setattr(retention_job, "_prune_request_logs", prune_request_logs)
    monkeypatch.setattr(retention_job, "_prune_usage_history", prune_usage_history)
    monkeypatch.setattr(retention_job, "_prune_additional_usage_history", prune_additional)

    await DataRetentionScheduler(interval_seconds=1)._prune_once()

    assert leader.run_if_leader_calls == 1
    prune_pins.assert_awaited_once()
    prune_request_logs.assert_not_called()
    prune_usage_history.assert_not_called()
    prune_additional.assert_not_called()


@pytest.mark.asyncio
async def test_prune_once_runs_when_any_effective_retention_set(monkeypatch) -> None:
    """Each tick re-resolves the effective retention inside the pass, so a
    dashboard change enables pruning without a restart."""
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
    prune = AsyncMock(side_effect=RuntimeError("db down"))
    monkeypatch.setattr(retention_scheduler, "run_retention_pass", prune)

    await DataRetentionScheduler(interval_seconds=1)._prune_once()

    prune.assert_awaited_once()
    assert leader.run_if_leader_calls == 1


@pytest.mark.asyncio
async def test_prune_once_survives_a_failing_pass_on_consecutive_ticks(monkeypatch) -> None:
    """A settings read or database blip inside the pass must not kill the tick loop."""
    leader = _GateLeader(leader=True)
    monkeypatch.setattr(retention_scheduler, "_get_leader_election", lambda: leader)

    async def _boom() -> EffectiveRetention:
        raise RuntimeError("db down")

    monkeypatch.setattr(retention_job, "get_effective_retention", _boom)
    prune_pins = AsyncMock(return_value=0)
    monkeypatch.setattr(retention_job, "prune_model_source_pins", prune_pins)

    scheduler = DataRetentionScheduler(interval_seconds=1)
    await scheduler._prune_once()
    await scheduler._prune_once()

    assert leader.run_if_leader_calls == 2
    prune_pins.assert_not_called()  # the pass failed before reaching the purge, and the loop is still alive


def test_scheduler_does_not_gate_the_tick_on_the_retention_windows() -> None:
    """Mutant guard: re-introducing an ``enabled`` short-circuit would skip the pin purge."""
    assert not hasattr(retention_scheduler, "get_effective_retention")


def test_effective_retention_enabled_property() -> None:
    assert EffectiveRetention(request_log_days=0, usage_history_days=0).enabled is False
    assert EffectiveRetention(request_log_days=30, usage_history_days=0).enabled is True
    assert EffectiveRetention(request_log_days=0, usage_history_days=45).enabled is True

"""Automations scheduler ticks follow the dashboard pause toggle (M2 background jobs)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any, cast

import pytest

import app.modules.automations.scheduler as scheduler_module
from app.modules.automations.scheduler import AutomationsScheduler, build_automations_scheduler

pytestmark = pytest.mark.unit


class _Leader:
    async def run_if_leader(self, fn: Callable[[], Awaitable[object]]) -> object:
        return await fn()


def _snapshot(enabled: bool | None) -> SimpleNamespace:
    return SimpleNamespace(automations_scheduler_enabled=enabled)


def _scheduler(
    monkeypatch: pytest.MonkeyPatch, dashboard_snapshot: Callable[[], Awaitable[Any]]
) -> tuple[AutomationsScheduler, list[SimpleNamespace]]:
    ticks: list[SimpleNamespace] = []

    async def _counting_body(self: AutomationsScheduler, dashboard_settings: Any) -> None:
        ticks.append(cast(SimpleNamespace, dashboard_settings))

    monkeypatch.setattr(scheduler_module, "_get_leader_election", lambda: _Leader())
    monkeypatch.setattr(AutomationsScheduler, "_run_due_as_leader", _counting_body)
    return AutomationsScheduler(interval_seconds=30, enabled=True, dashboard_snapshot=dashboard_snapshot), ticks


@pytest.mark.asyncio
async def test_ticks_follow_the_dashboard_toggle_without_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    """Booted enabled -> dashboard off -> next tick skipped -> dashboard on -> next tick runs."""
    toggle: dict[str, bool | None] = {"enabled": None}  # None = inherit the env alias / default (True)

    async def dashboard_snapshot() -> SimpleNamespace:
        return _snapshot(toggle["enabled"])

    scheduler, ticks = _scheduler(monkeypatch, dashboard_snapshot)

    await scheduler._run_due_once()
    assert len(ticks) == 1

    toggle["enabled"] = False
    await scheduler._run_due_once()
    assert len(ticks) == 1

    toggle["enabled"] = True
    await scheduler._run_due_once()
    assert len(ticks) == 2


@pytest.mark.asyncio
async def test_tick_hands_its_snapshot_to_the_leader_gated_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """One settings read per tick, taken before the lock and threaded into the locked body."""
    reads = 0

    async def dashboard_snapshot() -> SimpleNamespace:
        nonlocal reads
        reads += 1
        return _snapshot(True)

    scheduler, ticks = _scheduler(monkeypatch, dashboard_snapshot)

    await scheduler._run_due_once()

    assert reads == 1
    assert [snapshot.automations_scheduler_enabled for snapshot in ticks] == [True]


@pytest.mark.asyncio
async def test_paused_tick_does_not_consult_leader_election(monkeypatch: pytest.MonkeyPatch) -> None:
    async def paused() -> SimpleNamespace:
        return _snapshot(False)

    scheduler, ticks = _scheduler(monkeypatch, paused)

    def _unexpected_election():
        raise AssertionError("a paused tick must not touch leader election")

    monkeypatch.setattr(scheduler_module, "_get_leader_election", _unexpected_election)

    await scheduler._run_due_once()

    assert ticks == []


def test_build_scheduler_always_starts_and_reads_the_dashboard_toggle_per_tick() -> None:
    """The env alias no longer decides whether the loop exists; each tick reads the effective toggle."""
    scheduler = build_automations_scheduler()

    assert scheduler.enabled is True
    assert scheduler.interval_seconds == scheduler_module._INTERVAL_SECONDS
    assert scheduler.dashboard_snapshot is scheduler_module._dashboard_settings_snapshot


@pytest.mark.asyncio
async def test_run_loop_survives_a_transient_toggle_read_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A flaky settings read must not kill the loop task (a dead task also aborts shutdown)."""
    reads = 0
    scheduler: AutomationsScheduler

    async def flaky_snapshot() -> SimpleNamespace:
        nonlocal reads
        reads += 1
        if reads == 1:
            raise RuntimeError("database is briefly unavailable")
        scheduler._stop.set()
        return _snapshot(True)

    scheduler, ticks = _scheduler(monkeypatch, flaky_snapshot)
    scheduler.interval_seconds = 0

    with caplog.at_level("ERROR"):
        await scheduler._run_loop()

    assert reads == 2
    assert len(ticks) == 1
    assert "Automations scheduler tick failed" in caplog.text

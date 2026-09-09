"""Automations scheduler ticks follow the dashboard pause toggle (M2 background jobs)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

import app.modules.automations.scheduler as scheduler_module
from app.modules.automations.scheduler import AutomationsScheduler, build_automations_scheduler

pytestmark = pytest.mark.unit


class _Leader:
    async def run_if_leader(self, fn: Callable[[], Awaitable[object]]) -> object:
        return await fn()


def _scheduler(
    monkeypatch: pytest.MonkeyPatch, dashboard_enabled: Callable[[], Awaitable[bool]]
) -> tuple[AutomationsScheduler, list[int]]:
    ticks: list[int] = []

    async def _counting_body(self: AutomationsScheduler) -> None:
        ticks.append(len(ticks) + 1)

    monkeypatch.setattr(scheduler_module, "_get_leader_election", lambda: _Leader())
    monkeypatch.setattr(AutomationsScheduler, "_run_due_as_leader", _counting_body)
    return AutomationsScheduler(interval_seconds=30, enabled=True, dashboard_enabled=dashboard_enabled), ticks


@pytest.mark.asyncio
async def test_ticks_follow_the_dashboard_toggle_without_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    """Booted enabled -> dashboard off -> next tick skipped -> dashboard on -> next tick runs."""
    toggle = {"enabled": True}

    async def dashboard_enabled() -> bool:
        return toggle["enabled"]

    scheduler, ticks = _scheduler(monkeypatch, dashboard_enabled)

    await scheduler._run_due_once()
    assert ticks == [1]

    toggle["enabled"] = False
    await scheduler._run_due_once()
    assert ticks == [1]

    toggle["enabled"] = True
    await scheduler._run_due_once()
    assert ticks == [1, 2]


@pytest.mark.asyncio
async def test_paused_tick_does_not_consult_leader_election(monkeypatch: pytest.MonkeyPatch) -> None:
    async def paused() -> bool:
        return False

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
    assert scheduler.dashboard_enabled is scheduler_module._dashboard_automations_enabled

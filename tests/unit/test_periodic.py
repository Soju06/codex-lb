from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

import pytest

from app.core.utils.periodic import PeriodicPhaseEvent, SupervisedPeriodicPhase

pytestmark = pytest.mark.unit


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0)


def _runner(
    operation,
    events: list[PeriodicPhaseEvent],
    supervisor_exits: list[tuple[str, str, float]],
    *,
    interval: float = 0.01,
    deadline: float = 0.05,
) -> SupervisedPeriodicPhase:
    return SupervisedPeriodicPhase(
        phase="test_phase",
        operation=operation,
        interval_seconds=interval,
        deadline_seconds=deadline,
        restart_delay_seconds=0.01,
        on_event=events.append,
        on_supervisor_exit=lambda phase, exc, delay: supervisor_exits.append((phase, type(exc).__name__, delay)),
    )


@pytest.mark.asyncio
async def test_periodic_phase_continues_after_failure_and_reports_recovery() -> None:
    events: list[PeriodicPhaseEvent] = []
    supervisor_exits: list[tuple[str, str, float]] = []
    calls = 0

    async def operation() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("boom")

    runner = _runner(operation, events, supervisor_exits)
    runner.start()
    try:
        await _wait_until(lambda: len(events) >= 2)
    finally:
        assert await runner.stop(timeout_seconds=1)

    assert [(event.outcome, event.consecutive_failures) for event in events[:2]] == [
        ("failure", 1),
        ("success", 1),
    ]
    assert events[0].error_type == "RuntimeError"
    assert supervisor_exits == []


@pytest.mark.asyncio
async def test_periodic_phase_timeout_keeps_one_owner_and_skips_catch_up_burst() -> None:
    events: list[PeriodicPhaseEvent] = []
    supervisor_exits: list[tuple[str, str, float]] = []
    release_first = asyncio.Event()
    starts: list[float] = []
    active = 0
    max_active = 0

    async def operation() -> None:
        nonlocal active, max_active
        starts.append(time.monotonic())
        active += 1
        max_active = max(max_active, active)
        try:
            if len(starts) == 1:
                await release_first.wait()
        finally:
            active -= 1

    runner = _runner(operation, events, supervisor_exits, interval=0.02, deadline=0.01)
    runner.start()
    try:
        await _wait_until(lambda: any(event.outcome == "timeout" for event in events))
        await asyncio.sleep(0.05)
        assert len(starts) == 1
        assert runner.phase_task is not None

        released_at = time.monotonic()
        release_first.set()
        await _wait_until(lambda: len(starts) >= 2)
    finally:
        assert await runner.stop(timeout_seconds=1)

    timeout_event = next(event for event in events if event.outcome == "timeout")
    late_success = next(event for event in events if event.outcome == "success" and event.late)
    assert timeout_event.consecutive_failures == 1
    assert late_success.consecutive_failures == 1
    assert starts[1] > released_at
    assert max_active == 1
    assert supervisor_exits == []


@pytest.mark.asyncio
async def test_periodic_phase_stop_cancels_and_drains_owned_child() -> None:
    events: list[PeriodicPhaseEvent] = []
    supervisor_exits: list[tuple[str, str, float]] = []
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def operation() -> None:
        started.set()
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    runner = _runner(operation, events, supervisor_exits)
    runner.start()
    await asyncio.wait_for(started.wait(), timeout=1)

    assert await runner.stop(timeout_seconds=1)
    assert cancelled.is_set()
    assert not runner.running
    assert supervisor_exits == []


@pytest.mark.asyncio
async def test_periodic_phase_retains_noncooperative_child_after_stop_deadline() -> None:
    events: list[PeriodicPhaseEvent] = []
    supervisor_exits: list[tuple[str, str, float]] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def operation() -> None:
        started.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                current_task = asyncio.current_task()
                assert current_task is not None
                current_task.uncancel()

    runner = _runner(operation, events, supervisor_exits)
    runner.start()
    await asyncio.wait_for(started.wait(), timeout=1)

    assert await runner.stop(timeout_seconds=0.01) is False
    owned = runner.phase_task
    assert owned is not None
    assert not owned.done()

    release.set()
    await asyncio.wait_for(owned, timeout=1)
    await _wait_until(lambda: not runner.running)


@pytest.mark.asyncio
async def test_periodic_phase_restarts_after_unexpected_worker_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[PeriodicPhaseEvent] = []
    supervisor_exits: list[tuple[str, str, float]] = []

    async def operation() -> None:
        return None

    runner = _runner(operation, events, supervisor_exits)
    calls = 0
    restarted = asyncio.Event()

    async def faulty_cycles() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("worker escaped")
        restarted.set()
        await asyncio.Future()

    monkeypatch.setattr(runner, "_run_cycles", faulty_cycles)
    runner.start()
    try:
        await asyncio.wait_for(restarted.wait(), timeout=1)
    finally:
        assert await runner.stop(timeout_seconds=1)

    assert calls == 2
    assert supervisor_exits == [("test_phase", "RuntimeError", 0.01)]


def test_periodic_phase_rejects_invalid_bounds() -> None:
    async def operation() -> None:
        return None

    def construct(
        *,
        interval_seconds: float = 1,
        deadline_seconds: float = 1,
        restart_delay_seconds: float = 0,
    ) -> SupervisedPeriodicPhase:
        return SupervisedPeriodicPhase(
            phase="test",
            operation=operation,
            interval_seconds=interval_seconds,
            deadline_seconds=deadline_seconds,
            restart_delay_seconds=restart_delay_seconds,
            on_event=lambda event: None,
            on_supervisor_exit=lambda phase, exc, delay: None,
        )

    with pytest.raises(ValueError, match="interval_seconds"):
        construct(interval_seconds=0)
    with pytest.raises(ValueError, match="deadline_seconds"):
        construct(deadline_seconds=0)
    with pytest.raises(ValueError, match="restart_delay_seconds"):
        construct(restart_delay_seconds=-1)

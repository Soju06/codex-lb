"""``stop_task_after_grace`` fallback path (#2505).

Kept as a focused unit test by exception: the process-level integration test
only reaches the happy path (every task finishes inside the grace), so the
cancel-and-warn fallback has no integration path that exercises it.
"""

from __future__ import annotations

import asyncio
import logging
import time

import pytest

from app.core import shutdown as shutdown_module
from app.core.scheduling import task_shutdown


@pytest.mark.asyncio
async def test_task_finishing_its_work_is_not_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_shutdown, "DATABASE_TASK_STOP_GRACE_SECONDS", 1.0)
    finished = asyncio.Event()

    async def one_unit_of_work() -> None:
        await asyncio.sleep(0.05)
        finished.set()

    task = asyncio.create_task(one_unit_of_work())
    await task_shutdown.stop_task_after_grace(task)

    assert finished.is_set()
    assert not task.cancelled()


@pytest.mark.asyncio
async def test_task_busy_past_grace_is_cancelled_and_named(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(task_shutdown, "DATABASE_TASK_STOP_GRACE_SECONDS", 0.05)
    caplog.set_level(logging.WARNING, logger=task_shutdown.__name__)

    async def wedged_database_call() -> None:
        await asyncio.Event().wait()

    task = asyncio.create_task(wedged_database_call())
    await task_shutdown.stop_task_after_grace(task)

    assert task.cancelled()
    (record,) = caplog.records
    assert record.levelno == logging.WARNING
    assert "wedged_database_call" in record.getMessage()


@pytest.mark.asyncio
async def test_cancelling_the_caller_propagates_and_leaves_the_task_running(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_shutdown, "DATABASE_TASK_STOP_GRACE_SECONDS", 5.0)
    release = asyncio.Event()

    async def in_flight_database_call() -> None:
        await release.wait()

    task = asyncio.create_task(in_flight_database_call())
    stopper = asyncio.create_task(task_shutdown.stop_task_after_grace(task))
    await asyncio.sleep(0.05)
    stopper.cancel()

    with pytest.raises(asyncio.CancelledError):
        await stopper
    assert not task.done()
    release.set()
    await task


@pytest.mark.asyncio
async def test_task_deferring_cancellation_is_tracked_not_awaited_forever(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(task_shutdown, "DATABASE_TASK_STOP_GRACE_SECONDS", 0.05)
    caplog.set_level(logging.WARNING, logger=task_shutdown.__name__)
    cleanup_may_finish = asyncio.Event()

    async def shielded_database_cleanup() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await cleanup_may_finish.wait()  # defers cancellation, like shielded session teardown
            raise

    task = asyncio.create_task(shielded_database_cleanup())
    await task_shutdown.stop_task_after_grace(task)  # returns despite the task still running

    assert not task.done()
    assert task in task_shutdown.undrained_tasks()
    messages = [record.getMessage() for record in caplog.records]
    assert any("still running" in m and "shielded_database_cleanup" in m for m in messages)

    cleanup_may_finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task not in task_shutdown.undrained_tasks()


def _shutdown_budget(monkeypatch: pytest.MonkeyPatch, usable_seconds: float) -> None:
    """Simulate a committed shutdown whose shared drain deadline leaves ``usable_seconds``,
    counting down in real time. (The post-drain cleanup reserve lies beyond it.)"""

    deadline = time.monotonic() + usable_seconds
    monkeypatch.setattr(
        shutdown_module,
        "remaining_drain_timeout_seconds",
        lambda: max(deadline - time.monotonic(), 0.0),
    )


@pytest.mark.asyncio
async def test_sequential_stops_of_wedged_tasks_stay_within_the_shutdown_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Seven wedged tasks (six schedulers + the lease keeper) would take 7 x (2s grace + 2s
    # post-cancel wait) = 28s uncapped; with 0.5s of budget left they must all be done in ~0.5s.
    _shutdown_budget(monkeypatch, usable_seconds=0.5)

    async def wedged_database_call() -> None:
        await asyncio.Event().wait()

    tasks = [asyncio.create_task(wedged_database_call()) for _ in range(7)]
    started = time.monotonic()
    for task in tasks:
        await task_shutdown.stop_task_after_grace(task)
    elapsed = time.monotonic() - started

    assert elapsed < 1.5, f"sequential stops took {elapsed:.2f}s against a 0.5s budget"
    assert all(task.cancelled() for task in tasks)


@pytest.mark.asyncio
async def test_exhausted_budget_cancels_immediately_and_still_tracks_deferring_tasks(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _shutdown_budget(monkeypatch, usable_seconds=0.0)
    # The post-cancel wait uses the plain grace (so a promptly cancelled task is
    # awaited); shrink it so the deferring task is tracked quickly.
    monkeypatch.setattr(task_shutdown, "DATABASE_TASK_STOP_GRACE_SECONDS", 0.05)
    caplog.set_level(logging.WARNING, logger=task_shutdown.__name__)
    cleanup_may_finish = asyncio.Event()

    async def shielded_database_cleanup() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await cleanup_may_finish.wait()
            raise

    task = asyncio.create_task(shielded_database_cleanup())
    started = time.monotonic()
    await task_shutdown.stop_task_after_grace(task)

    assert time.monotonic() - started < 0.5
    assert task in task_shutdown.undrained_tasks()  # clean shutdown record stays withheld
    cleanup_may_finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_idle_loop_exits_without_warning_when_no_drain_time_left(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _shutdown_budget(monkeypatch, usable_seconds=0.0)
    caplog.set_level(logging.WARNING, logger=task_shutdown.__name__)
    stop = asyncio.Event()
    ticks: list[int] = []

    async def scheduler_loop() -> None:  # the loop shape every changed scheduler uses
        while not stop.is_set():
            ticks.append(1)
            try:
                await asyncio.wait_for(stop.wait(), timeout=60)
            except TimeoutError:
                continue

    task = asyncio.create_task(scheduler_loop())
    await asyncio.sleep(0.01)  # idle between ticks
    stop.set()
    await task_shutdown.stop_task_after_grace(task)

    assert task.done() and not task.cancelled()
    assert caplog.records == []


@pytest.mark.asyncio
async def test_promptly_cancelled_task_is_finished_when_stop_returns_with_no_drain_time_left(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # app/main.py stops the cache poller only after the model scheduler has finished;
    # that ordering needs stop() to return only once the cancelled task is done.
    _shutdown_budget(monkeypatch, usable_seconds=0.0)
    cleaned_up = asyncio.Event()

    async def tick_in_database_work() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0.02)  # prompt rollback/close on cancellation
            cleaned_up.set()
            raise

    task = asyncio.create_task(tick_in_database_work())
    await asyncio.sleep(0)
    await task_shutdown.stop_task_after_grace(task)

    assert task.done()
    assert cleaned_up.is_set()

"""``stop_task_after_grace`` fallback path (#2505).

Kept as a focused unit test by exception: the process-level integration test
only reaches the happy path (every task finishes inside the grace), so the
cancel-and-warn fallback has no integration path that exercises it.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

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

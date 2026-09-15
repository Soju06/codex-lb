"""Regression tests for the shared-future waiter helper.

The helper replaces ``wait_for(shield(shared))`` on futures awaited by many
concurrent waiters (http-bridge inflight/capacity registries, token-refresh
singleflight). The structural invariant under test: no matter how many
waiters attach, time out, or are cancelled, the shared future carries exactly
one done callback and no leaked per-waiter state. Under the old shield
pattern each waiter attached callbacks to the shared future and removed them
with O(n) scans — a mass timeout livelocked the event loop (2026-08-20
production incident).
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.clock import RealScheduler
from app.core.utils.shared_future import _WAITERS_ATTR, wait_on_shared_future

pytestmark = pytest.mark.unit


def _callback_count(future: asyncio.Future) -> int | None:
    callbacks = getattr(future, "_callbacks", None)
    if callbacks is None:
        return None
    return len(callbacks)


async def test_result_propagates_to_all_waiters():
    shared: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    waiters = [asyncio.create_task(wait_on_shared_future(shared, timeout=5)) for _ in range(10)]
    await asyncio.sleep(0)
    shared.set_result("session")
    assert await asyncio.gather(*waiters) == ["session"] * 10


async def test_exception_propagates_to_all_waiters():
    shared: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    waiters = [asyncio.create_task(wait_on_shared_future(shared, timeout=5)) for _ in range(4)]
    await asyncio.sleep(0)
    shared.set_exception(RuntimeError("creation failed"))
    results = await asyncio.gather(*waiters, return_exceptions=True)
    assert all(isinstance(r, RuntimeError) and str(r) == "creation failed" for r in results)


async def test_shared_cancellation_cancels_waiters():
    shared: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    waiters = [asyncio.create_task(wait_on_shared_future(shared, timeout=5)) for _ in range(4)]
    await asyncio.sleep(0)
    shared.cancel()
    results = await asyncio.gather(*waiters, return_exceptions=True)
    assert all(isinstance(r, asyncio.CancelledError) for r in results)


async def test_timeout_raises_and_leaves_shared_pending():
    shared: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    with pytest.raises(TimeoutError):
        await wait_on_shared_future(shared, timeout=0.01)
    assert not shared.done()
    assert not shared.cancelled()
    # The owner can still complete the creation after waiters gave up.
    shared.set_result("late")
    assert await wait_on_shared_future(shared) == "late"


async def test_mass_timeout_does_not_accumulate_callbacks_on_shared():
    """The incident shape: many waiters piling onto one pending future and
    timing out together must leave the shared future's callback list at its
    constant size (one fan-out callback), not one-or-more per waiter."""
    shared: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    for _ in range(3):  # repeated retry rounds, as in the admission loop
        waiters = [asyncio.create_task(wait_on_shared_future(shared, timeout=0.01)) for _ in range(200)]
        results = await asyncio.gather(*waiters, return_exceptions=True)
        assert all(isinstance(r, TimeoutError) for r in results)
    count = _callback_count(shared)
    if count is not None:
        assert count == 1
    assert getattr(shared, _WAITERS_ATTR) == set()
    assert not shared.done()
    shared.cancel()


async def test_cancelling_one_waiter_leaves_others_and_shared_intact():
    shared: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    victim = asyncio.create_task(wait_on_shared_future(shared, timeout=5))
    survivor = asyncio.create_task(wait_on_shared_future(shared, timeout=5))
    await asyncio.sleep(0)
    victim.cancel()
    with pytest.raises(asyncio.CancelledError):
        await victim
    assert not shared.done()
    shared.set_result("session")
    assert await survivor == "session"


async def test_done_shared_returns_immediately():
    shared: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    shared.set_result("cached")
    assert await wait_on_shared_future(shared, timeout=0.01) == "cached"

    failed: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    failed.set_exception(RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        await wait_on_shared_future(failed)


async def test_late_waiter_after_fan_out_gets_result():
    shared: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    first = asyncio.create_task(wait_on_shared_future(shared, timeout=5))
    await asyncio.sleep(0)
    shared.set_result("session")
    assert await first == "session"
    # Fan-out already ran and cleared the waiter set; a late waiter must not
    # hang on the emptied set.
    assert await wait_on_shared_future(shared, timeout=0.01) == "session"


async def test_shared_task_keeps_running_when_all_waiters_cancel():
    """Singleflight semantics: waiter cancellation must not abort the work."""
    finished = asyncio.Event()

    async def _work() -> str:
        await asyncio.sleep(0.05)
        finished.set()
        return "refreshed"

    task = asyncio.create_task(_work())
    waiter = asyncio.create_task(wait_on_shared_future(task))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert await task == "refreshed"
    assert finished.is_set()


async def test_timed_wait_runs_through_injected_scheduler_and_untimed_wait_does_not():
    recorded: list[tuple[object, float | None]] = []

    class RecordingScheduler(RealScheduler):
        async def wait_for(self, fut, timeout):
            recorded.append((fut, timeout))
            return await asyncio.wait_for(fut, timeout)

    scheduler = RecordingScheduler()
    shared: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    timed = asyncio.create_task(wait_on_shared_future(shared, timeout=5, scheduler=scheduler))
    untimed = asyncio.create_task(wait_on_shared_future(shared, scheduler=scheduler))
    await asyncio.sleep(0)
    shared.set_result("session")

    assert await asyncio.gather(timed, untimed) == ["session", "session"]
    # Only the timed wait is a timing seam; ``timeout=None`` keeps the plain
    # ``await proxy`` fast path so level-cancelled loops behave as before.
    assert [timeout for _awaitable, timeout in recorded] == [5]
    assert isinstance(recorded[0][0], asyncio.Future)
    assert recorded[0][0] is not shared


async def test_a_failure_that_lands_with_no_waiter_left_is_still_consumed():
    """A waiter that times out leaves the shared future unattended.

    ``wait_on_shared_future`` discards the waiter's proxy when it times out, so
    a shared future that completes in the gap before the next wait reaches the
    fan-out callback with an empty waiter set. If the fan-out only consumed the
    exception while delivering it, nothing would retrieve it, and asyncio logs
    ``Task exception was never retrieved`` — an ERROR plus traceback — when the
    task is collected. Production saw exactly that from the SSE keepalive
    injector: one per stream whose source ended between waits, on requests that
    had otherwise returned 200.
    """

    started = asyncio.Event()

    async def _fails() -> str:
        started.set()
        await asyncio.sleep(0.02)
        raise StopAsyncIteration

    task = asyncio.create_task(_fails())
    with pytest.raises(asyncio.TimeoutError):
        await wait_on_shared_future(task, timeout=0.001)
    await started.wait()
    # No waiter is registered from here on, which is the production shape.
    assert not getattr(task, _WAITERS_ATTR, set())

    # Let the task fail with nobody awaiting it. Awaiting it here would
    # retrieve the exception and hide the very thing under test.
    await asyncio.wait([task])
    # The fan-out is a done callback, so it runs a loop pass *after* the task
    # completes; asserting on ``done()`` alone races it.
    for _ in range(3):
        await asyncio.sleep(0)

    # ``_log_traceback`` is what asyncio's destructor checks before logging;
    # retrieving the exception clears it. Asserting the flag is how the test
    # observes "nobody would be told about this" without racing the GC.
    assert task._log_traceback is False
    assert isinstance(task.exception(), StopAsyncIteration)


async def test_a_failure_delivered_to_a_live_waiter_is_still_consumed():
    """The empty-set fix must not regress the delivering path."""

    async def _fails() -> str:
        raise RuntimeError("upstream gone")

    task = asyncio.create_task(_fails())
    with pytest.raises(RuntimeError):
        await wait_on_shared_future(task)
    assert task._log_traceback is False

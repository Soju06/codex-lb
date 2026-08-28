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

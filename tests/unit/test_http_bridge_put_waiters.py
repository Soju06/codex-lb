from __future__ import annotations

import asyncio
from typing import Any

import anyio
import pytest

from app.core.clock import REAL_SCHEDULER
from app.modules.proxy._service.http_bridge.request_submit import (
    _HTTPBridgeLiveEventQueue,
    _HTTPBridgeLiveEventQueueByteBudget,
)
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("release", ["drain", "revoke", "discard", "terminal", "eos"])
async def test_blocked_put_wakes_in_producer_task(monkeypatch: pytest.MonkeyPatch, release: str) -> None:
    budget = _HTTPBridgeLiveEventQueueByteBudget(max_bytes=64)
    queue = _HTTPBridgeLiveEventQueue(maxsize=1, revoked=asyncio.Event(), byte_budget=budget)
    queue.put_nowait("first")
    loop = asyncio.get_running_loop()

    def no_child_tasks(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("blocked bridge puts must not spawn child tasks")

    def release_waiter() -> None:
        assert budget.used_bytes == len("first") + len("blocked")
        if release == "drain":
            assert queue.get_nowait() == "first"
        elif release == "revoke":
            queue.revoke()
        elif release == "discard":
            queue.discard()
        elif release == "terminal":
            queue.enqueue_terminal_event_nowait("terminal")
        else:
            queue.enqueue_terminal_nowait()

    callback = loop.call_soon(release_waiter)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(loop, "create_task", no_child_tasks)
            await queue.put("blocked")
        if release == "drain":
            assert queue.get_nowait() == "blocked"
        elif release != "discard":
            assert queue.get_nowait() == "first"
        if release == "terminal":
            assert queue.get_nowait() == "terminal"
            assert queue.get_nowait() is None
        elif release in {"eos", "discard"}:
            assert queue.get_nowait() is None
        assert queue.empty()
        assert budget.used_bytes == 0
    finally:
        callback.cancel()
        queue.discard()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_after_wakeup", [False, True])
async def test_cancelled_put_does_not_take_slot_or_strand_waiting_producer(cancel_after_wakeup: bool) -> None:
    budget = _HTTPBridgeLiveEventQueueByteBudget(max_bytes=32)
    queue = _HTTPBridgeLiveEventQueue(maxsize=1, revoked=asyncio.Event(), byte_budget=budget)
    queue.put_nowait("first")
    cancelled = asyncio.create_task(queue.put("cancelled"))
    retained = asyncio.create_task(queue.put("retained"))
    try:
        await asyncio.sleep(0)
        assert budget.used_bytes == len("firstcancelledretained")
        if cancel_after_wakeup:
            assert queue.get_nowait() == "first"
        cancelled.cancel()
        cancelled.cancel()
        if not cancel_after_wakeup:
            assert queue.get_nowait() == "first"
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        await asyncio.wait_for(retained, timeout=1.0)
        assert queue.qsize() == 1
        assert budget.used_bytes == len("retained")
        assert queue.get_nowait() == "retained"
        assert budget.used_bytes == 0
        # Both the immediate and previously blocked put must join correctly.
        queue.task_done()
        queue.task_done()
        await asyncio.wait_for(queue.join(), timeout=1.0)
    finally:
        cancelled.cancel()
        retained.cancel()
        await asyncio.gather(cancelled, retained, return_exceptions=True)
        queue.discard()


@pytest.mark.asyncio
async def test_anyio_cancellation_releases_blocked_reservation_without_cleanup_await() -> None:
    budget = _HTTPBridgeLiveEventQueueByteBudget(max_bytes=32)
    queue = _HTTPBridgeLiveEventQueue(maxsize=1, revoked=asyncio.Event(), byte_budget=budget)
    queue.put_nowait("first")
    scope_ready = asyncio.Event()
    scope: anyio.CancelScope | None = None

    async def produce() -> None:
        nonlocal scope
        with anyio.CancelScope() as scope:
            scope_ready.set()
            await queue.put("blocked")

    producer = asyncio.create_task(produce())
    try:
        await scope_ready.wait()
        assert budget.used_bytes == len("firstblocked")
        assert scope is not None
        scope.cancel()
        scope.cancel()
        await asyncio.wait_for(producer, timeout=1.0)
        assert budget.used_bytes == len("first")
        assert queue.get_nowait() == "first"
        assert budget.used_bytes == 0
    finally:
        producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
        queue.discard()


@pytest.mark.asyncio
@pytest.mark.parametrize("virtual", [False, True])
async def test_put_timeout_releases_reservation_in_owner_task(virtual: bool) -> None:
    scheduler = VirtualScheduler(VirtualClock()) if virtual else REAL_SCHEDULER
    budget = _HTTPBridgeLiveEventQueueByteBudget(max_bytes=32)
    queue = _HTTPBridgeLiveEventQueue(maxsize=1, revoked=asyncio.Event(), byte_budget=budget, scheduler=scheduler)
    queue.put_nowait("first")

    async def produce() -> None:
        async with scheduler.timeout(0.01):
            await queue.put("blocked")

    producer = scheduler.create_task(produce())
    try:
        if isinstance(scheduler, VirtualScheduler):
            await scheduler.advance(0.009)
            assert not producer.done()
            assert budget.used_bytes == len("firstblocked")
            await scheduler.advance(0.001)
        with pytest.raises(TimeoutError):
            await producer
        assert budget.used_bytes == len("first")
        assert queue.get_nowait() == "first"
        assert budget.used_bytes == 0
        if isinstance(scheduler, VirtualScheduler):
            assert scheduler.pending_timers == 0
            assert not scheduler.owned_tasks
    finally:
        producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
        queue.discard()


@pytest.mark.asyncio
async def test_failed_new_reservation_wakes_already_blocked_producer() -> None:
    budget = _HTTPBridgeLiveEventQueueByteBudget(max_bytes=12)
    queue = _HTTPBridgeLiveEventQueue(maxsize=1, revoked=asyncio.Event(), byte_budget=budget)
    queue.put_nowait("first")
    producer = asyncio.create_task(queue.put("blocked"))
    try:
        await asyncio.sleep(0)
        assert budget.used_bytes == 12
        await queue.put("over-budget")
        await asyncio.wait_for(producer, timeout=1.0)
        assert queue.budget_exceeded.is_set()
        assert queue.terminal_budget_exceeded
        assert budget.used_bytes == len("first")
        assert queue.get_nowait() == "first"
        assert budget.used_bytes == 0
    finally:
        producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
        queue.discard()

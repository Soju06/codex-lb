from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.core.clock import REAL_SCHEDULER
from app.modules.proxy._service.http_bridge.request_submit import _HTTPBridgeLiveEventQueue
from app.modules.proxy._service.http_bridge.streaming import _next_http_bridge_event_block
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("virtual", [False, True])
@pytest.mark.parametrize("timeout", [-1.0, 0.0, 1.0])
async def test_raced_publication_uses_no_read_child_task(
    monkeypatch: pytest.MonkeyPatch, virtual: bool, timeout: float
) -> None:
    scheduler = VirtualScheduler(VirtualClock()) if virtual else REAL_SCHEDULER
    queue = _HTTPBridgeLiveEventQueue(maxsize=2, revoked=asyncio.Event(), scheduler=scheduler)
    loop = asyncio.get_running_loop()

    def forbid_child(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("bridge read spawned a child task")

    loop.call_soon(queue.put_nowait, "raced")
    try:
        with monkeypatch.context() as patch:
            patch.setattr(loop, "create_task", forbid_child)
            assert await _next_http_bridge_event_block(queue, timeout=timeout, scheduler=scheduler) == "raced"
        assert queue.empty()
        assert queue.queued_bytes == 0
        if isinstance(scheduler, VirtualScheduler):
            assert scheduler.pending_timers == 0
            assert not scheduler.owned_tasks
    finally:
        queue.discard()


@pytest.mark.asyncio
async def test_virtual_read_deadline_keeps_late_payload_for_next_read() -> None:
    scheduler = VirtualScheduler(VirtualClock())
    queue = _HTTPBridgeLiveEventQueue(maxsize=2, revoked=asyncio.Event(), scheduler=scheduler)
    reader = scheduler.create_task(_next_http_bridge_event_block(queue, timeout=1.0, scheduler=scheduler))
    try:
        await scheduler.advance(0.999)
        assert not reader.done()
        await scheduler.advance(0.001)
        with pytest.raises(TimeoutError):
            await reader
        queue.put_nowait("late")
        assert await _next_http_bridge_event_block(queue, timeout=1.0, scheduler=scheduler) == "late"
        assert queue.queued_bytes == 0
        assert scheduler.pending_timers == 0
        assert all(task.done() for task in scheduler.owned_tasks)
    finally:
        await scheduler.cancel_owned_tasks()
        queue.discard()


@pytest.mark.asyncio
async def test_virtual_revoked_reader_waits_for_selected_terminal() -> None:
    scheduler = VirtualScheduler(VirtualClock())
    queue = _HTTPBridgeLiveEventQueue(maxsize=2, revoked=asyncio.Event(), scheduler=scheduler)
    reader = scheduler.create_task(_next_http_bridge_event_block(queue, timeout=10.0, scheduler=scheduler))
    try:
        await scheduler.drain()
        queue.revoke()
        await scheduler.advance(2.0)
        assert not reader.done()
        queue.enqueue_terminal_event_nowait("selected-terminal")
        await scheduler.drain()
        assert await reader == "selected-terminal"
        assert await _next_http_bridge_event_block(queue, timeout=1.0, scheduler=scheduler) is None
        assert queue.queued_bytes == 0
        assert scheduler.pending_timers == 0
        assert all(task.done() for task in scheduler.owned_tasks)
    finally:
        await scheduler.cancel_owned_tasks()
        queue.discard()


@pytest.mark.asyncio
async def test_virtual_read_external_cancellation_keeps_queued_payload() -> None:
    scheduler = VirtualScheduler(VirtualClock())
    queue = _HTTPBridgeLiveEventQueue(maxsize=2, revoked=asyncio.Event(), scheduler=scheduler)
    reader = scheduler.create_task(_next_http_bridge_event_block(queue, timeout=1.0, scheduler=scheduler))
    try:
        await scheduler.drain()
        queue.put_nowait("retained")
        reader.cancel()
        with pytest.raises(asyncio.CancelledError):
            await reader
        assert await _next_http_bridge_event_block(queue, timeout=1.0, scheduler=scheduler) == "retained"
        assert queue.queued_bytes == 0
        assert scheduler.pending_timers == 0
        assert all(task.done() for task in scheduler.owned_tasks)
    finally:
        await scheduler.cancel_owned_tasks()
        queue.discard()

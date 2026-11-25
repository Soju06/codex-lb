from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.modules.proxy._service.http_bridge.request_submit import _HTTPBridgeLiveEventQueue
from app.modules.proxy._service.http_bridge.streaming import (
    _HTTPBridgeLiveEventQueueBudgetExceeded,
    _next_http_bridge_event_block,
)

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("publication", ["live", "terminal", "discard", "budget"])
async def test_empty_bridge_reader_wakes_without_child_tasks(monkeypatch: pytest.MonkeyPatch, publication: str) -> None:
    queue = _HTTPBridgeLiveEventQueue(maxsize=2, revoked=asyncio.Event())
    loop = asyncio.get_running_loop()

    def no_child_tasks(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("empty bridge reads must not spawn child tasks")

    def publish() -> None:
        if publication == "live":
            queue.put_nowait("event")
        elif publication == "terminal":
            queue.enqueue_terminal_event_nowait("event")
        elif publication == "discard":
            queue.discard()
        else:
            queue.budget_exceeded.set()
            queue.revoke()

    loop.call_soon(publish)
    monkeypatch.setattr(loop, "create_task", no_child_tasks)
    try:
        if publication == "budget":
            with pytest.raises(_HTTPBridgeLiveEventQueueBudgetExceeded):
                await _next_http_bridge_event_block(queue, timeout=1.0)
        else:
            assert await _next_http_bridge_event_block(queue, timeout=1.0) == (
                None if publication == "discard" else "event"
            )
    finally:
        queue.discard()


@pytest.mark.asyncio
async def test_revocation_keeps_empty_reader_waiting_for_actual_terminal() -> None:
    queue = _HTTPBridgeLiveEventQueue(maxsize=2, revoked=asyncio.Event())
    reader = asyncio.create_task(_next_http_bridge_event_block(queue, timeout=1.0))
    try:
        await asyncio.sleep(0)
        queue.revoke()
        await asyncio.sleep(0)
        assert not reader.done()
        assert queue.enqueue_terminal_event_nowait("actual-terminal")
        assert await reader == "actual-terminal"
        assert await queue.get() is None
        assert queue.queued_bytes == 0
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        queue.discard()


@pytest.mark.asyncio
async def test_cancelling_awakened_reader_does_not_strand_another_reader() -> None:
    queue = _HTTPBridgeLiveEventQueue(maxsize=2, revoked=asyncio.Event())
    first = asyncio.create_task(queue.get())
    second = asyncio.create_task(queue.get())
    try:
        await asyncio.sleep(0)
        queue.put_nowait("event")
        first.cancel()
        assert await asyncio.wait_for(second, timeout=1.0) == "event"
        with pytest.raises(asyncio.CancelledError):
            await first
        assert queue.empty()
        assert queue.queued_bytes == 0
    finally:
        first.cancel()
        second.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
        queue.discard()

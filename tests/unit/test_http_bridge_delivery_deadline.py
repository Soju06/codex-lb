from __future__ import annotations

import asyncio

import pytest

from app.core.utils.sse import parse_sse_data_json
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.http_bridge.request_submit import (
    _HTTPBridgeLiveEventQueue,
    _HTTPBridgeLiveEventQueueByteBudget,
)
from app.modules.proxy._service.http_bridge.streaming import (
    _HTTPBridgeLiveEventQueueBudgetExceeded,
    _next_http_bridge_event_block,
)
from app.modules.proxy._service.http_bridge.upstream_events import _enqueue_http_bridge_event
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler

pytestmark = pytest.mark.unit


def _state(queue: _HTTPBridgeLiveEventQueue, deadline: float) -> proxy_service._WebSocketRequestState:
    return proxy_service._WebSocketRequestState(
        request_id="deadline",
        response_id="resp-deadline",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=100.0,
        event_queue=queue,
        event_queue_revoked=queue.revoked,
        event_queue_consumer_started=True,
        bridge_request_deadline=deadline,
        transport="http",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("full", [False, True])
async def test_expired_payload_latches_failure_after_terminal_is_consumed(full: bool) -> None:
    clock = VirtualClock(monotonic_value=100.0)
    scheduler = VirtualScheduler(clock)
    budget = _HTTPBridgeLiveEventQueueByteBudget(max_bytes=4096)
    queue = _HTTPBridgeLiveEventQueue(maxsize=2, revoked=asyncio.Event(), byte_budget=budget, scheduler=scheduler)
    state = _state(queue, 100.0)
    if full:
        queue.put_nowait("one")
        queue.put_nowait("two")
    assert not await _enqueue_http_bridge_event(state, queue, "lost", scheduler=scheduler, clock=clock)
    if full:
        assert await queue.get() == "one"
        assert await queue.get() == "two"
    failure_block = await queue.get()
    assert failure_block is not None
    failure = parse_sse_data_json(failure_block)
    assert failure is not None
    assert failure["type"] == "response.failed"
    failure_response = failure["response"]
    assert isinstance(failure_response, dict)
    failure_error = failure_response["error"]
    assert isinstance(failure_error, dict)
    assert failure_error["code"] == "request_timeout"
    assert await queue.get() is None
    assert not await _enqueue_http_bridge_event(
        state, queue, 'data: {"type":"response.completed"}\n\n', terminal=True, scheduler=scheduler, clock=clock
    )
    assert await queue.get() is None
    queue.discard()
    assert budget.used_bytes == 0
    assert scheduler.pending_timers == 0
    assert not scheduler.owned_tasks


@pytest.mark.asyncio
async def test_eos_only_deadline_keeps_accepted_completion() -> None:
    clock = VirtualClock(monotonic_value=100.0)
    scheduler = VirtualScheduler(clock)
    queue = _HTTPBridgeLiveEventQueue(maxsize=1, revoked=asyncio.Event(), scheduler=scheduler)
    state = _state(queue, 101.0)
    completed = 'data: {"type":"response.completed"}\n\n'
    producer = scheduler.create_task(
        _enqueue_http_bridge_event(state, queue, completed, terminal=True, scheduler=scheduler, clock=clock)
    )
    await scheduler.advance(0.9)
    assert queue.full()
    assert not producer.done()
    await scheduler.advance(0.1)
    assert await producer is False
    assert await queue.get() == completed
    assert await queue.get() is None
    queue.discard()
    await scheduler.drain()
    assert scheduler.pending_timers == 0
    assert all(task.done() for task in scheduler.owned_tasks)


@pytest.mark.asyncio
async def test_deadline_failure_that_cannot_fit_budget_still_fails_closed() -> None:
    clock = VirtualClock(monotonic_value=100.0)
    budget = _HTTPBridgeLiveEventQueueByteBudget(max_bytes=3)
    queue = _HTTPBridgeLiveEventQueue(maxsize=2, revoked=asyncio.Event(), byte_budget=budget)
    queue.put_nowait("one")
    state = _state(queue, 99.0)
    assert not await _enqueue_http_bridge_event(state, queue, "lost", clock=clock)
    assert await _next_http_bridge_event_block(queue, timeout=None) == "one"
    with pytest.raises(_HTTPBridgeLiveEventQueueBudgetExceeded):
        await _next_http_bridge_event_block(queue, timeout=None)
    assert not queue.enqueue_terminal_event_nowait('data: {"type":"response.completed"}\n\n')
    queue.discard()
    assert budget.used_bytes == 0

from __future__ import annotations

import asyncio
from collections import deque
from types import SimpleNamespace
from typing import Any, cast

import anyio
import pytest

from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.http_bridge import request_submit as http_bridge_request_submit
from app.modules.proxy._service.http_bridge import upstream_events as http_bridge_upstream_events

pytestmark = pytest.mark.unit


class _AbortEosService(http_bridge_upstream_events._HTTPBridgeUpstreamEventsMixin):
    def _cancel_request_state_api_key_reservation_heartbeat(
        self,
        request_state: proxy_service._WebSocketRequestState,
    ) -> None:
        del request_state

    async def _release_websocket_request_state_reservation(
        self,
        request_state: proxy_service._WebSocketRequestState,
    ) -> None:
        del request_state


def _make_session() -> proxy_service._HTTPBridgeSession:
    return proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-abort-eos", None),
        headers={},
        affinity=proxy_service._AffinityPolicy(
            key="sid-abort-eos",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.5",
        account=cast(Any, SimpleNamespace(id="acc-abort-eos")),
        upstream=cast(Any, SimpleNamespace()),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=0.0,
        idle_ttl_seconds=60.0,
    )


def _make_claimed_request_state(
    event_queue: asyncio.Queue[str | None],
    *,
    event_queue_revoked: asyncio.Event | None = None,
) -> proxy_service._WebSocketRequestState:
    return proxy_service._WebSocketRequestState(
        request_id="req-abort-eos",
        model="gpt-5.5",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        event_queue=event_queue,
        event_queue_revoked=event_queue_revoked or asyncio.Event(),
        terminal_settlement_phase="claimed",
        transport="http",
    )


@pytest.mark.asyncio
async def test_aborted_terminal_settlement_unblocks_full_live_queue() -> None:
    """An aborted terminal owner must publish EOS even when the live queue is full."""
    event_queue = http_bridge_request_submit._HTTPBridgeLiveEventQueue(
        maxsize=2,
        revoked=asyncio.Event(),
    )
    event_queue.put_nowait("first-buffered-event")
    event_queue.put_nowait("last-buffered-event")
    assert event_queue.full()
    request_state = _make_claimed_request_state(event_queue, event_queue_revoked=event_queue.revoked)
    request_state.event_queue_consumer_started = True

    await asyncio.wait_for(
        _AbortEosService()._settle_aborted_http_bridge_terminal_states(
            _make_session(),
            [request_state],
        ),
        timeout=0.1,
    )

    # The terminal marker follows every buffered event instead of evicting the
    # oldest one, and settlement still completes without waiting for a read.
    assert [event_queue.get_nowait(), event_queue.get_nowait(), event_queue.get_nowait()] == [
        "first-buffered-event",
        "last-buffered-event",
        None,
    ]
    assert request_state.terminal_settlement_phase is None
    assert not [
        task for task in asyncio.all_tasks() if task.get_name() in {"http-bridge-event-put", "http-bridge-event-revoke"}
    ]


@pytest.mark.asyncio
async def test_aborted_terminal_settlement_preserves_preconsumer_queue_until_consumed() -> None:
    budget = http_bridge_request_submit._HTTPBridgeLiveEventQueueByteBudget(max_bytes=64)
    event_queue = http_bridge_request_submit._HTTPBridgeLiveEventQueue(
        maxsize=2,
        revoked=asyncio.Event(),
        byte_budget=budget,
    )
    event_queue.put_nowait("unread-aborted-payload")
    request_state = _make_claimed_request_state(event_queue, event_queue_revoked=event_queue.revoked)

    await _AbortEosService()._settle_aborted_http_bridge_terminal_states(
        _make_session(),
        [request_state],
    )

    assert event_queue.get_nowait() == "unread-aborted-payload"
    assert event_queue.get_nowait() is None
    assert event_queue.empty()
    assert event_queue.queued_bytes == 0
    assert budget.used_bytes == 0


@pytest.mark.asyncio
async def test_revoked_discarded_live_queue_wakes_a_delayed_consumer() -> None:
    event_queue = http_bridge_request_submit._HTTPBridgeLiveEventQueue(
        maxsize=2,
        revoked=asyncio.Event(),
    )
    event_queue.put_nowait("preconsumer-payload")
    event_queue.revoke()
    event_queue.discard()

    assert await asyncio.wait_for(event_queue.get(), timeout=0.1) is None


def test_abort_eos_respects_queue_revocation() -> None:
    event_queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=4)
    event_queue.put_nowait("buffered-event")
    request_state = _make_claimed_request_state(event_queue)
    request_state.event_queue_revoked.set()

    # Revocation stops live producers, but terminal abort EOS remains allowed
    # so a delayed consumer can drain the buffered event and finish promptly.
    assert http_bridge_upstream_events._enqueue_http_bridge_abort_eos(request_state, event_queue) is True
    assert event_queue.get_nowait() == "buffered-event"
    assert event_queue.get_nowait() is None
    assert event_queue.empty()


@pytest.mark.asyncio
async def test_best_effort_advisory_does_not_wait_for_preconsumer_capacity() -> None:
    event_queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=1)
    event_queue.put_nowait("buffered-event")
    request_state = _make_claimed_request_state(event_queue)
    request_state.event_queue_consumer_started = False

    delivered = await asyncio.wait_for(
        http_bridge_upstream_events._enqueue_http_bridge_event(
            request_state,
            event_queue,
            "advisory",
            nonblocking_preconsumer=True,
        ),
        timeout=0.1,
    )

    assert delivered is False
    assert event_queue.get_nowait() == "buffered-event"


@pytest.mark.asyncio
@pytest.mark.parametrize("queue_state", ["revoked", "terminal_pending", "budget_exhausted"])
async def test_best_effort_advisory_reports_custom_queue_drop_without_leaking_budget(queue_state: str) -> None:
    budget = http_bridge_request_submit._HTTPBridgeLiveEventQueueByteBudget(max_bytes=8)
    event_queue = http_bridge_request_submit._HTTPBridgeLiveEventQueue(
        maxsize=2,
        revoked=asyncio.Event(),
        byte_budget=budget,
    )
    request_state = _make_claimed_request_state(event_queue, event_queue_revoked=event_queue.revoked)
    request_state.event_queue_consumer_started = False

    if queue_state == "revoked":
        event_queue.revoke()
    elif queue_state == "terminal_pending":
        assert event_queue.enqueue_terminal_nowait() is True
    else:
        budget = http_bridge_request_submit._HTTPBridgeLiveEventQueueByteBudget(max_bytes=1)
        event_queue = http_bridge_request_submit._HTTPBridgeLiveEventQueue(
            maxsize=2,
            revoked=asyncio.Event(),
            byte_budget=budget,
        )
        request_state.event_queue = event_queue
        request_state.event_queue_revoked = event_queue.revoked

    delivered = await http_bridge_upstream_events._enqueue_http_bridge_event(
        request_state,
        event_queue,
        "advisory",
        nonblocking_preconsumer=True,
    )

    assert delivered is False
    assert event_queue.queued_bytes == 0
    assert budget.used_bytes == 0
    if queue_state == "terminal_pending":
        assert await event_queue.get() is None
    else:
        assert event_queue.empty()

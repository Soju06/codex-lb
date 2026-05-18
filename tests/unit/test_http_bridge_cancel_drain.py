from __future__ import annotations

import asyncio
import json
from collections import deque
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio
import pytest

from app.core.clients.proxy_websocket import UpstreamResponsesWebSocket
from app.db.models import AccountStatus
from app.modules.proxy import service as proxy_service

pytestmark = pytest.mark.unit


def _make_http_bridge_session(
    pending_requests: deque[proxy_service._WebSocketRequestState],
    *,
    queued_request_count: int,
) -> proxy_service._HTTPBridgeSession:
    return proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-cancel-drain", None),
        headers={"x-codex-session-id": "sid-cancel-drain"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-cancel-drain",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.5",
        account=cast(Any, SimpleNamespace(id="acc-cancel-drain", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, SimpleNamespace(close=AsyncMock())),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=queued_request_count,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )


def _make_request_state(
    request_id: str,
    *,
    response_id: str | None,
    awaiting_response_created: bool,
    event_queue: asyncio.Queue[str | None] | None = None,
) -> proxy_service._WebSocketRequestState:
    return proxy_service._WebSocketRequestState(
        request_id=request_id,
        model="gpt-5.5",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        response_id=response_id,
        awaiting_response_created=awaiting_response_created,
        event_queue=event_queue,
        transport="http",
        skip_request_log=True,
    )


@pytest.mark.asyncio
async def test_cancelled_http_bridge_request_quarantines_late_anonymous_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled downstream stream is still alive upstream until terminal.

    Late upstream events may be anonymous (no top-level response.id). They must
    be drained by the cancelled request state, not routed to the next unresolved
    request on the same HTTP bridge session.
    """
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    monkeypatch.setattr(service, "_release_websocket_reservation", AsyncMock())
    monkeypatch.setattr(service, "_finalize_websocket_request_state", AsyncMock())

    cancelled_request = _make_request_state(
        "req-cancelled",
        response_id="resp-cancelled",
        awaiting_response_created=False,
        event_queue=asyncio.Queue(),
    )
    session = _make_http_bridge_session(deque([cancelled_request]), queued_request_count=1)

    detached = await service._detach_http_bridge_request(session, request_state=cancelled_request)
    assert detached is True

    retry_queue: asyncio.Queue[str | None] = asyncio.Queue()
    retry_request = _make_request_state(
        "req-retry",
        response_id=None,
        awaiting_response_created=True,
        event_queue=retry_queue,
    )
    async with session.pending_lock:
        session.pending_requests.append(retry_request)
        session.queued_request_count += 1

    await service._process_http_bridge_upstream_text(
        session,
        json.dumps(
            {
                "type": "response.output_item.added",
                "sequence_number": 1,
                "output_index": 0,
                "item": {
                    "id": "msg-orphan-from-cancelled-request",
                    "type": "message",
                    "role": "assistant",
                    "status": "in_progress",
                    "content": [],
                },
            },
            separators=(",", ":"),
        ),
    )

    assert retry_queue.empty(), "late anonymous output from the cancelled request leaked into the retry queue"
    assert cancelled_request in session.pending_requests
    assert retry_request in session.pending_requests

    await service._process_http_bridge_upstream_text(
        session,
        json.dumps(
            {
                "type": "response.completed",
                "sequence_number": 2,
                "response": {
                    "id": "resp-cancelled",
                    "object": "response",
                    "status": "completed",
                    "output": [],
                },
            },
            separators=(",", ":"),
        ),
    )

    assert cancelled_request not in session.pending_requests
    assert retry_request in session.pending_requests
    assert session.queued_request_count == 1


@pytest.mark.asyncio
async def test_cancelled_http_bridge_request_does_not_steal_active_response_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    monkeypatch.setattr(service, "_release_websocket_reservation", AsyncMock())
    monkeypatch.setattr(service, "_finalize_websocket_request_state", AsyncMock())

    cancelled_request = _make_request_state(
        "req-cancelled-before-created",
        response_id=None,
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
    )
    session = _make_http_bridge_session(deque([cancelled_request]), queued_request_count=1)

    detached = await service._detach_http_bridge_request(session, request_state=cancelled_request)
    assert detached is True

    retry_queue: asyncio.Queue[str | None] = asyncio.Queue()
    retry_request = _make_request_state(
        "req-active-created",
        response_id=None,
        awaiting_response_created=True,
        event_queue=retry_queue,
    )
    async with session.pending_lock:
        session.pending_requests.append(retry_request)
        session.queued_request_count += 1

    await service._process_http_bridge_upstream_text(
        session,
        json.dumps(
            {
                "type": "response.created",
                "sequence_number": 1,
                "response": {
                    "id": "resp-active-created",
                    "object": "response",
                    "status": "in_progress",
                    "output": [],
                },
            },
            separators=(",", ":"),
        ),
    )

    retry_event = await asyncio.wait_for(retry_queue.get(), timeout=0.1)
    assert retry_event is not None
    assert "resp-active-created" in retry_event
    assert cancelled_request.response_id is None
    assert retry_request.response_id == "resp-active-created"
    assert cancelled_request in session.pending_requests
    assert retry_request in session.pending_requests
    assert session.queued_request_count == 1


@pytest.mark.asyncio
async def test_cancelled_http_bridge_request_does_not_swallow_active_anonymous_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    monkeypatch.setattr(service, "_release_websocket_reservation", AsyncMock())
    monkeypatch.setattr(service, "_finalize_websocket_request_state", AsyncMock())

    cancelled_request = _make_request_state(
        "req-cancelled-draining",
        response_id=None,
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
    )
    session = _make_http_bridge_session(deque([cancelled_request]), queued_request_count=1)

    detached = await service._detach_http_bridge_request(session, request_state=cancelled_request)
    assert detached is True

    retry_queue: asyncio.Queue[str | None] = asyncio.Queue()
    retry_request = _make_request_state(
        "req-active-delta",
        response_id="resp-active-delta",
        awaiting_response_created=False,
        event_queue=retry_queue,
    )
    async with session.pending_lock:
        session.pending_requests.append(retry_request)
        session.queued_request_count += 1

    await service._process_http_bridge_upstream_text(
        session,
        json.dumps(
            {
                "type": "response.output_item.added",
                "sequence_number": 2,
                "output_index": 0,
                "item": {
                    "id": "msg-active-delta",
                    "type": "message",
                    "role": "assistant",
                    "status": "in_progress",
                    "content": [],
                },
            },
            separators=(",", ":"),
        ),
    )

    retry_event = await asyncio.wait_for(retry_queue.get(), timeout=0.1)
    assert retry_event is not None
    assert "msg-active-delta" in retry_event
    assert cancelled_request in session.pending_requests
    assert retry_request in session.pending_requests
    assert session.queued_request_count == 1


@pytest.mark.asyncio
async def test_cancelled_http_bridge_request_does_not_swallow_active_anonymous_terminal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A draining cancelled request should quarantine data deltas, not active terminal errors."""
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    monkeypatch.setattr(service, "_release_websocket_reservation", AsyncMock())
    monkeypatch.setattr(service, "_finalize_websocket_request_state", AsyncMock())

    cancelled_request = _make_request_state(
        "req-cancelled-precreated",
        response_id=None,
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
    )
    session = _make_http_bridge_session(deque([cancelled_request]), queued_request_count=1)

    detached = await service._detach_http_bridge_request(session, request_state=cancelled_request)
    assert detached is True

    retry_queue: asyncio.Queue[str | None] = asyncio.Queue()
    retry_request = _make_request_state(
        "req-active-retry",
        response_id=None,
        awaiting_response_created=True,
        event_queue=retry_queue,
    )
    async with session.pending_lock:
        session.pending_requests.append(retry_request)
        session.queued_request_count += 1

    await service._process_http_bridge_upstream_text(
        session,
        json.dumps(
            {
                "type": "error",
                "status": 502,
                "error": {
                    "code": "upstream_error",
                    "type": "server_error",
                    "message": "pre-created request failed",
                },
            },
            separators=(",", ":"),
        ),
    )

    retry_event = await asyncio.wait_for(retry_queue.get(), timeout=0.1)
    assert retry_event is not None
    assert "pre-created request failed" in retry_event
    assert await asyncio.wait_for(retry_queue.get(), timeout=0.1) is None
    assert cancelled_request in session.pending_requests
    assert retry_request not in session.pending_requests
    assert session.queued_request_count == 0


def test_anonymous_event_prefers_unresolved_visible_request_before_active_response() -> None:
    """A normal pipelined request awaiting response.created owns pre-created anonymous events."""
    active_request = _make_request_state(
        "req-active-created",
        response_id="resp-active-created",
        awaiting_response_created=False,
        event_queue=asyncio.Queue(),
    )
    waiting_request = _make_request_state(
        "req-waiting-created",
        response_id=None,
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
    )

    matched_request = proxy_service._match_websocket_request_state_for_anonymous_event(
        deque([active_request, waiting_request]),
        prefer_previous_response_not_found=False,
        prefer_draining_requests=True,
    )

    assert matched_request is waiting_request

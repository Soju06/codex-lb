from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio
import pytest

from app.core.clients.proxy_websocket import UpstreamWebSocketMessage
from app.core.config.settings import Settings
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.http_bridge.request_submit import _HTTPBridgeLiveEventQueue

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_paused_enqueue_deadline_allows_shared_reader_to_settle_expired_sibling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    settings = Settings(
        http_responses_session_bridge_enabled=True,
        http_responses_session_bridge_request_budget_seconds=1.0,
        stream_idle_timeout_seconds=60.0,
    )
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(service, "_retry_http_bridge_precreated_request", AsyncMock(return_value=False))
    monkeypatch.setattr(service, "_handle_stream_error", AsyncMock())
    monkeypatch.setattr(service, "_write_request_log", AsyncMock())
    monkeypatch.setattr(service, "_retire_stale_pending_http_bridge_session", AsyncMock())
    monkeypatch.setattr(service, "_retire_http_bridge_after_drain_if_ready", AsyncMock(return_value=False))

    frames: asyncio.Queue[UpstreamWebSocketMessage] = asyncio.Queue()
    upstream = SimpleNamespace(receive=frames.get, close=AsyncMock())
    paused_queue = _HTTPBridgeLiveEventQueue(maxsize=2, revoked=asyncio.Event())
    paused_queue.put_nowait("buffered-one")
    paused_queue.put_nowait("buffered-two")
    sibling_queue: asyncio.Queue[str | None] = asyncio.Queue()
    now = time.monotonic()
    paused = proxy_service._WebSocketRequestState(
        request_id="paused",
        model="gpt-5.5",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=now,
        transport="http",
        response_id="resp-paused",
        event_queue=paused_queue,
        event_queue_revoked=paused_queue.revoked,
        event_queue_consumer_started=True,
        bridge_request_deadline=now + 0.05,
        skip_request_log=True,
    )
    sibling = proxy_service._WebSocketRequestState(
        request_id="sibling",
        model="gpt-5.5",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=now,
        transport="http",
        response_id="resp-sibling",
        event_queue=sibling_queue,
        event_queue_consumer_started=True,
        skip_request_log=True,
    )
    session = proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "deadline-test", None),
        headers={},
        affinity=proxy_service._AffinityPolicy(key="deadline-test", kind=proxy_service.StickySessionKind.CODEX_SESSION),
        request_model="gpt-5.5",
        account=cast(Any, SimpleNamespace(id="acc-deadline", chatgpt_account_id=None)),
        upstream=cast(Any, upstream),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque([paused, sibling]),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=2,
        last_used_at=now,
        idle_ttl_seconds=60.0,
    )
    frames.put_nowait(
        UpstreamWebSocketMessage(
            kind="text",
            text=json.dumps({"type": "response.output_text.delta", "response_id": "resp-paused", "delta": "x"}),
        )
    )
    process_text = service._process_http_bridge_upstream_text

    async def process_with_expiring_sibling(session: Any, text: str) -> None:
        paused.bridge_request_deadline = time.monotonic() + 0.05
        sibling.started_at = time.monotonic() - 2.0
        await process_text(session, text)

    monkeypatch.setattr(service, "_process_http_bridge_upstream_text", process_with_expiring_sibling)

    # No downstream read is allowed to release the full queue. The real reader
    # must leave dispatch at the paused request's deadline and discover that
    # its sibling's budget expired while dispatch was blocked.
    reader = asyncio.create_task(service._relay_http_bridge_upstream_messages(session))
    try:
        await asyncio.wait_for(reader, timeout=1.0)
        terminal = await asyncio.wait_for(sibling_queue.get(), timeout=0.1)
        assert terminal is not None
        assert "response.failed" in terminal
        assert "request_timeout" in terminal
        assert await asyncio.wait_for(sibling_queue.get(), timeout=0.1) is None
        assert paused_queue.revoked.is_set()
        assert paused_queue.get_nowait() == "buffered-one"
        assert paused_queue.get_nowait() == "buffered-two"
        assert not session.pending_requests
        assert session.queued_request_count == 0
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        paused_queue.discard()

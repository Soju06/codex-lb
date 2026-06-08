from __future__ import annotations

import asyncio
import contextlib
import json
import time
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

pytestmark = pytest.mark.e2e


class _FakeUpstreamMessage:
    def __init__(self, kind: str, *, text: str | None = None, close_code: int | None = None) -> None:
        self.kind = kind
        self.text = text
        self.close_code = close_code
        self.error = None
        self.data = None


class _CancelThenRetryUpstreamWebSocket:
    def __init__(self) -> None:
        self.sent_text: list[str] = []
        self.closed = False
        self._messages: asyncio.Queue[_FakeUpstreamMessage] = asyncio.Queue()

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        if len(self.sent_text) == 1:
            await self._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.created",
                            "sequence_number": 0,
                            "response": {
                                "id": "resp_cancelled",
                                "object": "response",
                                "status": "in_progress",
                                "output": [],
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            )
            return

        if len(self.sent_text) == 2:
            # Late anonymous output from request #1. Without the drain barrier,
            # the HTTP bridge routes this into request #2 because request #2 is
            # the only unresolved pending request.
            await self._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.output_item.added",
                            "sequence_number": 1,
                            "output_index": 0,
                            "item": {
                                "id": "msg_orphan_from_cancelled_request",
                                "type": "message",
                                "role": "assistant",
                                "status": "in_progress",
                                "content": [],
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            )
            await self._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.created",
                            "sequence_number": 0,
                            "response": {
                                "id": "resp_retry",
                                "object": "response",
                                "status": "in_progress",
                                "output": [],
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            )
            await self._messages.put(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.completed",
                            "sequence_number": 1,
                            "response": {
                                "id": "resp_retry",
                                "object": "response",
                                "status": "completed",
                                "output": [
                                    {
                                        "id": "msg_retry",
                                        "type": "message",
                                        "role": "assistant",
                                        "status": "completed",
                                        "content": [{"type": "output_text", "text": "retry ok"}],
                                    }
                                ],
                            },
                        },
                        separators=(",", ":"),
                    ),
                )
            )
            return

        raise AssertionError(f"unexpected send #{len(self.sent_text)}: {text}")

    async def send_bytes(self, data: bytes) -> None:
        raise AssertionError(f"Unexpected binary frame: {data!r}")

    async def receive(self) -> _FakeUpstreamMessage:
        return await self._messages.get()

    async def close(self) -> None:
        self.closed = True

    def response_header(self, name: str) -> str | None:
        del name
        return None


def _make_request_state(request_id: str) -> proxy_service._WebSocketRequestState:
    return proxy_service._WebSocketRequestState(
        request_id=request_id,
        model="gpt-5.5",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=time.monotonic(),
        awaiting_response_created=True,
        event_queue=asyncio.Queue(),
        transport="http",
        request_text='{"model":"gpt-5.5","input":"hi","stream":true}',
        skip_request_log=True,
    )


def _make_session(upstream: _CancelThenRetryUpstreamWebSocket) -> proxy_service._HTTPBridgeSession:
    return proxy_service._HTTPBridgeSession(
        key=proxy_service._HTTPBridgeSessionKey("session_header", "sid-cancel-retry-e2e", None),
        headers={"x-codex-session-id": "sid-cancel-retry-e2e"},
        affinity=proxy_service._AffinityPolicy(
            key="sid-cancel-retry-e2e",
            kind=proxy_service.StickySessionKind.CODEX_SESSION,
        ),
        request_model="gpt-5.5",
        account=cast(Any, SimpleNamespace(id="acc-cancel-retry-e2e", status=AccountStatus.ACTIVE)),
        upstream=cast(UpstreamResponsesWebSocket, upstream),
        upstream_control=proxy_service._WebSocketUpstreamControl(),
        pending_requests=deque(),
        pending_lock=anyio.Lock(),
        response_create_gate=asyncio.Semaphore(1),
        queued_request_count=0,
        last_used_at=1.0,
        idle_ttl_seconds=120.0,
    )


@pytest.mark.asyncio
async def test_cancelled_http_bridge_stream_retires_before_retry_can_share_upstream() -> None:
    service = proxy_service.ProxyService(cast(Any, nullcontext()))
    service._finalize_websocket_request_state = cast(Any, AsyncMock())
    upstream = _CancelThenRetryUpstreamWebSocket()
    session = _make_session(upstream)
    service._http_bridge_sessions[session.key] = session
    session.upstream_reader = asyncio.create_task(service._relay_http_bridge_upstream_messages(session))

    first_request = _make_request_state("req-cancelled")
    first_stream = service._stream_http_bridge_session_events(
        session,
        request_state=first_request,
        text_data=first_request.request_text or "{}",
        queue_limit=8,
        propagate_http_errors=True,
        downstream_turn_state=None,
    )
    try:
        first_block = await asyncio.wait_for(first_stream.__anext__(), timeout=2.0)
        first_payload = proxy_service.parse_sse_data_json(first_block)
        assert isinstance(first_payload, dict)
        assert first_payload["type"] == "response.created"

        # Simulate downstream cancellation before request #1 reaches terminal.
        # The bridge must retire the shared upstream instead of sending a retry
        # behind the unterminated upstream response and guessing anonymous-frame
        # ownership.
        await first_stream.aclose()
    finally:
        if session.upstream_reader is not None:
            session.upstream_reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session.upstream_reader

    assert upstream.sent_text == [first_request.request_text]
    assert upstream.closed is True
    assert session.closed is True
    assert session.upstream_control.retire_after_drain is True
    assert not session.pending_requests

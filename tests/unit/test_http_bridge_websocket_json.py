from __future__ import annotations

import json
from collections import deque
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import anyio
import pytest

from app.core.clients.native_egress import NativeWebSocketRoutingMetadata
from app.core.clients.proxy_websocket import UpstreamWebSocketMessage
from app.core.clock import REAL_CLOCK, REAL_SCHEDULER
from app.core.types import JsonValue
from app.core.utils.sse import parse_sse_data_json
from app.modules.proxy._service.http_bridge.upstream_events import _HTTPBridgeUpstreamEventsMixin

pytestmark = pytest.mark.unit

ERROR: dict[str, JsonValue] = {
    "type": "error",
    "status": 400,
    "error": {
        "type": "invalid_request_error",
        "code": "unsupported_value",
        "param": "parallel_tool_calls",
        "message": "Responses-Lite requires parallel_tool_calls=false.",
    },
}


def _frame_text(payload: dict[str, JsonValue], style: str) -> str:
    if style == "compact":
        return json.dumps(payload, separators=(",", ":"))
    text = json.dumps(payload, indent=2)
    if style == "crlf":
        return text.replace("\n", "\r\n")
    if style == "leading-whitespace":
        return " \t\r\n" + text + "\r\n "
    return text


@pytest.mark.asyncio
@pytest.mark.parametrize("style", ["compact", "pretty", "crlf", "leading-whitespace"])
@pytest.mark.parametrize("native", [False, True], ids=["opaque", "native"])
@pytest.mark.parametrize(
    "payload",
    [
        ERROR,
        {"type": "response.created", "response": {"id": "resp_json", "status": "in_progress"}},
        {"type": "response.completed", "response": {"id": "resp_json", "status": "completed"}},
    ],
)
async def test_bridge_parses_complete_websocket_json_documents(
    payload: dict[str, JsonValue], style: str, native: bool
) -> None:
    text = _frame_text(payload, style)
    process = AsyncMock()
    harness = SimpleNamespace(_process_parsed_http_bridge_upstream_event=process)
    response = payload.get("response")
    response_id = response.get("id") if isinstance(response, dict) else None
    assert response_id is None or isinstance(response_id, str)
    message = UpstreamWebSocketMessage(
        kind="text",
        text=text,
        responses_interpreted=native,
        payload=payload if native else None,
        event_type=cast(str, payload["type"]) if native else None,
        routing=NativeWebSocketRoutingMetadata(response_id, None) if native else None,
    )

    await _HTTPBridgeUpstreamEventsMixin._process_http_bridge_upstream_text(
        harness, cast(Any, None), text, message=message, scheduler=REAL_SCHEDULER, clock=REAL_CLOCK
    )

    process.assert_awaited_once()
    assert process.await_args is not None
    parsed = process.await_args.kwargs
    assert parsed["payload"] == payload
    assert parsed["event_type"] == payload["type"]
    assert parsed["response_id"] == response_id
    assert parsed["text"] == text
    assert parse_sse_data_json(parsed["event_block"]) == payload


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", "[DONE]", "[]", "true", "1", "null", '{"type":', 'data: {"type":"error"}\n\n'])
async def test_bridge_non_object_messages_do_not_fabricate_events(text: str) -> None:
    process = AsyncMock()
    harness = SimpleNamespace(_process_parsed_http_bridge_upstream_event=process)
    archive = Mock()
    session = SimpleNamespace(
        pending_lock=anyio.Lock(), pending_requests=deque(), upstream=SimpleNamespace(archive_received=archive)
    )

    await _HTTPBridgeUpstreamEventsMixin._process_http_bridge_upstream_text(
        harness, cast(Any, session), text, scheduler=REAL_SCHEDULER, clock=REAL_CLOCK
    )

    process.assert_not_awaited()
    archive.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e400", "-1e400"])
@pytest.mark.parametrize("native", [False, True], ids=["opaque", "native"])
async def test_non_finite_frame_never_enters_downstream_relay(number: str, native: bool) -> None:
    text = '{"type":"response.output_text.delta","nested":{"values":[' + number + "]}}"
    payload = json.loads(text)
    process, archive = AsyncMock(), Mock()
    harness = SimpleNamespace(_process_parsed_http_bridge_upstream_event=process)
    pending = SimpleNamespace(archive_request_id="rejected-frame-test")
    session = SimpleNamespace(
        pending_lock=anyio.Lock(), pending_requests=deque([pending]), upstream=SimpleNamespace(archive_received=archive)
    )
    message = UpstreamWebSocketMessage(
        kind="text",
        text=text,
        responses_interpreted=native,
        payload=payload if native else None,
        event_type="response.output_text.delta" if native else None,
        routing=NativeWebSocketRoutingMetadata(None, None) if native else None,
    )

    await _HTTPBridgeUpstreamEventsMixin._process_http_bridge_upstream_text(
        harness, cast(Any, session), text, message=message, scheduler=REAL_SCHEDULER, clock=REAL_CLOCK
    )

    process.assert_not_awaited()
    assert list(session.pending_requests) == [pending]
    archive.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True], ids=["opaque", "native"])
async def test_finite_numbers_and_nonfinite_words_in_strings_are_preserved(native: bool) -> None:
    payload: dict[str, JsonValue] = {
        "type": "response.output_text.delta",
        "delta": "NaN Infinity -Infinity",
        "nested": {"values": [1.5, -0.0, 1e300, 10**100, True, None]},
    }
    process = AsyncMock()
    harness = SimpleNamespace(_process_parsed_http_bridge_upstream_event=process)
    text = json.dumps(payload, indent=2)
    message = UpstreamWebSocketMessage(
        kind="text",
        text=text,
        responses_interpreted=native,
        payload=payload if native else None,
        event_type="response.output_text.delta" if native else None,
        routing=NativeWebSocketRoutingMetadata(None, None) if native else None,
    )

    await _HTTPBridgeUpstreamEventsMixin._process_http_bridge_upstream_text(
        harness, cast(Any, None), text, message=message, scheduler=REAL_SCHEDULER, clock=REAL_CLOCK
    )

    process.assert_awaited_once()
    assert process.await_args is not None
    assert process.await_args.kwargs["payload"] == payload
    assert parse_sse_data_json(process.await_args.kwargs["event_block"]) == payload

"""Chunked websocket text IPC reassembly (`websocket_text_chunking_v1`).

The helper splits an upstream websocket message that would exceed one bounded
IPC line into ``websocket_text`` events whose ``text`` fields concatenate to
the message; the pump must reassemble them exactly, keep the single-event
shape for small messages, and fail closed when any other event interrupts a
chunk sequence.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.core.clients.native_egress import (
    NativeEgressProtocolError,
    NativeEgressWebSocket,
    SubprocessNativeEgressClient,
)

pytestmark = pytest.mark.unit


async def _noop_abort(*args: object, **kwargs: object) -> None:
    return None


def _make_websocket(events: list[dict[str, object] | BaseException]) -> NativeEgressWebSocket:
    queue: asyncio.Queue[dict[str, object] | BaseException] = asyncio.Queue()
    for event in events:
        queue.put_nowait(event)
    client = cast(
        SubprocessNativeEgressClient,
        SimpleNamespace(
            _finish_request=lambda *_args, **_kwargs: None,
            _abort_request=_noop_abort,
        ),
    )
    return NativeEgressWebSocket(
        status=101,
        headers=(),
        client=client,
        process=cast(Any, SimpleNamespace()),
        request_id="ws-chunk-test",
        generation=1,
        events=queue,
    )


def _chunk(text: str, more: bool) -> dict[str, object]:
    event: dict[str, object] = {"type": "websocket_text", "request_id": "ws-chunk-test", "text": text}
    if more:
        event["more"] = True
    return event


@pytest.mark.asyncio
async def test_chunk_sequence_reassembles_into_one_message() -> None:
    # A multi-chunk upstream message crosses the IPC boundary as several
    # bounded chunks; the wrapper's consumer sees exactly one complete
    # message, and the peer close still follows it.
    payload = "x" * (5 * 1024 * 1024) + "é" * 10 + "y" * 1024
    websocket = _make_websocket(
        [
            _chunk(payload[:3_145_728], True),
            _chunk(payload[3_145_728 : 2 * 3_145_728], True),
            _chunk(payload[2 * 3_145_728 :], False),
            {"type": "websocket_close", "request_id": "ws-chunk-test", "code": 1000, "reason": ""},
        ]
    )
    message = await websocket.receive()
    assert message.kind == "text"
    assert message.text == payload
    close = await websocket.receive()
    assert close.kind == "close"
    with pytest.raises(Exception, match="native websocket closed"):
        await websocket.receive()


@pytest.mark.asyncio
async def test_small_message_keeps_the_single_event_shape() -> None:
    # Default compatibility: a message that fits one IPC line arrives as a
    # single event with no ``more`` field and is delivered unchanged.
    websocket = _make_websocket([_chunk("hello", False)])
    message = await websocket.receive()
    assert message.kind == "text"
    assert message.text == "hello"


@pytest.mark.asyncio
async def test_invalid_more_flag_is_a_protocol_error() -> None:
    websocket = _make_websocket([{"type": "websocket_text", "request_id": "r", "text": "x", "more": "yes"}])
    with pytest.raises(NativeEgressProtocolError, match="text event is invalid"):
        await websocket.receive()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "interrupting",
    [
        pytest.param({"type": "websocket_binary", "request_id": "r", "data": "AAAA"}, id="binary"),
        pytest.param({"type": "websocket_close", "request_id": "r", "code": 1000, "reason": ""}, id="close"),
        pytest.param({"type": "cancelled", "request_id": "r"}, id="cancelled"),
        pytest.param(
            {
                "type": "websocket_responses_text",
                "request_id": "r",
                "text": "{}",
                "event_type": None,
                "payload": {},
                "payload_response_id": None,
                "sequence_number": None,
            },
            id="responses_event",
        ),
    ],
)
async def test_interrupted_chunk_sequence_fails_closed(interrupting: dict[str, object]) -> None:
    # One message's chunks arrive back to back; any other event with
    # fragments pending is a protocol violation and must fail the stream
    # instead of silently truncating the message.
    events: list[dict[str, object] | BaseException] = [_chunk("part-", True), interrupting]
    websocket = _make_websocket(events)
    with pytest.raises(NativeEgressProtocolError, match="interrupted a chunked text message"):
        await websocket.receive()


@pytest.mark.asyncio
async def test_send_ack_between_messages_does_not_disturb_reassembly() -> None:
    # Acknowledgements may interleave BETWEEN messages (never within one);
    # fragments are empty then, so reassembly of the next message is exact.
    websocket = _make_websocket(
        [
            _chunk("first", False),
            {"type": "websocket_sent", "request_id": "ws-chunk-test", "command_id": "ws-chunk-test:1"},
            _chunk("sec", True),
            _chunk("ond", False),
        ]
    )
    first = await websocket.receive()
    second = await websocket.receive()
    assert (first.text, second.text) == ("first", "second")

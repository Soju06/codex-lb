"""Byte budget on completed websocket messages + chunked binary IPC.

Thread 1 (native_egress.py): the pump drains bounded helper events but queued
up to 64 reassembled messages near the 64 MiB bridge cap (~4 GiB) in
``_messages`` with only a count limit. A byte budget must trip first.

Thread 2 (websocket.rs): a single binary frame near the 64 MiB cap
base64-expands past the parent's 24 MiB readline limit. The helper now splits
it into ``websocket_binary{more: true}`` chunks the pump reassembles.
"""

from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.core.clients.native_egress import (
    NativeEgressProtocolError,
    NativeEgressTransportError,
    NativeEgressWebSocket,
    NativeWebSocketMessage,
    SubprocessNativeEgressClient,
)

pytestmark = pytest.mark.unit

_EXPECTED_MESSAGE_QUEUE_BYTES_LIMIT = 32 * 1024 * 1024


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
        request_id="ws-budget-test",
        generation=1,
        events=queue,
    )


def _binary_event(payload: bytes, more: bool) -> dict[str, object]:
    event: dict[str, object] = {
        "type": "websocket_binary",
        "request_id": "ws-budget-test",
        "data": base64.b64encode(payload).decode("ascii"),
    }
    if more:
        event["more"] = True
    return event


@pytest.mark.asyncio
async def test_completed_message_byte_budget_trips_before_count_limit() -> None:
    # 1 MiB text messages: the 64-slot count limit would admit 64 MiB, but the
    # 32 MiB byte budget must fail the pump first with consumer_backpressure.
    websocket = _make_websocket([])
    chunk = "x" * (1024 * 1024)
    with pytest.raises(NativeEgressTransportError, match="bounded message queue"):
        for _ in range(64):
            websocket._queue_message(NativeWebSocketMessage(kind="text", text=chunk))
    # Draining resets the budget so the next lone message fits again.
    assert websocket._messages_bytes <= _EXPECTED_MESSAGE_QUEUE_BYTES_LIMIT


@pytest.mark.asyncio
async def test_lone_large_message_accepted_on_empty_queue() -> None:
    # A single 40 MiB message past the 32 MiB budget is still accepted when the
    # consumer is caught up (empty queue): the message cap bounds it, and only
    # a *behind* consumer trips the budget.
    websocket = _make_websocket([])
    big = "y" * (40 * 1024 * 1024)
    websocket._queue_message(NativeWebSocketMessage(kind="text", text=big))
    assert websocket._messages_bytes == len(big.encode("utf-8"))


@pytest.mark.asyncio
async def test_binary_chunk_sequence_reassembles_into_one_message() -> None:
    payload = bytes(range(256)) * 4096 + b"tail-marker"
    part1, part2, part3 = payload[:1000], payload[1000:5000], payload[5000:]
    websocket = _make_websocket(
        [
            _binary_event(part1, True),
            _binary_event(part2, True),
            _binary_event(part3, False),
            {"type": "websocket_close", "request_id": "ws-budget-test", "code": 1000, "reason": ""},
        ]
    )
    message = await websocket.receive()
    assert message.kind == "binary"
    assert message.data == payload


@pytest.mark.asyncio
async def test_small_binary_keeps_single_event_shape() -> None:
    websocket = _make_websocket([_binary_event(b"hello-binary", False)])
    message = await websocket.receive()
    assert message.kind == "binary"
    assert message.data == b"hello-binary"
    assert getattr(websocket, "_binary_fragments", None) == []


@pytest.mark.asyncio
async def test_invalid_binary_more_flag_is_protocol_error() -> None:
    websocket = _make_websocket(
        [{"type": "websocket_binary", "request_id": "r", "data": "AAAA", "more": "yes"}]
    )
    with pytest.raises(NativeEgressProtocolError, match="binary event is invalid"):
        await websocket.receive()


@pytest.mark.asyncio
async def test_interrupted_binary_sequence_fails_closed() -> None:
    websocket = _make_websocket(
        [
            _binary_event(b"part-", True),
            {"type": "websocket_close", "request_id": "r", "code": 1000, "reason": ""},
        ]
    )
    with pytest.raises(NativeEgressProtocolError, match="interrupted a chunked binary message"):
        await websocket.receive()

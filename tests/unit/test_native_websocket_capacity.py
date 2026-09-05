from __future__ import annotations

import asyncio
import gc
from pathlib import Path

import pytest

import app.core.clients.native_egress as native
from app.core.clients.native_buffer import BufferBudget, BufferFull, ByteQueue, event_size
from tests.unit.test_native_egress import _write_helper


def _capacity_helper(tmp_path: Path) -> native.SubprocessNativeEgressClient:
    helper = tmp_path / "capacity-helper"
    _write_helper(
        helper,
        """#!/usr/bin/env python3
import json
import sys
for line in sys.stdin:
    cmd = json.loads(line)
    rid = cmd["request_id"]
    kind = cmd["type"]
    def emit(kind, **fields):
        print(json.dumps(dict(type=kind, request_id=rid, **fields)), flush=True)
    if kind == "websocket_connect":
        emit("websocket_open", status=101, headers=[])
    elif kind == "websocket_send_text":
        if cmd["text"] in ("burst", "flood"):
            for index in range(128):
                emit("websocket_text", text=str(index) + ("x" * 512 if cmd["text"] == "flood" else ""))
            emit("websocket_text", text='{"type":"response.completed"}')
        else:
            emit("websocket_text", text=cmd["text"])
        emit("websocket_sent", command_id=cmd["command_id"])
    elif kind == "websocket_close":
        emit("websocket_sent", command_id=cmd["command_id"])
        emit("websocket_close", code=1000, reason="")
    elif kind == "cancel":
        emit("cancelled")
""",
    )
    return native.SubprocessNativeEgressClient(helper)


async def _connect(client: native.SubprocessNativeEgressClient) -> native.NativeEgressWebSocket:
    return await client.websocket(
        native.NativeWebSocketRequest(
            url="wss://example.test/responses",
            headers={},
            connect_timeout_seconds=10,
            max_message_bytes=1024,
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [100, 300, 500])
async def test_concurrent_bursts_keep_acknowledgements_and_completion(tmp_path: Path, count: int) -> None:
    client = _capacity_helper(tmp_path)
    try:
        async with asyncio.timeout(30):
            sockets = await asyncio.gather(*(_connect(client) for _ in range(count)))
            # Consumers deliberately do not read until every send is acked.
            # A pump blocked on a full message queue would deadlock this step.
            await asyncio.gather(*(socket.send_text("burst") for socket in sockets))

            async def consume(socket: native.NativeEgressWebSocket) -> None:
                for index in range(128):
                    assert (await socket.receive()).text == str(index)
                assert (await socket.receive()).text == '{"type":"response.completed"}'

            await asyncio.gather(*(consume(socket) for socket in sockets))
            assert client._websocket_budget.used == 0
    finally:
        await client.aclose()
    assert not client._cancel_tasks
    assert client._websocket_budget.used == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["connection", "helper"])
async def test_byte_overflow_preserves_prefix_and_isolates_other_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scope: str,
) -> None:
    client = _capacity_helper(tmp_path)
    if scope == "connection":
        monkeypatch.setattr(native, "_NATIVE_WEBSOCKET_CONNECTION_BUFFER_BYTES", 4096)
    else:
        client._websocket_budget.limit = 4096
    try:
        async with asyncio.timeout(5):
            slow, fast = await _connect(client), await _connect(client)
            with pytest.raises(native.NativeEgressTransportError):
                await slow.send_text("flood")
            if scope == "connection":
                # Leave the failed slow socket's accepted prefix unread.
                await fast.send_text("independent")
                assert (await fast.receive()).text == "independent"
            accepted: list[str | None] = []
            with pytest.raises(native.NativeEgressTransportError) as failure:
                while True:
                    accepted.append((await slow.receive()).text)
            assert 0 < len(accepted) < 128
            assert accepted == [str(index) + "x" * 512 for index in range(len(accepted))]
            assert failure.value.failure_phase == "consumer_backpressure"
            assert failure.value.failure_detail is not None
            assert f"{scope}_limit_bytes=4096" in failure.value.failure_detail
            await fast.send_text("still healthy")
            assert (await fast.receive()).text == "still healthy"
            assert client._websocket_budget.used == 0
            await slow.close()
    finally:
        await client.aclose()
    assert client._websocket_budget.used == 0


@pytest.mark.asyncio
async def test_helper_shutdown_releases_unread_data(tmp_path: Path) -> None:
    client = _capacity_helper(tmp_path)
    socket = await _connect(client)
    await socket.send_text("burst")
    assert client._websocket_budget.used > 0
    await client.aclose()
    assert client._websocket_budget.used == 0
    assert socket._pump_task.done()
    assert not client._cancel_tasks


def test_raw_and_decoded_stages_share_a_byte_budget() -> None:
    shared = BufferBudget(2000)
    connection = BufferBudget(1000)
    raw: ByteQueue[str | BaseException] = ByteQueue(connection, shared, event_size)
    decoded: ByteQueue[str | BaseException] = ByteQueue(connection, shared, event_size)
    raw.put_nowait("x" * 400)
    with pytest.raises(BufferFull):
        decoded.put_nowait("y" * 400)
    decoded.put_nowait(RuntimeError("terminal"))
    assert raw.get_nowait() == "x" * 400
    assert isinstance(decoded.get_nowait(), RuntimeError)
    assert shared.used == connection.used == 0


@pytest.mark.asyncio
async def test_shutdown_wakes_receiver_already_waiting_on_empty_queue(tmp_path: Path) -> None:
    client = _capacity_helper(tmp_path)
    socket = await _connect(client)
    receiver = asyncio.create_task(socket.receive())
    await asyncio.sleep(0)
    try:
        await client.aclose()
        with pytest.raises(native.NativeEgressTransportError):
            await asyncio.wait_for(receiver, timeout=1)
        assert client._websocket_budget.used == 0
    finally:
        receiver.cancel()
        await asyncio.gather(receiver, return_exceptions=True)


def test_abandoned_queue_releases_shared_capacity() -> None:
    shared = BufferBudget(4096)
    queue = ByteQueue[object](BufferBudget(4096), shared, event_size)
    queue.put_nowait("buffered data")
    assert shared.used > 0
    del queue
    gc.collect()
    assert shared.used == 0

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiohttp
import pytest
from aiohttp._websocket.writer import WEBSOCKET_MAX_SYNC_CHUNK_SIZE
from fastapi import FastAPI
from websockets.asyncio.server import serve

from app.core.clients import proxy_websocket
from app.core.clients.proxy_websocket import ArchivingUpstreamWebSocket, CodexUpstreamWebSocket
from tests.integration.test_astra_steering_dispatch import _use_websocket_route
from tests.unit.test_astra_steering_protocol import ScriptedSocket, create, response, run_socket, saw

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("steer_failed", [False, True], ids=["active-steer", "rejected-steer"])
@pytest.mark.parametrize("terminal", ["response.completed", "response.failed"])
async def test_compressed_steering_create_owns_only_its_dispatched_lifecycle(
    app_instance: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    steer_failed: bool,
    terminal: str,
) -> None:
    # Exercise aiohttp's real shielded compression task and real socket write.
    # Automatic responses arrive before compression finishes; explicit responses
    # arrive after the peer reads the frame, while local flow control still waits.
    call = {"type": "function_call", "call_id": "tool", "name": "slow", "arguments": "{}"}
    result = {"type": "function_call_output", "call_id": "tool", "output": "saved " * WEBSOCKET_MAX_SYNC_CHUNK_SIZE}
    compression_entered = asyncio.Event()
    reader_processed_automatic = asyncio.Event()
    reader_processed_explicit = asyncio.Event()
    drain_finished = asyncio.Event()
    wire_frames = []
    states = []
    compression_tasks = []
    blocked_drains = []
    archive_path = tmp_path / "upstream-frames.jsonl"

    def archive_text(*, text, **_kwargs):
        # Keep the production archive wrapper and a real archive write, but avoid
        # starting the process-global archive worker for this transport test.
        with archive_path.open("a") as archive:
            archive.write(text + "\n")

    monkeypatch.setattr(proxy_websocket, "archive_text", archive_text)

    class Socket(ScriptedSocket):
        async def receive(self):
            if self.scripts:
                return await super().receive()
            await drain_finished.wait()
            return {"type": "websocket.disconnect"}

    socket = Socket(
        [
            (create(), lambda _: True),
            (
                {"type": "response.steer", "previous_response_id": "r1", "input": "Correction"},
                saw("response.created", "r1"),
            ),
            (create(parent="r1", input_items=[result]), saw("response.completed", "r1")),
        ]
    )

    async def upstream_server(connection):
        async for raw in connection:
            frame = json.loads(raw)
            wire_frames.append(frame)
            if len(wire_frames) == 1:
                await connection.send(json.dumps(response("response.created", "r1")))
            elif frame["type"] == "response.steer":
                for event in [
                    {"type": "response.steer.accepted", "steer": {"id": "s1", "previous_response_id": "r1"}},
                    response("response.completed", "r1", output=[call]),
                ]:
                    await connection.send(json.dumps(event))
                await asyncio.wait_for(compression_entered.wait(), timeout=2)
                if steer_failed:
                    await connection.send(
                        json.dumps(
                            {
                                "type": "response.steer.failed",
                                "steer": {"id": "s1", "previous_response_id": "r1"},
                                "error": {"code": "successor_creation_failed", "message": "Rejected"},
                            }
                        )
                    )
                for kind in ["response.created", "response.completed"]:
                    await connection.send(json.dumps(response(kind, "r-automatic", parent="r1")))
            else:
                final = response(terminal, "r-explicit", parent="r1")
                if terminal == "response.failed":
                    final["response"]["status"] = "failed"
                    final["response"]["error"] = {"code": "invalid_request_error", "message": "Invalid tool output"}
                for event in [response("response.created", "r-explicit", parent="r1"), final]:
                    await connection.send(json.dumps(event))

    async with serve(upstream_server, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        url = f"ws://127.0.0.1:{port}"
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(url, compress=15, heartbeat=None) as connection:
                writer = connection._writer
                assert writer.compress > 0
                protocol = writer.protocol
                compressor = writer._get_compressor(None)
                original_compress = compressor.compress
                original_drain = type(protocol)._drain_helper
                upstream = ArchivingUpstreamWebSocket(
                    CodexUpstreamWebSocket(connection),
                    url=url,
                    headers={},
                    account_id="acc_astra_protocol",
                    archive_payloads=True,
                )

                async def compress(data):
                    frame = json.loads(data)
                    assert frame["type"] == "response.create" and frame["previous_response_id"] == "r1"
                    assert len(data) > WEBSOCKET_MAX_SYNC_CHUNK_SIZE
                    compression_tasks.append(asyncio.current_task())
                    # Archiving has already written the explicit frame. The peer
                    # has received only the parent create and steer so far.
                    assert json.loads(archive_path.read_text().splitlines()[-1])["input"] == [result]
                    assert len(wire_frames) == 2
                    compression_entered.set()
                    await asyncio.wait_for(reader_processed_automatic.wait(), timeout=2)
                    assert len(wire_frames) == 2
                    # aiohttp otherwise avoids awaiting drain for this small
                    # compressed frame. Use its normal paused-protocol branch.
                    writer._limit = 1
                    protocol.pause_writing()
                    return await original_compress(data)

                async def drain(current_protocol):
                    if current_protocol is protocol:
                        blocked_drains.append(asyncio.current_task())
                        try:
                            await asyncio.wait_for(reader_processed_explicit.wait(), timeout=2)
                        finally:
                            if protocol._paused:
                                protocol.resume_writing()
                        await original_drain(current_protocol)
                        drain_finished.set()
                    else:
                        await original_drain(current_protocol)

                monkeypatch.setattr(compressor, "compress", compress)
                monkeypatch.setattr(type(protocol), "_drain_helper", drain)

                def configure(service, _account):
                    original_process = service._process_upstream_websocket_text
                    original_admit = service._acquire_request_state_response_create_admission

                    async def process(text, **kwargs):
                        value = await original_process(text, **kwargs)
                        event = json.loads(text)
                        if saw("response.completed", "r-automatic")([event]):
                            assert compression_entered.is_set() and len(wire_frames) == 2
                            reader_processed_automatic.set()
                        elif saw(terminal, "r-explicit")([event]):
                            assert not drain_finished.is_set() and len(wire_frames) == 3
                            reader_processed_explicit.set()
                        return value

                    async def admit(state, **kwargs):
                        states.append(state)
                        await original_admit(state, **kwargs)

                    monkeypatch.setattr(service, "_process_upstream_websocket_text", process)
                    monkeypatch.setattr(service, "_acquire_request_state_response_create_admission", admit)
                    _use_websocket_route(app_instance, monkeypatch, service, socket)

                try:
                    service, reservations, settled, released, logs = await run_socket(
                        monkeypatch, socket, upstream, configure=configure
                    )
                finally:
                    # aiohttp deliberately shields compression from cancellation.
                    # Release and join this connection's tasks even on assertion
                    # failure, before its transport or the peer server disappears.
                    reader_processed_automatic.set()
                    reader_processed_explicit.set()
                    if protocol._paused:
                        protocol.resume_writing()
                    if writer._background_tasks:
                        await asyncio.wait_for(asyncio.gather(*writer._background_tasks), timeout=2)

    assert len(compression_tasks) == 1 and all(task.done() for task in compression_tasks)
    assert len(blocked_drains) == 1 and compression_tasks[0] is not blocked_drains[0]
    assert drain_finished.is_set()
    assert [frame["type"] for frame in wire_frames] == ["response.create", "response.steer", "response.create"]
    assert wire_frames[-1]["input"] == [result]
    assert not saw("response.created", "r-automatic")(socket.sent)
    assert not saw("response.completed", "r-automatic")(socket.sent)
    assert saw("response.created", "r-explicit")(socket.sent)
    assert saw(terminal, "r-explicit")(socket.sent)
    assert len(reservations) == 3
    assert settled == [
        ("res_0", "success", 10, "r1"),
        ("res_2", "success" if terminal == "response.completed" else "error", 10, "r-explicit"),
    ]
    assert [call.args[0].reservation_id for call in released.await_args_list if call.args[0]] == ["res_1"]
    assert [row["request_id"] for row in logs.calls] == ["r1", "r-explicit"]
    assert all(state.response_create_admission is None and not state.response_create_gate_acquired for state in states)
    assert not service._background_cleanup_tasks

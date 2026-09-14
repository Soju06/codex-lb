from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from app.core.clients.proxy_websocket import WebsocketsUpstreamWebSocket
from app.dependencies import get_proxy_websocket_context
from app.modules.proxy import api as proxy_api
from app.modules.proxy._service.websocket import steering
from tests.unit.test_astra_steering_protocol import ScriptedSocket, create, response, run_socket, saw
from tests.unit.test_proxy_websocket_model_source_guard import _api_key

pytestmark = pytest.mark.integration


def _use_websocket_route(app_instance, monkeypatch, service, socket):
    original_proxy = service.proxy_responses_websocket

    async def route(*_args, **_kwargs):
        monkeypatch.setattr(service, "proxy_responses_websocket", original_proxy)
        app_instance.dependency_overrides[get_proxy_websocket_context] = lambda: SimpleNamespace(service=service)
        connected = False

        async def receive():
            nonlocal connected
            if not connected:
                connected = True
                return {"type": "websocket.connect"}
            return await socket.receive()

        async def send(message):
            if message["type"] == "websocket.send":
                await socket.send_text(message["text"])
            elif message["type"] == "websocket.close":
                await socket.close()

        await app_instance(
            {
                "type": "websocket",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "scheme": "ws",
                "path": "/backend-api/codex/responses",
                "root_path": "",
                "query_string": b"",
                "headers": [],
                "client": ("127.0.0.1", 12345),
                "server": ("testserver", 80),
                "subprotocols": [],
            },
            receive,
            send,
        )

    async def validate(*_args, **_kwargs):
        return _api_key(), None

    async def transport_denial():
        return None

    monkeypatch.setattr(proxy_api, "_validate_proxy_websocket_request", validate)
    monkeypatch.setattr(proxy_api, "_websocket_upstream_transport_denial", transport_denial)
    monkeypatch.setattr(service, "proxy_responses_websocket", route)


@pytest.mark.asyncio
@pytest.mark.parametrize("steer_failed", [False, True], ids=["active-steer", "rejected-steer"])
@pytest.mark.parametrize("terminal", ["response.completed", "response.failed", "error"])
@pytest.mark.parametrize(
    "automatic_terminal_kind,retire_history",
    [
        (kind, False)
        for kind in [
            None,
            "identified",
            "error",
            "typeless",
            "response.completed",
            "response.failed",
            "response.incomplete",
        ]
    ]
    + [("identified", True), ("error", True)],
)
async def test_explicit_steering_response_owns_usage_before_transport_drain_returns(
    app_instance: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    steer_failed: bool,
    terminal: str,
    automatic_terminal_kind: str | None,
    retire_history: bool,
) -> None:
    # The production websockets implementation writes the frame before drain().
    # Upstream can consume it and answer while that awaited flow control is blocked.
    call = {"type": "function_call", "call_id": "tool", "name": "slow", "arguments": "{}"}
    result = {"type": "function_call_output", "call_id": "tool", "output": "saved"}
    reader_processed_terminal = asyncio.Event()
    reader_processed_automatic = asyncio.Event()
    reader_retired = asyncio.Event()
    drain_finished = asyncio.Event()
    wire_frames = []
    states = []
    blocked_drains = []
    controls = []
    server_connection = None
    if retire_history:
        monkeypatch.setattr(steering, "_MAX_STEERING_HISTORY_IDS", 1)
    automatic_before_write = automatic_terminal_kind is not None
    if automatic_terminal_kind == "identified":
        automatic_terminal = response("response.completed", "r-auto", parent="r1")
    elif automatic_terminal_kind in {"error", "typeless"}:
        automatic_terminal = {"error": {"code": "server_error", "message": "Automatic successor failed"}}
        if automatic_terminal_kind == "error":
            automatic_terminal["type"] = "error"
    else:
        automatic_terminal = {"type": automatic_terminal_kind, "response": {"output": []}}

    def is_explicit_terminal(event):
        if terminal == "error":
            return event.get("error", {}).get("message") == "Invalid tool output"
        return saw(terminal, "r-explicit")([event])

    steer_rejection = {
        "type": "response.steer.failed",
        "steer": {"id": "s1", "previous_response_id": "r1"},
        "error": {"code": "successor_creation_failed", "message": "Rejected"},
    }

    class Socket(ScriptedSocket):
        async def receive(self):
            if self.scripts:
                return await super().receive()
            await drain_finished.wait()
            if retire_history:
                await reader_retired.wait()
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
        nonlocal server_connection
        server_connection = connection
        async for raw in connection:
            frame = json.loads(raw)
            wire_frames.append(frame)
            if len(wire_frames) == 1:
                events = [response("response.created", "r1")]
            elif frame["type"] == "response.steer":
                events = [
                    {"type": "response.steer.accepted", "steer": {"id": "s1", "previous_response_id": "r1"}},
                    response("response.completed", "r1", output=[call]),
                ]
            else:
                events = []
                if steer_failed and not automatic_before_write:
                    events.append(steer_rejection)
                error = {"code": "invalid_request_error", "message": "Invalid tool output"}
                if terminal == "error":
                    # An explicit request can fail after write but before created.
                    events.append({"type": "error", "error": error})
                else:
                    final = response(terminal, "r-explicit", parent="r1")
                    if terminal == "response.failed":
                        final["response"]["status"] = "failed"
                        final["response"]["error"] = error
                    events.extend([response("response.created", "r-explicit", parent="r1"), final])
            for event in events:
                await connection.send(json.dumps(event))

    async with serve(upstream_server, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        async with connect(f"ws://127.0.0.1:{port}", proxy=None, ping_interval=None) as connection:
            upstream = WebsocketsUpstreamWebSocket(connection)
            original_close = upstream.close
            original_send = connection.send
            original_drain = connection.drain
            sending_explicit = False

            async def close():
                if retire_history:
                    assert reader_processed_terminal.is_set()
                    assert controls[-1].reconnect_requested
                await original_close()
                reader_retired.set()

            async def send(data, **kwargs):
                nonlocal sending_explicit
                frame = json.loads(data)
                sending_explicit = frame.get("type") == "response.create" and frame.get("previous_response_id") == "r1"
                try:
                    if sending_explicit and automatic_before_write:
                        # Gate before the installed client's real send, while
                        # the same real peer delivers an automatic lifecycle.
                        assert server_connection is not None
                        if steer_failed:
                            await server_connection.send(json.dumps(steer_rejection))
                        await server_connection.send(json.dumps(response("response.created", "r-auto", parent="r1")))
                        await server_connection.send(json.dumps(automatic_terminal))
                        await asyncio.wait_for(reader_processed_automatic.wait(), timeout=2)
                        assert len(wire_frames) == 2
                        assert not states[-1].response_create_dispatched
                        assert states[-1].response_id is None
                    await original_send(data, **kwargs)
                finally:
                    sending_explicit = False

            async def drain():
                if sending_explicit:
                    blocked_drains.append(asyncio.current_task())
                    await asyncio.wait_for(reader_processed_terminal.wait(), timeout=2)
                    drain_finished.set()
                await original_drain()

            monkeypatch.setattr(connection, "send", send)
            monkeypatch.setattr(connection, "drain", drain)
            monkeypatch.setattr(upstream, "close", close)

            def configure(service, _account):
                original_process = service._process_upstream_websocket_text
                original_admit = service._acquire_request_state_response_create_admission

                async def process(text, **kwargs):
                    if not controls:
                        controls.append(kwargs["upstream_control"])
                    value = await original_process(text, **kwargs)
                    raw = json.loads(text)
                    if raw == automatic_terminal:
                        reader_processed_automatic.set()
                    if is_explicit_terminal(raw):
                        assert not drain_finished.is_set()
                        reader_processed_terminal.set()
                    return value

                async def admit(state, **kwargs):
                    states.append(state)
                    await original_admit(state, **kwargs)

                monkeypatch.setattr(service, "_process_upstream_websocket_text", process)
                monkeypatch.setattr(service, "_acquire_request_state_response_create_admission", admit)
                _use_websocket_route(app_instance, monkeypatch, service, socket)

            service, reservations, settled, released, logs = await run_socket(
                monkeypatch, socket, upstream, configure=configure
            )

    assert len(blocked_drains) == 1 and reader_processed_terminal.is_set()
    assert [frame["type"] for frame in wire_frames] == ["response.create", "response.steer", "response.create"]
    assert wire_frames[-1]["input"] == [result]
    assert saw("response.created", "r-explicit")(socket.sent) == (terminal != "error")
    assert any(is_explicit_terminal(event) for event in socket.sent)
    assert not any(event.get("response", {}).get("id") == "r-auto" for event in socket.sent)
    assert automatic_terminal not in socket.sent
    assert reader_processed_automatic.is_set() == automatic_before_write
    assert len(reservations) == 3
    explicit_log_id = states[-1].request_id if terminal == "error" else "r-explicit"
    assert [(entry[0], entry[1], entry[3]) for entry in settled] == [
        ("res_0", "success", "r1"),
        ("res_2", "success" if terminal == "response.completed" else "error", explicit_log_id),
    ]
    assert [call.args[0].reservation_id for call in released.await_args_list if call.args[0]] == ["res_1"]
    assert [row["request_id"] for row in logs.calls] == ["r1", explicit_log_id]
    assert all(state.response_create_admission is None and not state.response_create_gate_acquired for state in states)
    assert not service._background_cleanup_tasks
    if retire_history:
        assert controls[-1].retire_after_drain and reader_retired.is_set()

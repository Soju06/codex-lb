from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Literal

import anyio
import pytest
from fastapi import FastAPI

from app.dependencies import get_proxy_websocket_context
from app.modules.proxy import api as proxy_api
from tests.unit.test_astra_steering_protocol import ScriptedSocket, ScriptedUpstream, create, response, run_socket, saw
from tests.unit.test_proxy_websocket_model_source_guard import _api_key

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("checkpoint", ["prepare", "owner", "admission"])
@pytest.mark.parametrize("reader_event", ["rejected", "created"])
async def test_websocket_rejects_stale_explicit_steering_swap(
    app_instance: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    checkpoint: Literal["prepare", "owner", "admission"],
    reader_event: Literal["rejected", "created"],
) -> None:
    # Given an owned steering placeholder waiting for a tool result.
    steer = {"type": "response.steer", "previous_response_id": "r1", "input": "Correction"}
    result = {"type": "function_call_output", "call_id": "tool", "output": "saved"}
    call = {"type": "function_call", "call_id": "tool", "name": "slow", "arguments": "{}"}
    reader_processed = anyio.Event()

    class Socket(ScriptedSocket):
        async def send_text(self, text: str) -> None:
            await super().send_text(text)
            event = json.loads(text)
            if event["type"] == "response.steer.failed" or saw("response.created", "r-auto")([event]):
                reader_processed.set()

    socket = Socket(
        [
            (create(), lambda _: True),
            (steer, saw("response.created", "r1")),
            (create(parent="r1", input_items=[result]), saw("response.steer.pending")),
            (
                create(input_items="Unrelated"),
                lambda events: saw("response.failed")(events) or saw("response.completed", "r-explicit")(events),
            ),
        ]
    )
    socket.finish_when = lambda event: saw("response.completed", "r-after")([event])

    class Upstream(ScriptedUpstream):
        async def send_text(self, text: str) -> None:
            if self.events:
                await super().send_text(text)
                return
            payload = json.loads(text)
            self.sent.append(payload)
            response_id = "r-explicit" if payload.get("previous_response_id") == "r1" else "r-after"
            if response_id == "r-after" and reader_event == "created":
                self.messages.put_nowait(
                    SimpleNamespace(kind="text", text=json.dumps(response("response.completed", "r-auto", parent="r1")))
                )
            for kind in ("response.created", "response.completed"):
                self.messages.put_nowait(
                    SimpleNamespace(
                        kind="text",
                        text=json.dumps(response(kind, response_id, parent=payload.get("previous_response_id"))),
                    )
                )

    upstream = Upstream(
        [
            [response("response.created", "r1")],
            [
                {"type": "response.steer.accepted", "steer": {"id": "s1", "previous_response_id": "r1"}},
                response("response.completed", "r1", output=[call]),
                {
                    "type": "response.steer.pending",
                    "steer": {"id": "s1", "previous_response_id": "r1"},
                    "reason": "waiting_for_required_input",
                    "required_input": [{"type": "function_call_output", "call_id": "tool"}],
                },
            ],
        ]
    )
    suspended = False

    async def race() -> None:
        nonlocal suspended
        if suspended:
            return
        suspended = True
        event = (
            {
                "type": "response.steer.failed",
                "steer": {"id": "s1", "previous_response_id": "r1", "input": steer["input"]},
                "error": {"code": "successor_creation_failed", "message": "Rejected"},
            }
            if reader_event == "rejected"
            else response("response.created", "r-auto", parent="r1")
        )
        upstream.messages.put_nowait(SimpleNamespace(kind="text", text=json.dumps(event)))
        with anyio.fail_after(2):
            await reader_processed.wait()

    states = []

    def configure(service, _account):
        original_prepare = service._prepare_websocket_response_create_request
        original_owner = service._resolve_websocket_previous_response_owner
        original_admit = service._acquire_request_state_response_create_admission
        original_proxy = service.proxy_responses_websocket

        async def prepare(payload, **kwargs):
            if checkpoint == "prepare" and payload.get("previous_response_id") == "r1":
                await race()
            return await original_prepare(payload, **kwargs)

        async def owner(**kwargs):
            if checkpoint == "owner" and kwargs["previous_response_id"] == "r1":
                await race()
            return await original_owner(**kwargs)

        async def admit(state, **kwargs):
            states.append(state)
            if checkpoint == "admission" and state.previous_response_id == "r1" and state.request_text is not None:
                await race()
            return await original_admit(state, **kwargs)

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
        monkeypatch.setattr(service, "_prepare_websocket_response_create_request", prepare)
        monkeypatch.setattr(service, "_resolve_websocket_previous_response_owner", owner)
        monkeypatch.setattr(service, "_acquire_request_state_response_create_admission", admit)
        monkeypatch.setattr(service, "proxy_responses_websocket", route)

    # When the real upstream reader changes ownership at the selected await.
    _, reservations, settled, released, _ = await run_socket(monkeypatch, socket, upstream, configure=configure)

    # Then no stale explicit frame is dispatched and the connection still admits work.
    assert suspended and reader_processed.is_set()
    assert not any(
        frame.get("previous_response_id") == "r1" for frame in upstream.sent if frame["type"] == "response.create"
    )
    failures = [event for event in socket.sent if event["type"] == "response.failed"]
    assert [event["response"]["error"]["code"] for event in failures] == ["response_not_found"]
    assert len(reservations) == 4
    assert [(entry[0], entry[3]) for entry in settled] == (
        [("res_0", "r1"), ("res_3", "r-after")]
        if reader_event == "rejected"
        else [("res_0", "r1"), ("res_1", "r-auto"), ("res_3", "r-after")]
    )
    assert [call.args[0].reservation_id for call in released.await_args_list if call.args[0]] == (
        ["res_1", "res_2"] if reader_event == "rejected" else ["res_2"]
    )
    assert all(state.response_create_admission is None and not state.response_create_gate_acquired for state in states)

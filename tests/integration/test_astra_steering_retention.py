from __future__ import annotations

import json

import pytest
from fastapi import FastAPI

from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.proxy._service.support import _WebSocketUpstreamControl
from tests.integration.test_astra_steering_parent_id import _configure_route, _run_route
from tests.unit.test_astra_steering_protocol import ScriptedSocket, ScriptedUpstream, create, response, saw

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("parent_terminal", ["response.completed", "response.incomplete"])
async def test_completed_astra_releases_bodies_before_next_steer_and_tool_continuation(
    app_instance: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    parent_terminal: str,
) -> None:
    tools = [{"type": "function", "name": "tool", "parameters": {"type": "object", "properties": {}}}]
    settings = {"stream_id": "original-lane", "reasoning": {"effort": "high", "summary": "auto"}, "tools": tools}
    initial = {
        **create(
            input_items=[
                {"role": "user", "content": "large request " * 8192},
                {"type": "configuration_update", "reasoning": {"effort": "medium"}},
                {"type": "configuration_update", "reasoning": {"effort": "xhigh"}},
            ]
        ),
        **settings,
    }
    call = {"type": "function_call", "id": "fc", "call_id": "tool", "name": "tool", "arguments": "{}"}
    result = {"type": "function_call_output", "call_id": "tool", "output": "Done"}
    control: _WebSocketUpstreamControl | None = None
    released_at_terminal: dict[str, bool] = {}
    retained_settings: dict[str, dict] = {}
    prepared_efforts: list[str | None] = []

    class Socket(ScriptedSocket):
        async def send_text(self, text: str) -> None:
            event = json.loads(text)
            if event["type"] in {"response.completed", "response.incomplete"}:
                assert control is not None and control.last_completed_request is not None
                state = control.last_completed_request
                response_id = event["response"]["id"]
                assert state.response_id == response_id
                configuration = state.steering_configuration or {}
                released_at_terminal[response_id] = (
                    state.request_text is None
                    and state.fresh_upstream_request_text is None
                    and not state.fresh_upstream_request_is_retry_safe
                    and "input" not in configuration
                )
                retained_settings[response_id] = {
                    key: configuration.get(key) for key in ("reasoning", "stream_id", "tools")
                }
            await super().send_text(text)

    socket = Socket(
        [
            (initial, lambda _: True),
            (
                {"type": "response.steer", "previous_response_id": "r1", "input": "Correction"},
                saw(parent_terminal, "r1"),
            ),
            ({**create(parent="r1", input_items=[result]), **settings}, saw("response.steer.pending")),
            (
                {"type": "response.steer", "previous_response_id": "r2", "input": "Another correction"},
                saw("response.completed", "r2"),
            ),
        ]
    )
    socket.finish_when = lambda event: saw("response.completed", "r3")([event])
    upstream = ScriptedUpstream(
        [
            [response("response.created", "r1"), response(parent_terminal, "r1", output=[call])],
            [
                {"type": "response.steer.accepted", "steer": {"id": "s1", "previous_response_id": "r1"}},
                {
                    "type": "response.steer.pending",
                    "steer": {"id": "s1", "previous_response_id": "r1"},
                    "reason": "waiting_for_required_input",
                    "required_input": [{"type": "function_call_output", "call_id": "tool"}],
                },
            ],
            [response("response.created", "r2", parent="r1"), response("response.completed", "r2", parent="r1")],
            [
                {"type": "response.steer.accepted", "steer": {"id": "s2", "previous_response_id": "r2"}},
                response("response.created", "r3", parent="r2"),
                response("response.completed", "r3", parent="r2"),
            ],
        ]
    )
    service, api_key, reservations = await _configure_route(app_instance, monkeypatch, upstream)
    original_process = service._process_upstream_websocket_text
    original_prepare = service._prepare_response_bridge_request_state

    async def process(text, **kwargs):
        nonlocal control
        control = kwargs["upstream_control"]
        return await original_process(text, **kwargs)

    def prepare(*args, **kwargs):
        state, text = original_prepare(*args, **kwargs)
        prepared_efforts.append(state.reasoning_effort)
        return state, text

    monkeypatch.setattr(service, "_process_upstream_websocket_text", process)
    monkeypatch.setattr(service, "_prepare_response_bridge_request_state", prepare)
    await _run_route(app_instance, socket)
    assert await service.drain_persistence_tasks(timeout_seconds=2)
    assert len(upstream.sent) == 4
    assert upstream.sent[2]["input"] == [result]
    assert upstream.sent[2]["stream_id"] == "original-lane"
    assert prepared_efforts == ["high", "xhigh", "high", "high"]
    assert len(reservations) == 4
    async with SessionLocal() as session:
        repository = ApiKeysRepository(session)
        rows = [await repository.get_usage_reservation(item.reservation_id) for item in reservations]
        assert [row.status for row in rows if row is not None] == ["finalized", "released", "finalized", "finalized"]
        limits = await repository.get_limits_by_key(api_key.id)
        assert limits[0].current_value == 42
    assert released_at_terminal == {"r1": True, "r2": True, "r3": True}
    assert retained_settings["r1"] == {
        "reasoning": {"effort": "xhigh", "summary": "auto"},
        "stream_id": "original-lane",
        "tools": tools,
    }

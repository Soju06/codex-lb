from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocket

from app.core.types import JsonValue
from app.modules.api_keys.service import ApiKeyUsageReservationData
from app.modules.proxy import service as proxy_service
from app.modules.proxy.replay_safety import (
    responses_input_items_are_self_contained_fresh_replay,
    responses_input_suffix_matches_pending_tool_calls,
)
from tests.unit.test_proxy_http_bridge import _make_bridge_session
from tests.unit.test_proxy_utils import (
    _make_account,
    _make_proxy_settings,
    _repo_factory,
    _RequestLogsRecorder,
    _SettingsCache,
)
from tests.unit.test_proxy_websocket_model_source_guard import _api_key

pytestmark = pytest.mark.unit


def response(event: str, response_id: str, *, parent: str | None = None, output: list[dict] | None = None) -> dict:
    value = {
        "id": response_id,
        "model": "gpt-6-astra",
        "status": "completed",
        "output": output or [],
        "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
        "previous_response_id": parent,
    }
    return {"type": event, "response": value}


def create(*, parent: str | None = None, input_items: JsonValue = "Hi") -> dict:
    result = {"type": "response.create", "model": "gpt-6-astra", "instructions": "", "input": input_items}
    if parent is not None:
        result["previous_response_id"] = parent
    return result


class ScriptedSocket:
    def __init__(self, scripts: list[tuple[dict, Callable[[list[dict]], bool]]]) -> None:
        self.scripts = scripts
        self.sent: list[dict] = []
        self.changed = asyncio.Event()
        self.finished = asyncio.Event()
        self.finish_when = lambda event: event.get("type") in {"response.completed", "response.failed"}

    async def receive(self) -> dict:
        if not self.scripts:
            await self.finished.wait()
            return {"type": "websocket.disconnect"}
        frame, ready = self.scripts[0]
        while not ready(self.sent):
            self.changed.clear()
            await self.changed.wait()
        self.scripts.pop(0)
        return {"type": "websocket.receive", "text": json.dumps(frame)}

    async def send_text(self, text: str) -> None:
        value = json.loads(text)
        self.sent.append(value)
        self.changed.set()
        if not self.scripts and self.finish_when(value):
            self.finished.set()

    async def send_bytes(self, data: bytes) -> None:
        raise AssertionError(data)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.finished.set()
        self.changed.set()


class ScriptedUpstream:
    def __init__(self, events: list[list[dict]]) -> None:
        self.events = events
        self.messages = asyncio.Queue()
        self.sent: list[dict] = []

    def response_header(self, name: str) -> None:
        return None

    async def receive(self) -> SimpleNamespace:
        return await self.messages.get()

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))
        for event in self.events.pop(0):
            self.messages.put_nowait(SimpleNamespace(kind="text", text=json.dumps(event)))

    async def close(self) -> None:
        pass


def saw(kind: str, response_id: str | None = None) -> Callable[[list[dict]], bool]:
    return lambda events: any(
        event.get("type") == kind and (response_id is None or event.get("response", {}).get("id") == response_id)
        for event in events
    )


async def run_socket(monkeypatch, socket: ScriptedSocket, upstream: ScriptedUpstream):
    settings = _make_proxy_settings()
    settings.stream_idle_timeout_seconds = 300.0
    settings.proxy_downstream_websocket_idle_timeout_seconds = 120.0
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(logs))
    account = _make_account("acc_astra_async")
    monkeypatch.setattr(service, "_connect_proxy_websocket", AsyncMock(return_value=(account, upstream)))
    monkeypatch.setattr(service, "_resolve_compact_turn_state_owner", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_resolve_file_account_for_responses", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "_revalidate_open_websocket_account", AsyncMock(return_value=(account, None, None)))
    monkeypatch.setattr(service, "_refresh_websocket_api_key_policy", AsyncMock(side_effect=lambda key: key))
    reservations = []

    async def reserve(*args, **kwargs):
        item = ApiKeyUsageReservationData(
            reservation_id=f"res_{len(reservations)}", key_id="key_ws_guard", model="gpt-6-astra"
        )
        reservations.append(item)
        return item

    monkeypatch.setattr(service, "_reserve_websocket_api_key_usage", reserve)
    monkeypatch.setattr(service, "_settle_stream_api_key_usage", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "_release_websocket_reservation", AsyncMock())
    monkeypatch.setattr(service, "_start_request_state_api_key_reservation_heartbeat", lambda *args, **kwargs: None)
    monkeypatch.setattr(service._load_balancer, "record_success", AsyncMock())
    monkeypatch.setattr(service._load_balancer, "release_account_lease", AsyncMock())
    monkeypatch.setattr(service, "_acquire_account_response_create_lease_or_overload", AsyncMock(return_value=None))
    await asyncio.wait_for(
        service.proxy_responses_websocket(
            cast(WebSocket, socket),
            {},
            codex_session_affinity=True,
            openai_cache_affinity=False,
            api_key=_api_key(),
        ),
        timeout=5,
    )


@pytest.mark.asyncio
async def test_async_call_survives_intervening_turn_and_delayed_output(monkeypatch):
    call = {"type": "function_call", "name": "slow", "call_id": "a", "arguments": "{}", "async": True}
    sync = {"type": "custom_tool_call", "name": "sync", "call_id": "b", "input": "x"}
    output = {"type": "function_call_output", "call_id": "a", "output": "later result"}
    socket = ScriptedSocket(
        [
            (create(), lambda _: True),
            (create(parent="r1", input_items="Continue"), saw("response.completed", "r1")),
            (create(parent="r2", input_items=[output]), saw("response.completed", "r2")),
        ]
    )
    upstream = ScriptedUpstream(
        [
            [
                response("response.created", "r1"),
                {"type": "response.output_item.done", "response_id": "r1", "item": call},
                {"type": "response.output_item.done", "response_id": "r1", "item": sync},
                response("response.completed", "r1", output=[call, sync]),
            ],
            [response("response.created", "r2", parent="r1"), response("response.completed", "r2", parent="r1")],
            [response("response.created", "r3", parent="r2"), response("response.completed", "r3", parent="r2")],
        ]
    )
    await run_socket(monkeypatch, socket, upstream)
    second = upstream.sent[1]["input"]
    synthetic = [item for item in second if item.get("type") in {"function_call_output", "custom_tool_call_output"}]
    assert [item["call_id"] for item in synthetic] == ["b"]
    assert upstream.sent[2]["input"] == [output]


def test_durable_manifest_keeps_synchronous_calls_when_async_is_pending() -> None:
    from app.modules.proxy._service.http_bridge import upstream_events as upstream_events_module
    from app.modules.proxy._service.support import record_async_tool_call

    state = proxy_service._WebSocketRequestState(
        request_id="req-mixed-manifest",
        model="gpt-6-astra",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        pending_tool_call_types={"async_1": "function_call", "sync_1": "function_call"},
        added_tool_call_types={"async_1": "function_call", "sync_1": "function_call"},
    )
    async_call = {"type": "function_call", "call_id": "async_1", "name": "slow", "arguments": "{}", "async": True}
    sync_call = {"type": "function_call", "call_id": "sync_1", "name": "now", "arguments": "{}"}
    record_async_tool_call(
        state,
        {"type": "response.completed", "response": {"output": [async_call, sync_call]}},
    )

    assert upstream_events_module._durable_pending_tool_call_manifest(
        state,
        {"type": "response.completed", "response": {"output": [async_call, sync_call]}},
    ) == {"sync_1": "function_call"}


def test_durable_manifest_omits_async_only_pending_calls() -> None:
    from app.modules.proxy._service.http_bridge import upstream_events as upstream_events_module
    from app.modules.proxy._service.support import record_async_tool_call

    state = proxy_service._WebSocketRequestState(
        request_id="req-async-only-manifest",
        model="gpt-6-astra",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        pending_tool_call_types={"async_1": "function_call"},
        added_tool_call_types={"async_1": "function_call"},
    )
    async_call = {"type": "function_call", "call_id": "async_1", "name": "slow", "arguments": "{}", "async": True}
    record_async_tool_call(state, {"type": "response.completed", "response": {"output": [async_call]}})

    assert (
        upstream_events_module._durable_pending_tool_call_manifest(
            state,
            {"type": "response.completed", "response": {"output": [async_call]}},
        )
        is None
    )


def test_pending_tools_reset_when_durable_anchor_owner_changes() -> None:
    from app.modules.proxy._service.http_bridge.streaming import _http_bridge_reset_pending_tools_for_anchor

    session = _make_bridge_session(key_value="owner-reset")
    session.last_completed_response_id = "resp-shared"
    session.last_completed_response_account_id = "acc-old"
    session.last_pending_tool_calls = {"sync_1": "function_call", "async_1": "function_call"}
    session.pending_async_tool_calls = {"async_1": "function_call"}

    _http_bridge_reset_pending_tools_for_anchor(session, response_id="resp-shared", account_id="acc-old")
    assert session.last_pending_tool_calls == {"sync_1": "function_call", "async_1": "function_call"}
    assert session.pending_async_tool_calls == {"async_1": "function_call"}

    _http_bridge_reset_pending_tools_for_anchor(session, response_id="resp-shared", account_id="acc-new")
    assert session.last_pending_tool_calls == {}
    assert session.pending_async_tool_calls == {}


def test_account_neutral_replay_accepts_unsettled_async_function_call() -> None:
    items: list[JsonValue] = [
        {"type": "function_call", "call_id": "async_1", "name": "slow", "arguments": "{}", "async": True},
        {"type": "function_call", "call_id": "sync_1", "name": "now", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "sync_1", "output": "ok"},
    ]
    assert responses_input_items_are_self_contained_fresh_replay(items)


def test_durable_suffix_ignores_async_calls_when_matching_sync_manifest() -> None:
    stored: list[JsonValue] = [{"role": "user", "content": "first"}]
    suffix: list[JsonValue] = [
        {"type": "function_call", "call_id": "async_1", "name": "slow", "arguments": "{}", "async": True},
        {"type": "function_call", "call_id": "sync_1", "name": "now", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "sync_1", "output": "ok"},
    ]
    assert responses_input_suffix_matches_pending_tool_calls(
        [*stored, *suffix],
        stored_count=1,
        pending_tool_calls={"sync_1": "function_call"},
    )

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import replace
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocket

from app.core.types import JsonValue
from app.modules.api_keys.service import ApiKeyUsageReservationData
from app.modules.proxy import service as proxy_service
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
    if event == "response.incomplete":
        value["incomplete_details"] = {"reason": "steered"}
        value["status"] = "incomplete"
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
        self.finish_when = lambda event: (
            event.get("type") in {"response.completed", "response.failed", "response.steer.failed"}
        )

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


async def run_socket(
    monkeypatch, socket: ScriptedSocket, upstream: ScriptedUpstream, *, configure=None, cancelled=False
):
    settings = _make_proxy_settings()
    settings.stream_idle_timeout_seconds = 300.0
    settings.proxy_downstream_websocket_idle_timeout_seconds = 120.0
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))
    logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(logs))
    account = _make_account("acc_astra_protocol")
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
    monkeypatch.setattr(service, "_extend_websocket_api_key_usage", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "_reduce_websocket_api_key_usage", AsyncMock(return_value=True))
    settled = []

    async def settle(key, reservation, settlement, request_id, **kwargs):
        settled.append((reservation.reservation_id, settlement.status, settlement.input_tokens, request_id))
        return True

    monkeypatch.setattr(service, "_settle_stream_api_key_usage", settle)
    released = AsyncMock()
    monkeypatch.setattr(service, "_release_websocket_reservation", released)
    monkeypatch.setattr(service, "_start_request_state_api_key_reservation_heartbeat", lambda *args, **kwargs: None)
    monkeypatch.setattr(service._load_balancer, "record_success", AsyncMock())
    monkeypatch.setattr(service._load_balancer, "release_account_lease", AsyncMock())
    monkeypatch.setattr(service, "_acquire_account_response_create_lease_or_overload", AsyncMock(return_value=None))
    if configure is not None:
        configure(service, account)
    operation = asyncio.wait_for(
        service.proxy_responses_websocket(
            cast(WebSocket, socket),
            {},
            codex_session_affinity=True,
            openai_cache_affinity=False,
            api_key=_api_key(),
        ),
        timeout=5,
    )
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            await operation
    else:
        await operation
    return service, reservations, settled, released, logs


@pytest.mark.asyncio
async def test_steering_automatic_successor_settles_both_responses_once(monkeypatch):
    steer = {"type": "response.steer", "previous_response_id": "r1", "input": "A correction"}
    socket = ScriptedSocket([(create(), lambda _: True), (steer, saw("response.created", "r1"))])
    upstream = ScriptedUpstream(
        [
            [response("response.created", "r1")],
            [
                {"type": "response.steer.accepted", "steer": {"id": "s1", "previous_response_id": "r1"}},
                response("response.incomplete", "r1"),
                response("response.created", "r2", parent="r1"),
                response("response.completed", "r2", parent="r1"),
            ],
        ]
    )
    _, reservations, settled, released, logs = await run_socket(monkeypatch, socket, upstream)
    assert [frame["type"] for frame in upstream.sent] == ["response.create", "response.steer"]
    assert len(reservations) == 2
    assert [(value[0], value[1], value[2]) for value in settled] == [("res_0", "success", 10), ("res_1", "success", 10)]
    assert len(logs.calls) == 2
    assert {row["request_id"] for row in logs.calls} == {"r1", "r2"}


@pytest.mark.asyncio
async def test_multiple_queued_steers_share_one_successor_reservation(monkeypatch):
    first = {"type": "response.steer", "previous_response_id": "r1", "input": "First correction"}
    second = {**first, "input": "Second correction"}
    socket = ScriptedSocket(
        [
            (create(), lambda _: True),
            (first, saw("response.created", "r1")),
            (second, saw("response.steer.accepted")),
        ]
    )
    upstream = ScriptedUpstream(
        [
            [response("response.created", "r1")],
            [{"type": "response.steer.accepted", "steer": {"id": "s1", "previous_response_id": "r1"}}],
            [
                {"type": "response.steer.accepted", "steer": {"id": "s2", "previous_response_id": "r1"}},
                response("response.completed", "r1"),
                response("response.created", "r2", parent="r1"),
                response("response.completed", "r2", parent="r1"),
            ],
        ]
    )
    socket.finish_when = lambda event: event.get("type") == "response.completed" and event["response"]["id"] == "r2"
    service, reservations, settled, _, _ = await run_socket(monkeypatch, socket, upstream)
    assert len(reservations) == 2
    assert len(settled) == 2
    assert [item["input"] for item in upstream.sent[1:]] == ["First correction", "Second correction"]
    service._extend_websocket_api_key_usage.assert_awaited_once()
    assert service._extend_websocket_api_key_usage.await_args.args[0].reservation_id == "res_1"


@pytest.mark.asyncio
async def test_first_steer_on_migrated_parent_does_not_extend_its_new_reservation(monkeypatch):
    steer = {"type": "response.steer", "previous_response_id": "r1", "input": "Correction"}
    socket = ScriptedSocket([(create(), lambda _: True), (steer, saw("response.created", "r1"))])
    upstream = ScriptedUpstream(
        [
            [response("response.created", "r1")],
            [
                {"type": "response.steer.accepted", "steer": {"id": "s1", "previous_response_id": "r1"}},
                response("response.incomplete", "r1"),
                response("response.created", "r2", parent="r1"),
                response("response.completed", "r2", parent="r1"),
            ],
        ]
    )

    def configure(service, account):
        del account
        original = service._prepare_response_bridge_request_state
        prepared = 0

        def prepare(*args, **kwargs):
            nonlocal prepared
            state, text = original(*args, **kwargs)
            prepared += 1
            if prepared == 1:
                assert state.request_text is not None
                state.steering_configuration = json.loads(state.request_text)
                state.request_text = None
            return state, text

        monkeypatch.setattr(service, "_prepare_response_bridge_request_state", prepare)

    service, reservations, settled, _, _ = await run_socket(monkeypatch, socket, upstream, configure=configure)
    assert len(reservations) == 2
    assert len(settled) == 2
    service._extend_websocket_api_key_usage.assert_not_awaited()


@pytest.mark.asyncio
async def test_second_queued_steer_quota_rejection_happens_before_upstream_send(monkeypatch):
    from app.core.exceptions import ProxyRateLimitError

    first = {"type": "response.steer", "previous_response_id": "r1", "input": "First correction"}
    second = {**first, "input": "Second correction"}
    socket = ScriptedSocket(
        [
            (create(), lambda _: True),
            (first, saw("response.created", "r1")),
            (second, saw("response.steer.accepted")),
        ]
    )
    upstream = ScriptedUpstream(
        [
            [response("response.created", "r1")],
            [{"type": "response.steer.accepted", "steer": {"id": "s1", "previous_response_id": "r1"}}],
        ]
    )

    def configure(service, account):
        del account
        monkeypatch.setattr(
            service,
            "_extend_websocket_api_key_usage",
            AsyncMock(side_effect=ProxyRateLimitError("queued steer exceeds quota")),
        )

    service, reservations, _, _, _ = await run_socket(monkeypatch, socket, upstream, configure=configure)
    assert len(reservations) == 2
    assert [item["input"] for item in upstream.sent[1:]] == ["First correction"]
    service._extend_websocket_api_key_usage.assert_awaited_once()
    assert socket.sent[-1]["type"] == "response.steer.failed"
    assert socket.sent[-1]["error"]["code"] == "invalid_input"


@pytest.mark.asyncio
async def test_failed_queued_steer_returns_only_its_shared_reservation_budget(monkeypatch):
    first = {"type": "response.steer", "previous_response_id": "r1", "input": "First correction"}
    second = {**first, "input": "Second correction"}
    socket = ScriptedSocket(
        [
            (create(), lambda _: True),
            (first, saw("response.created", "r1")),
            (second, saw("response.steer.accepted")),
        ]
    )
    upstream = ScriptedUpstream(
        [
            [response("response.created", "r1")],
            [{"type": "response.steer.accepted", "steer": {"id": "s1", "previous_response_id": "r1"}}],
            [
                {"type": "response.steer.accepted", "steer": {"id": "s2", "previous_response_id": "r1"}},
                {
                    "type": "response.steer.failed",
                    "steer": {"id": "s1", "previous_response_id": "r1", "input": "First correction"},
                    "error": {"code": "invalid_input", "message": "Rejected"},
                },
                response("response.completed", "r1"),
                response("response.created", "r2", parent="r1"),
                response("response.completed", "r2", parent="r1"),
            ],
        ]
    )
    socket.finish_when = lambda event: event.get("type") == "response.completed" and event["response"]["id"] == "r2"
    service, reservations, settled, released, _ = await run_socket(monkeypatch, socket, upstream)
    assert len(reservations) == 2
    assert len(settled) == 2
    released.assert_not_awaited()
    service._reduce_websocket_api_key_usage.assert_awaited_once()
    assert service._reduce_websocket_api_key_usage.await_args.args[0].reservation_id == "res_1"


@pytest.mark.asyncio
async def test_failed_steer_reduction_error_does_not_abort_other_responses(monkeypatch, caplog):
    first = {"type": "response.steer", "previous_response_id": "r1", "input": "First correction"}
    second = {**first, "input": "Second correction"}
    socket = ScriptedSocket(
        [
            (create(), lambda _: True),
            (first, saw("response.created", "r1")),
            (second, saw("response.steer.accepted")),
        ]
    )
    upstream = ScriptedUpstream(
        [
            [response("response.created", "r1")],
            [{"type": "response.steer.accepted", "steer": {"id": "s1", "previous_response_id": "r1"}}],
            [
                {"type": "response.steer.accepted", "steer": {"id": "s2", "previous_response_id": "r1"}},
                {
                    "type": "response.steer.failed",
                    "steer": {"id": "s1", "previous_response_id": "r1", "input": "First correction"},
                    "error": {"code": "invalid_input", "message": "Rejected"},
                },
                response("response.completed", "r1"),
                response("response.created", "r2", parent="r1"),
                response("response.completed", "r2", parent="r1"),
            ],
        ]
    )
    socket.finish_when = lambda event: event.get("type") == "response.completed" and event["response"]["id"] == "r2"

    class FailingReductionService:
        def __init__(self, repository):
            del repository

        async def reduce_usage_reservation(self, *args, **kwargs):
            del args, kwargs
            raise RuntimeError("limit window rolled over")

    def configure(service, account):
        del account
        monkeypatch.delattr(service, "_reduce_websocket_api_key_usage")
        monkeypatch.setattr(proxy_service, "ApiKeysService", FailingReductionService)

    _, reservations, settled, _, _ = await run_socket(monkeypatch, socket, upstream, configure=configure)
    assert len(reservations) == 2
    assert len(settled) == 2
    assert saw("response.completed", "r2")(socket.sent)
    assert "Failed to reduce websocket API key reservation" in caplog.text


@pytest.mark.asyncio
async def test_pending_steering_continues_with_required_results_once(monkeypatch):
    steer = {"type": "response.steer", "previous_response_id": "r1", "input": "Correction"}
    result = {"type": "function_call_output", "call_id": "call_1", "output": "saved result"}
    call = {"type": "function_call", "call_id": "call_1", "name": "slow", "arguments": "{}"}
    socket = ScriptedSocket(
        [
            (create(), lambda _: True),
            (steer, saw("response.created", "r1")),
            (create(parent="r1", input_items=[result]), saw("response.steer.pending")),
        ]
    )
    upstream = ScriptedUpstream(
        [
            [response("response.created", "r1")],
            [
                {"type": "response.steer.accepted", "steer": {"id": "s1", "previous_response_id": "r1"}},
                {"type": "response.output_item.done", "response_id": "r1", "item": call},
                response("response.completed", "r1", output=[call]),
                {
                    "type": "response.steer.pending",
                    "steer": {"id": "s1", "previous_response_id": "r1"},
                    "reason": "waiting_for_required_input",
                    "required_input": [{"type": "function_call_output", "call_id": "call_1"}],
                },
            ],
            [response("response.created", "r2", parent="r1"), response("response.completed", "r2", parent="r1")],
        ]
    )
    _, reservations, settled, released, _ = await run_socket(monkeypatch, socket, upstream)
    assert len(reservations) == 3
    assert [value[0] for value in settled] == ["res_0", "res_2"]
    assert [call.args[0].reservation_id for call in released.await_args_list if call.args[0] is not None] == ["res_1"]
    assert upstream.sent[-1]["input"] == [result]
    assert sum(item["type"] == "response.steer" for item in upstream.sent) == 1


@pytest.mark.asyncio
async def test_failed_explicit_continuation_prepare_keeps_placeholder(monkeypatch):
    from app.core.clients.proxy import ProxyResponseError
    from app.core.errors import openai_error

    steer = {"type": "response.steer", "previous_response_id": "r1", "input": "Correction"}
    result = {"type": "function_call_output", "call_id": "call_1", "output": "saved result"}
    call = {"type": "function_call", "call_id": "call_1", "name": "slow", "arguments": "{}"}
    continuation = create(parent="r1", input_items=[result])
    socket = ScriptedSocket(
        [
            (create(), lambda _: True),
            (steer, saw("response.created", "r1")),
            (continuation, saw("response.steer.pending")),
            (continuation, saw("error")),
        ]
    )
    upstream = ScriptedUpstream(
        [
            [response("response.created", "r1")],
            [
                {"type": "response.steer.accepted", "steer": {"id": "s1", "previous_response_id": "r1"}},
                {"type": "response.output_item.done", "response_id": "r1", "item": call},
                response("response.completed", "r1", output=[call]),
                {
                    "type": "response.steer.pending",
                    "steer": {"id": "s1", "previous_response_id": "r1"},
                    "reason": "waiting_for_required_input",
                    "required_input": [{"type": "function_call_output", "call_id": "call_1"}],
                },
            ],
            [response("response.created", "r2", parent="r1"), response("response.completed", "r2", parent="r1")],
        ]
    )
    order: list[str] = []

    def configure(service, account):
        del account
        original_prepare = service._prepare_websocket_response_create_request
        original_release = service._release_websocket_request_state_reservation

        async def prepare(*args, **kwargs):
            payload = args[0] if args else kwargs.get("payload")
            if isinstance(payload, dict) and payload.get("previous_response_id") == "r1":
                order.append("prepare")
                if order.count("prepare") == 1:
                    raise ProxyResponseError(
                        400,
                        openai_error("invalid_input", "prepare failed", error_type="invalid_request_error"),
                    )
            return await original_prepare(*args, **kwargs)

        async def release(state, *args, **kwargs):
            if getattr(state, "steering_parent_response_id", None) == "r1" and state.request_text is None:
                order.append("release_placeholder")
            return await original_release(state, *args, **kwargs)

        monkeypatch.setattr(service, "_prepare_websocket_response_create_request", prepare)
        monkeypatch.setattr(service, "_release_websocket_request_state_reservation", release)

    _, reservations, settled, released, _ = await run_socket(monkeypatch, socket, upstream, configure=configure)
    assert order[:2] == ["prepare", "prepare"]
    assert "release_placeholder" in order
    assert order.index("release_placeholder") > order.index("prepare")
    assert order.count("prepare") == 2
    assert len(upstream.sent) == 3
    assert upstream.sent[-1]["input"] == [result]
    assert len(reservations) == 3
    assert [value[0] for value in settled] == ["res_0", "res_2"]
    assert [call.args[0].reservation_id for call in released.await_args_list if call.args[0] is not None] == ["res_1"]


@pytest.mark.asyncio
async def test_failed_explicit_continuation_admission_keeps_placeholder(monkeypatch):
    from app.core.clients.proxy import ProxyResponseError
    from app.core.errors import openai_error

    steer = {"type": "response.steer", "previous_response_id": "r1", "input": "Correction"}
    result = {"type": "function_call_output", "call_id": "call_1", "output": "saved result"}
    call = {"type": "function_call", "call_id": "call_1", "name": "slow", "arguments": "{}"}
    continuation = create(parent="r1", input_items=[result])
    socket = ScriptedSocket(
        [
            (create(), lambda _: True),
            (steer, saw("response.created", "r1")),
            (continuation, saw("response.steer.pending")),
            (continuation, saw("response.failed")),
        ]
    )
    upstream = ScriptedUpstream(
        [
            [response("response.created", "r1")],
            [
                {"type": "response.steer.accepted", "steer": {"id": "s1", "previous_response_id": "r1"}},
                {"type": "response.output_item.done", "response_id": "r1", "item": call},
                response("response.completed", "r1", output=[call]),
                {
                    "type": "response.steer.pending",
                    "steer": {"id": "s1", "previous_response_id": "r1"},
                    "reason": "waiting_for_required_input",
                    "required_input": [{"type": "function_call_output", "call_id": "call_1"}],
                },
            ],
            [response("response.created", "r2", parent="r1"), response("response.completed", "r2", parent="r1")],
        ]
    )
    order: list[str] = []

    def configure(service, account):
        del account
        original_admit = service._acquire_request_state_response_create_admission
        original_release = service._release_websocket_request_state_reservation
        failed = False

        async def admit(state, *args, **kwargs):
            nonlocal failed
            if getattr(state, "previous_response_id", None) == "r1" and state.request_text is not None and not failed:
                failed = True
                order.append("admit_fail")
                raise ProxyResponseError(
                    400,
                    openai_error("invalid_input", "admission failed", error_type="invalid_request_error"),
                )
            order.append("admit")
            return await original_admit(state, *args, **kwargs)

        async def release(state, *args, **kwargs):
            if getattr(state, "steering_parent_response_id", None) == "r1" and state.request_text is None:
                order.append("release_placeholder")
            return await original_release(state, *args, **kwargs)

        monkeypatch.setattr(service, "_acquire_request_state_response_create_admission", admit)
        monkeypatch.setattr(service, "_release_websocket_request_state_reservation", release)

    _, reservations, settled, released, _ = await run_socket(monkeypatch, socket, upstream, configure=configure)
    assert "admit_fail" in order
    assert "release_placeholder" in order
    assert order.index("release_placeholder") > order.index("admit_fail")
    assert len(upstream.sent) == 3
    assert upstream.sent[-1]["input"] == [result]
    assert len(reservations) == 4
    assert [value[0] for value in settled] == ["res_0", "res_3"]
    assert [call.args[0].reservation_id for call in released.await_args_list if call.args[0] is not None] == [
        "res_2",
        "res_1",
    ]


@pytest.mark.asyncio
async def test_steering_failure_releases_only_successor_reservation(monkeypatch):
    steer = {"type": "response.steer", "previous_response_id": "r1", "input": "Correction"}
    socket = ScriptedSocket([(create(), lambda _: True), (steer, saw("response.created", "r1"))])
    upstream = ScriptedUpstream(
        [
            [response("response.created", "r1")],
            [
                {"type": "response.steer.accepted", "steer": {"id": "s1", "previous_response_id": "r1"}},
                {
                    "type": "response.steer.failed",
                    "steer": {"id": "s1", "previous_response_id": "r1", "input": "Correction"},
                    "error": {"code": "successor_creation_failed", "message": "Rejected"},
                },
                response("response.completed", "r1"),
            ],
        ]
    )
    socket.finish_when = lambda event: event.get("type") == "response.completed" and event["response"]["id"] == "r1"
    _, reservations, settled, released, _ = await run_socket(monkeypatch, socket, upstream)
    assert len(reservations) == 2
    assert [value[0] for value in settled] == ["res_0"]
    assert [call.args[0].reservation_id for call in released.await_args_list if call.args[0] is not None] == ["res_1"]
    assert saw("response.steer.failed")(socket.sent)


@pytest.mark.asyncio
async def test_failed_final_steer_refund_does_not_abort_socket(monkeypatch, caplog):
    steer = {"type": "response.steer", "previous_response_id": "r1", "input": "Correction"}
    socket = ScriptedSocket([(create(), lambda _: True), (steer, saw("response.created", "r1"))])
    upstream = ScriptedUpstream(
        [
            [response("response.created", "r1")],
            [
                {"type": "response.steer.accepted", "steer": {"id": "s1", "previous_response_id": "r1"}},
                {
                    "type": "response.steer.failed",
                    "steer": {"id": "s1", "previous_response_id": "r1", "input": "Correction"},
                    "error": {"code": "successor_creation_failed", "message": "Rejected"},
                },
                response("response.completed", "r1"),
            ],
        ]
    )
    socket.finish_when = lambda event: event.get("type") == "response.completed" and event["response"]["id"] == "r1"

    def configure(service, account):
        del account
        original = service._release_websocket_request_state_reservation

        async def release(state, *args, **kwargs):
            if getattr(state, "steering_parent_response_id", None) == "r1":
                raise RuntimeError("refund failed")
            return await original(state, *args, **kwargs)

        monkeypatch.setattr(service, "_release_websocket_request_state_reservation", release)

    _, reservations, settled, _, _ = await run_socket(monkeypatch, socket, upstream, configure=configure)
    assert len(reservations) == 2
    assert [value[0] for value in settled] == ["res_0"]
    assert saw("response.steer.failed")(socket.sent)
    assert "Failed to release steering placeholder reservation" in caplog.text


@pytest.mark.asyncio
async def test_automatic_successor_never_claims_unrelated_queued_create(monkeypatch):
    steer = {"type": "response.steer", "previous_response_id": "r1", "input": "Correction"}
    socket = ScriptedSocket(
        [
            (create(), lambda _: True),
            (steer, saw("response.created", "r1")),
            (create(input_items="Unrelated"), saw("response.steer.accepted")),
        ]
    )
    upstream = ScriptedUpstream(
        [
            [response("response.created", "r1")],
            [{"type": "response.steer.accepted", "steer": {"id": "s1", "previous_response_id": "r1"}}],
            [
                response("response.incomplete", "r1"),
                response("response.created", "r2", parent="r1"),
                response("response.completed", "r2", parent="r1"),
                response("response.created", "r3"),
                response("response.completed", "r3"),
            ],
        ]
    )
    socket.finish_when = lambda event: event.get("type") == "response.completed" and event["response"]["id"] == "r3"
    _, reservations, settled, _, _ = await run_socket(monkeypatch, socket, upstream)
    assert len(reservations) == 3
    assert [(value[0], value[3]) for value in settled] == [("res_0", "r1"), ("res_1", "r2"), ("res_2", "r3")]


@pytest.mark.asyncio
async def test_steering_rejection_before_acceptance_matches_original_string(monkeypatch):
    steer = {"type": "response.steer", "previous_response_id": "r1", "input": "Original string"}
    socket = ScriptedSocket([(create(), lambda _: True), (steer, saw("response.created", "r1"))])
    upstream = ScriptedUpstream(
        [
            [response("response.created", "r1")],
            [
                {
                    "type": "response.steer.failed",
                    "steer": {"previous_response_id": "r1", "input": "Original string"},
                    "error": {"code": "invalid_input", "message": "Rejected"},
                },
                response("response.completed", "r1"),
            ],
        ]
    )
    socket.finish_when = lambda event: event.get("type") == "response.completed"
    _, reservations, settled, released, _ = await run_socket(monkeypatch, socket, upstream)
    assert len(reservations) == 2
    assert [entry[0] for entry in settled] == ["res_0"]
    assert [call.args[0].reservation_id for call in released.await_args_list if call.args[0]] == ["res_1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["completed", "anonymous_error"])
async def test_steering_tool_result_before_pending_owns_explicit_outcome(monkeypatch, terminal):
    steer = {"type": "response.steer", "previous_response_id": "r1", "input": "Correction"}
    result = {"type": "function_call_output", "call_id": "tool", "output": "saved"}
    call = {"type": "function_call", "call_id": "tool", "name": "slow", "arguments": "{}"}
    socket = ScriptedSocket(
        [
            (create(), lambda _: True),
            (steer, saw("response.created", "r1")),
            (create(parent="r1", input_items=[result]), saw("response.completed", "r1")),
        ]
    )
    outcome = (
        [response("response.created", "r2", parent="r1"), response("response.completed", "r2", parent="r1")]
        if terminal == "completed"
        else [{"type": "error", "error": {"code": "invalid_input", "message": "bad"}}]
    )
    upstream = ScriptedUpstream(
        [
            [response("response.created", "r1")],
            [
                {"type": "response.steer.accepted", "steer": {"id": "s", "previous_response_id": "r1"}},
                {"type": "response.output_item.done", "response_id": "r1", "item": call},
                response("response.completed", "r1", output=[call]),
            ],
            outcome,
        ]
    )
    socket.finish_when = lambda event: (
        event.get("type") == "error" or (event.get("type") == "response.completed" and event["response"]["id"] == "r2")
    )
    _, reservations, settled, released, logs = await run_socket(monkeypatch, socket, upstream)
    assert len(upstream.sent) == 3
    assert upstream.sent[-1]["input"] == [result]
    assert len(reservations) == 3
    assert [entry[0] for entry in settled] == ["res_0", "res_2"]
    assert settled[-1][1] == ("success" if terminal == "completed" else "error")
    assert len(logs.calls) == 2
    assert [call.args[0].reservation_id for call in released.await_args_list if call.args[0]] == ["res_1"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_input",
    [
        {"input": [{"role": "assistant", "content": "bad"}]},
        {"input": [{"role": "user", "content": [{"type": "input_file"}]}]},
        {"input": [{"role": "user", "content": [{"type": "input_text", "text": 42}]}]},
        {"previous_response_id": "foreign"},
        {"stream_id": "other"},
    ],
)
async def test_invalid_steering_is_rejected_without_child_admission(monkeypatch, bad_input):
    steer = {"type": "response.steer", "previous_response_id": "r1", "input": "Correction", **bad_input}
    socket = ScriptedSocket([(create(), lambda _: True), (steer, saw("response.created", "r1"))])
    upstream = ScriptedUpstream([[response("response.created", "r1")]])
    _, reservations, _, _, _ = await run_socket(monkeypatch, socket, upstream)
    assert len(upstream.sent) == 1
    assert len(reservations) == 1
    assert socket.sent[-1]["type"] == "response.steer.failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("denial", ["foreign_file", "account_unavailable", "policy_changed"])
async def test_steering_revalidates_file_account_and_key_policy(monkeypatch, denial):
    initial = {**create(), "reasoning": {"effort": "high"}}
    steer = {"type": "response.steer", "previous_response_id": "r1", "input": "Correction"}
    if denial == "foreign_file":
        steer["input"] = [{"role": "user", "content": [{"type": "input_file", "file_id": "file_foreign"}]}]
    socket = ScriptedSocket([(initial, lambda _: True), (steer, saw("response.created", "r1"))])
    upstream = ScriptedUpstream([[response("response.created", "r1")]])

    def configure(service, account):
        if denial == "foreign_file":
            monkeypatch.setattr(service, "_resolve_file_account_for_responses", AsyncMock(side_effect=[None, "other"]))
        elif denial == "account_unavailable":
            monkeypatch.setattr(
                service,
                "_revalidate_open_websocket_account",
                AsyncMock(return_value=(None, "no_available_accounts", "Unavailable")),
            )
        else:
            monkeypatch.setattr(
                service,
                "_refresh_websocket_api_key_policy",
                AsyncMock(side_effect=[_api_key(), replace(_api_key(), allowed_reasoning_efforts=["low"])]),
            )

    _, reservations, _, _, _ = await run_socket(monkeypatch, socket, upstream, configure=configure)
    assert len(upstream.sent) == 1
    assert len(reservations) == 1
    assert socket.sent[-1]["type"] == "response.steer.failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("effort", ["high"])
async def test_steering_keeps_permitted_inherited_raw_reasoning_effort(monkeypatch, effort):
    initial = {**create(), "reasoning": {"effort": effort}}
    steer = {"type": "response.steer", "previous_response_id": "r1", "input": "Correction"}
    socket = ScriptedSocket([(initial, lambda _: True), (steer, saw("response.created", "r1"))])
    upstream = ScriptedUpstream(
        [
            [response("response.created", "r1")],
            [
                {"type": "response.steer.accepted", "steer": {"id": "s", "previous_response_id": "r1"}},
                response("response.incomplete", "r1"),
                response("response.created", "r2", parent="r1"),
                response("response.completed", "r2", parent="r1"),
            ],
        ]
    )

    def configure(service, account):
        key = replace(_api_key(), allowed_reasoning_efforts=[effort])
        monkeypatch.setattr(service, "_refresh_websocket_api_key_policy", AsyncMock(return_value=key))

    _, reservations, settled, _, _ = await run_socket(monkeypatch, socket, upstream, configure=configure)
    assert upstream.sent[0]["reasoning"]["effort"] == ("max" if effort == "ultra" else effort)
    assert len(upstream.sent) == 2, socket.sent
    assert upstream.sent[1] == steer
    assert len(reservations) == len(settled) == 2


@pytest.mark.asyncio
async def test_cancelled_steering_send_releases_owned_leases_and_heartbeats(monkeypatch):
    from app.modules.proxy.load_balancer import AccountLease
    from app.modules.proxy.work_admission import WorkAdmissionController

    steer = {"type": "response.steer", "previous_response_id": "r1", "input": "Correction"}
    socket = ScriptedSocket([(create(), lambda _: True), (steer, saw("response.created", "r1"))])

    class CancelledSend(ScriptedUpstream):
        async def send_text(self, text):
            if json.loads(text).get("type") == "response.steer":
                self.sent.append(json.loads(text))
                current_task = asyncio.current_task()
                assert current_task is not None
                asyncio.get_running_loop().call_soon(current_task.cancel)
                await asyncio.Event().wait()
            await super().send_text(text)

    upstream = CancelledSend([[response("response.created", "r1")]])
    leases = []
    released_leases = []
    heartbeats = []
    observed_states = []
    controller = WorkAdmissionController(
        token_refresh_limit=2, websocket_connect_limit=2, response_create_limit=2, compact_response_create_limit=2
    )

    def configure(service, account):
        monkeypatch.delattr(service, "_start_request_state_api_key_reservation_heartbeat")
        monkeypatch.setattr(service, "_get_work_admission", lambda: controller)

        async def heartbeat(**kwargs):
            heartbeats.append(asyncio.current_task())
            await kwargs["stop_event"].wait()

        monkeypatch.setattr(service, "_run_api_key_reservation_heartbeat", heartbeat)

        async def acquire(**kwargs):
            lease = AccountLease(str(len(leases)), account.id, "response_create", 0.0)
            leases.append(lease)
            return lease

        async def release(lease):
            if lease is not None:
                released_leases.append(lease)

        monkeypatch.setattr(service, "_acquire_account_response_create_lease_or_overload", acquire)
        monkeypatch.setattr(service._load_balancer, "release_account_lease", release)
        original = service._prepare_response_bridge_request_state

        def prepare(*args, **kwargs):
            state, text = original(*args, **kwargs)
            observed_states.append(state)
            return state, text

        monkeypatch.setattr(service, "_prepare_response_bridge_request_state", prepare)

    _, reservations, settled, released, _ = await run_socket(
        monkeypatch, socket, upstream, configure=configure, cancelled=True
    )
    await asyncio.sleep(0)
    assert len(upstream.sent) == 2
    assert len(reservations) == 2
    assert sorted(leases, key=lambda item: item.lease_id) == sorted(released_leases, key=lambda item: item.lease_id)
    assert len(leases) == 2
    assert len(heartbeats) == 2 and all(task.done() for task in heartbeats)
    assert all(
        state.api_key_reservation is None and state.response_create_admission is None for state in observed_states
    )
    assert controller._response_create is not None
    assert controller._response_create.semaphore._value == 2
    finalized = [entry[0] for entry in settled] + [
        call.args[0].reservation_id for call in released.await_args_list if call.args[0]
    ]
    assert sorted(finalized) == ["res_0", "res_1"]


@pytest.mark.asyncio
async def test_cancelled_steering_after_child_registration_releases_successor_reservation(monkeypatch):
    steer = {"type": "response.steer", "previous_response_id": "r1", "input": "Correction"}
    socket = ScriptedSocket([(create(), lambda _: True), (steer, saw("response.created", "r1"))])
    upstream = ScriptedUpstream([[response("response.created", "r1")]])

    def configure(service, account):
        del account

        def cancel_after_registration(*args, **kwargs):
            del kwargs
            request_state = args[0]
            if request_state.steering_parent_response_id is not None:
                raise asyncio.CancelledError

        monkeypatch.setattr(service, "_start_request_state_api_key_reservation_heartbeat", cancel_after_registration)

    _, reservations, settled, released, _ = await run_socket(
        monkeypatch,
        socket,
        upstream,
        configure=configure,
        cancelled=True,
    )
    finalized = [entry[0] for entry in settled] + [
        call.args[0].reservation_id for call in released.await_args_list if call.args[0]
    ]
    assert len(reservations) == 2
    assert finalized.count("res_1") == 1


@pytest.mark.asyncio
async def test_steering_wrong_lane_and_missing_required_output_are_retryable(monkeypatch):
    initial = {**create(), "stream_id": "lane_1"}
    steer = {"type": "response.steer", "previous_response_id": "r1", "input": "Correction"}
    result = {"type": "function_call_output", "call_id": "tool", "output": "saved"}
    call = {"type": "function_call", "call_id": "tool", "name": "slow", "arguments": "{}"}
    complete = {**create(parent="r1", input_items=[result]), "stream_id": "lane_1"}
    socket = ScriptedSocket(
        [
            (initial, lambda _: True),
            (steer, saw("response.created", "r1")),
            ({**complete, "stream_id": "wrong_lane"}, saw("response.steer.pending")),
            ({**complete, "input": "Missing result"}, saw("error")),
            (complete, lambda events: sum(event.get("type") == "error" for event in events) == 2),
        ]
    )
    upstream = ScriptedUpstream(
        [
            [response("response.created", "r1")],
            [
                {"type": "response.steer.accepted", "steer": {"id": "s", "previous_response_id": "r1"}},
                {"type": "response.output_item.done", "response_id": "r1", "item": call},
                response("response.completed", "r1", output=[call]),
                {
                    "type": "response.steer.pending",
                    "steer": {"id": "s", "previous_response_id": "r1"},
                    "reason": "waiting_for_required_input",
                    "required_input": [{"type": "function_call_output", "call_id": "tool"}],
                },
            ],
            [response("response.created", "r2", parent="r1"), response("response.completed", "r2", parent="r1")],
        ]
    )
    socket.finish_when = lambda event: event.get("type") == "response.completed" and event["response"]["id"] == "r2"
    _, reservations, settled, released, _ = await run_socket(monkeypatch, socket, upstream)
    assert len(upstream.sent) == 3
    assert upstream.sent[-1]["stream_id"] == "lane_1"
    assert upstream.sent[-1]["input"] == [result]
    assert len(reservations) == 3
    assert [entry[0] for entry in settled] == ["res_0", "res_2"]
    assert [call.args[0].reservation_id for call in released.await_args_list if call.args[0]] == ["res_1"]


@pytest.mark.asyncio
async def test_upstream_disconnect_after_accepted_steering_never_replays(monkeypatch):
    from app.core.clients.proxy_websocket import UpstreamWebSocketMessage

    steer = {"type": "response.steer", "previous_response_id": "r1", "input": "Correction"}
    socket = ScriptedSocket([(create(), lambda _: True), (steer, saw("response.created", "r1"))])

    class DisconnectingUpstream(ScriptedUpstream):
        async def send_text(self, text):
            await super().send_text(text)
            if len(self.sent) == 2:
                self.messages.put_nowait(UpstreamWebSocketMessage(kind="close", close_code=1006))

    upstream = DisconnectingUpstream(
        [
            [response("response.created", "r1")],
            [{"type": "response.steer.accepted", "steer": {"id": "s", "previous_response_id": "r1"}}],
        ]
    )
    service, reservations, settled, released, _ = await run_socket(monkeypatch, socket, upstream)
    assert len(upstream.sent) == 2
    assert service._connect_proxy_websocket.await_count == 1
    assert len(reservations) == 2
    finalized = [entry[0] for entry in settled] + [
        call.args[0].reservation_id for call in released.await_args_list if call.args[0]
    ]
    assert sorted(finalized) == ["res_0", "res_1"]
    assert all(entry[1] != "success" for entry in settled)


@pytest.mark.asyncio
async def test_steering_size_limit_rejects_before_forwarding_or_reservation(monkeypatch):
    from app.modules.proxy._service.websocket import steering as steering_module

    steer = {"type": "response.steer", "previous_response_id": "r1", "input": "x" * 256}
    socket = ScriptedSocket([(create(), lambda _: True), (steer, saw("response.created", "r1"))])
    upstream = ScriptedUpstream([[response("response.created", "r1")]])
    monkeypatch.setattr(
        steering_module, "get_settings", lambda: SimpleNamespace(upstream_response_create_max_bytes=128)
    )
    _, reservations, _, _, _ = await run_socket(monkeypatch, socket, upstream)
    assert len(upstream.sent) == len(reservations) == 1
    assert socket.sent[-1]["type"] == "response.steer.failed"
    assert socket.sent[-1]["error"]["code"] == "payload_too_large"

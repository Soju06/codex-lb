from __future__ import annotations

import asyncio
import json
from collections import deque
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio
import pytest
from fastapi import WebSocket

from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.support import (
    _websocket_request_can_replay_before_visible_output,
    _websocket_should_defer_reasoning_prelude,
)
from tests.unit.test_proxy_utils import (
    _make_account,
    _make_proxy_settings,
    _QueuedTestUpstreamWebSocket,
    _repo_factory,
    _RequestLogsRecorder,
    _SettingsCache,
)

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_process_websocket_security_retry_releases_response_create_gate() -> None:
    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    account = _make_account("acc_ws_security_gate_regular")
    gate = asyncio.Semaphore(1)
    await gate.acquire()
    request_state = proxy_service._WebSocketRequestState(
        request_id="ws_req_security_gate",
        model="gpt-5.1",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        awaiting_response_created=True,
        transport="websocket",
        request_text='{"type":"response.create","model":"gpt-5.1","input":[]}',
    )
    request_state.response_create_gate = gate
    request_state.response_create_gate_acquired = True
    pending_requests = deque([request_state])
    upstream_control = proxy_service._WebSocketUpstreamControl()
    cyber_message = (
        "This chat was flagged for possible cybersecurity risk. "
        "To get authorized for security work, join the Trusted Access for Cyber program. "
        "https://chatgpt.com/cyber"
    )
    text = json.dumps(
        {
            "type": "response.failed",
            "response": {
                "id": "resp_ws_security_gate",
                "status": "failed",
                "error": {
                    "code": "invalid_request_error",
                    "type": "invalid_request_error",
                    "message": cyber_message,
                },
            },
        },
        separators=(",", ":"),
    )

    await service._process_upstream_websocket_text(
        text,
        account=account,
        account_id_value=account.id,
        pending_requests=pending_requests,
        pending_lock=anyio.Lock(),
        api_key=None,
        upstream_control=upstream_control,
        response_create_gate=gate,
    )

    assert upstream_control.replay_request_state is request_state
    assert request_state.response_create_gate_acquired is False
    assert request_state.response_create_gate is None
    await asyncio.wait_for(gate.acquire(), timeout=0.1)
    gate.release()


def test_http_bridge_deferred_reasoning_blocks_previsible_replay() -> None:
    request_state = proxy_service._WebSocketRequestState(
        request_id="http_security_deferred_reasoning",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        transport="http",
        awaiting_response_created=False,
        response_id="resp_security_deferred_reasoning",
        response_event_count=1,
        request_text='{"type":"response.create","model":"gpt-5.6-sol","input":[]}',
        upstream_model_output_seen=True,
        deferred_reasoning_downstream_texts=['data: {"type":"response.output_item.added"}\n\n'],
    )

    assert not _websocket_request_can_replay_before_visible_output(request_state)


def test_http_bridge_buffers_entire_reasoning_prelude_before_security_decision() -> None:
    request_state = proxy_service._WebSocketRequestState(
        request_id="http_security_multi_reasoning",
        model="gpt-5.6-sol",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        transport="http",
        awaiting_response_created=False,
        response_id="resp_security_multi_reasoning",
        response_event_count=2,
        request_text='{"type":"response.create","model":"gpt-5.6-sol","input":[]}',
        upstream_model_output_seen=True,
        deferred_reasoning_downstream_texts=['data: {"type":"response.output_item.added"}\n\n'],
    )

    assert _websocket_should_defer_reasoning_prelude(
        request_state,
        event_type="response.output_item.added",
        payload={"item": {"type": "reasoning"}},
    )
    assert not _websocket_request_can_replay_before_visible_output(request_state)


@pytest.mark.asyncio
async def test_direct_websocket_security_replay_reacquires_create_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _make_proxy_settings()
    settings.stream_idle_timeout_seconds = 300.0
    settings.proxy_downstream_websocket_idle_timeout_seconds = 120.0
    monkeypatch.setattr(proxy_service, "get_settings", lambda: settings)
    monkeypatch.setattr(proxy_service, "get_settings_cache", lambda: _SettingsCache(settings))

    service = proxy_service.ProxyService(_repo_factory(_RequestLogsRecorder()))
    regular_account = _make_account("acc_ws_security_gate_regular_e2e")
    authorized_account = _make_account("acc_ws_security_gate_authorized_e2e")
    authorized_account.security_work_authorized = True
    cyber_message = (
        "This chat was flagged for possible cybersecurity risk. "
        "To get authorized for security work, join the Trusted Access for Cyber program. "
        "https://chatgpt.com/cyber"
    )
    first_upstream = _QueuedTestUpstreamWebSocket(
        [
            SimpleNamespace(
                kind="text",
                text=json.dumps(
                    {
                        "type": "response.failed",
                        "response": {
                            "id": "resp_ws_security_gate_denied",
                            "status": "failed",
                            "error": {
                                "code": "invalid_request_error",
                                "type": "invalid_request_error",
                                "message": cyber_message,
                            },
                        },
                    },
                    separators=(",", ":"),
                ),
                data=None,
                close_code=None,
                error=None,
                error_code=None,
            )
        ]
    )
    second_upstream = _QueuedTestUpstreamWebSocket(
        [
            SimpleNamespace(
                kind="text",
                text='{"type":"response.created","response":{"id":"resp_ws_security_gate_ok","status":"in_progress"}}',
                data=None,
                close_code=None,
                error=None,
                error_code=None,
            ),
            SimpleNamespace(
                kind="text",
                text='{"type":"response.completed","response":{"id":"resp_ws_security_gate_ok","status":"completed","usage":{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}',
                data=None,
                close_code=None,
                error=None,
                error_code=None,
            ),
        ]
    )
    connect_count = 0

    async def fake_connect(_self: Any, _headers: Any, **_kwargs: Any):
        nonlocal connect_count
        connect_count += 1
        if connect_count == 1:
            return regular_account, first_upstream
        return authorized_account, second_upstream

    admission_count = 0
    original_acquire = service._acquire_request_state_response_create_admission

    async def track_acquire(request_state: Any, **kwargs: Any) -> None:
        nonlocal admission_count
        admission_count += 1
        await original_acquire(request_state, **kwargs)

    class _Downstream:
        def __init__(self, request_text: str) -> None:
            self.request_text = request_text
            self.request_sent = False
            self.done = asyncio.Event()
            self.sent_text: list[str] = []

        async def receive(self) -> dict[str, object]:
            if not self.request_sent:
                self.request_sent = True
                return {"type": "websocket.receive", "text": self.request_text}
            await self.done.wait()
            return {"type": "websocket.disconnect"}

        async def send_text(self, text: str) -> None:
            self.sent_text.append(text)
            payload = json.loads(text)
            if payload.get("type") in {"response.completed", "response.failed", "error"}:
                self.done.set()

        async def send_bytes(self, _data: bytes) -> None:
            return None

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            del code, reason
            self.done.set()

    monkeypatch.setattr(proxy_service.ProxyService, "_connect_proxy_websocket", fake_connect)
    monkeypatch.setattr(service, "_acquire_request_state_response_create_admission", track_acquire)
    monkeypatch.setattr(service, "_resolve_compact_turn_state_owner", AsyncMock(return_value=None))
    request_payload = {
        "type": "response.create",
        "model": "gpt-5.1",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "security check"}]}],
        "stream": True,
    }
    downstream = _Downstream(json.dumps(request_payload, separators=(",", ":")))

    await service.proxy_responses_websocket(
        cast(WebSocket, downstream),
        {},
        codex_session_affinity=False,
        openai_cache_affinity=False,
        api_key=None,
    )

    assert connect_count == 2
    assert admission_count == 2
    assert len(first_upstream.sent_text) == 1
    assert len(second_upstream.sent_text) == 1
    assert any(json.loads(text).get("type") == "response.completed" for text in downstream.sent_text)

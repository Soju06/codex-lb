from __future__ import annotations

import asyncio
import json
from collections import deque
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi.testclient import TestClient

import app.modules.proxy.api as proxy_api_module
import app.modules.proxy.service as proxy_module

pytestmark = pytest.mark.integration


class _FakeUpstreamMessage:
    def __init__(
        self,
        kind: str,
        *,
        text: str | None = None,
        data: bytes | None = None,
        close_code: int | None = None,
        error: str | None = None,
    ) -> None:
        self.kind = kind
        self.text = text
        self.data = data
        self.close_code = close_code
        self.error = error


class _FakeUpstreamWebSocket:
    def __init__(self, messages: list[_FakeUpstreamMessage]) -> None:
        self.sent_text: list[str] = []
        self.sent_bytes: list[bytes] = []
        self.closed = False
        self._messages: asyncio.Queue[_FakeUpstreamMessage] = asyncio.Queue()
        for message in messages:
            self._messages.put_nowait(message)

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)

    async def receive(self) -> _FakeUpstreamMessage:
        return await self._messages.get()

    async def close(self) -> None:
        self.closed = True


class _SequencedUpstreamWebSocket(_FakeUpstreamWebSocket):
    def __init__(
        self,
        messages: list[_FakeUpstreamMessage],
        *,
        deferred_message_batches: list[list[_FakeUpstreamMessage]] | None = None,
    ) -> None:
        super().__init__(messages)
        self._deferred_message_batches = deque(deferred_message_batches or [])

    async def send_text(self, text: str) -> None:
        await super().send_text(text)
        if not self._deferred_message_batches:
            return
        for message in self._deferred_message_batches.popleft():
            self._messages.put_nowait(message)


def test_backend_responses_websocket_proxies_upstream_and_persists_log(app_instance, monkeypatch):
    upstream_messages = [
        _FakeUpstreamMessage(
            "text",
            text=json.dumps(
                {
                    "type": "response.created",
                    "response": {"id": "resp_ws_1", "object": "response", "status": "in_progress"},
                },
                separators=(",", ":"),
            ),
        ),
        _FakeUpstreamMessage(
            "text",
            text=json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_ws_1",
                        "object": "response",
                        "status": "completed",
                        "service_tier": "fast",
                        "usage": {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8},
                    },
                },
                separators=(",", ":"),
            ),
        ),
    ]
    fake_upstream = _FakeUpstreamWebSocket(upstream_messages)
    seen: dict[str, object] = {}
    log_calls: list[dict[str, object]] = []

    class _FakeSettingsCache:
        async def get(self):
            return SimpleNamespace(prefer_earlier_reset_accounts=False, routing_strategy="usage_weighted")

    async def allow_firewall(_websocket):
        return None

    async def allow_proxy_api_key(authorization: str | None):
        assert authorization == "Bearer external-token"
        return None

    async def fake_connect_proxy_websocket(
        self,
        headers,
        *,
        sticky_key,
        sticky_kind,
        prefer_earlier_reset,
        routing_strategy,
        model,
        request_state,
        api_key,
        client_send_lock,
        websocket,
    ):
        del api_key
        seen["headers"] = dict(headers)
        seen["sticky_key"] = sticky_key
        seen["sticky_kind"] = sticky_kind
        seen["prefer_earlier_reset"] = prefer_earlier_reset
        seen["routing_strategy"] = routing_strategy
        seen["model"] = model
        seen["request_id"] = request_state.request_id
        return SimpleNamespace(id="acct_ws_proxy"), fake_upstream

    async def fake_write_request_log(self, **kwargs):
        log_calls.append(kwargs)

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", fake_write_request_log)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "service_tier": "fast",
        "reasoning": {"effort": "high"},
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect(
            "/backend-api/codex/responses",
            headers={
                "Authorization": "Bearer external-token",
                "chatgpt-account-id": "external-account",
                "session_id": "thread-ws-1",
                "openai-beta": "responses_websockets=2026-02-06",
            },
        ) as websocket:
            websocket.send_text(json.dumps(request_payload))
            first = json.loads(websocket.receive_text())
            second = json.loads(websocket.receive_text())

    assert first["type"] == "response.created"
    assert second["type"] == "response.completed"
    seen_headers = cast(dict[str, str], seen["headers"])
    assert seen_headers["session_id"] == "thread-ws-1"
    assert seen_headers["openai-beta"] == "responses_websockets=2026-02-06"
    assert seen["sticky_key"] == "thread-ws-1"
    assert seen["sticky_kind"] == proxy_module.StickySessionKind.CODEX_SESSION
    assert seen["prefer_earlier_reset"] is False
    assert seen["routing_strategy"] == "usage_weighted"
    assert seen["model"] == "gpt-5.4"
    expected_upstream_request = dict(request_payload)
    expected_upstream_request.pop("service_tier")
    assert fake_upstream.sent_text == [json.dumps(expected_upstream_request, separators=(",", ":"))]
    assert len(log_calls) == 1
    log = log_calls[0]
    assert log["account_id"] == "acct_ws_proxy"
    assert log["request_id"] == "resp_ws_1"
    assert log["model"] == "gpt-5.4"
    assert log["service_tier"] == "fast"
    assert log["transport"] == "websocket"
    assert log["status"] == "success"
    assert log["input_tokens"] == 3
    assert log["output_tokens"] == 5


def test_backend_responses_websocket_reconnects_after_account_health_failure(app_instance, monkeypatch):
    first_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.created", "response": {"id": "resp_ws_fail", "status": "in_progress"}},
                    separators=(",", ":"),
                ),
            ),
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.failed",
                        "response": {
                            "id": "resp_ws_fail",
                            "status": "failed",
                            "error": {"code": "rate_limit_exceeded", "message": "slow down"},
                            "usage": {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
                        },
                    },
                    separators=(",", ":"),
                ),
            ),
        ]
    )
    second_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.created", "response": {"id": "resp_ws_ok", "status": "in_progress"}},
                    separators=(",", ":"),
                ),
            ),
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_ws_ok",
                            "status": "completed",
                            "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                        },
                    },
                    separators=(",", ":"),
                ),
            ),
        ]
    )
    upstreams = [first_upstream, second_upstream]
    connect_models: list[str | None] = []
    handled_error_codes: list[str] = []

    class _FakeSettingsCache:
        async def get(self):
            return SimpleNamespace(prefer_earlier_reset_accounts=False, routing_strategy="usage_weighted")

    async def allow_firewall(_websocket):
        return None

    async def allow_proxy_api_key(_authorization: str | None):
        return None

    async def fake_connect_proxy_websocket(
        self,
        headers,
        *,
        sticky_key,
        sticky_kind,
        prefer_earlier_reset,
        routing_strategy,
        model,
        request_state,
        api_key,
        client_send_lock,
        websocket,
    ):
        del (
            self,
            headers,
            sticky_key,
            sticky_kind,
            prefer_earlier_reset,
            routing_strategy,
            request_state,
            api_key,
            client_send_lock,
            websocket,
        )
        upstream = upstreams[len(connect_models)]
        connect_models.append(model)
        return SimpleNamespace(id=f"acct_ws_proxy_{len(connect_models)}"), upstream

    async def fake_handle_stream_error(self, account, error, code):
        del self, account, error
        handled_error_codes.append(code)

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_handle_stream_error", fake_handle_stream_error)

    first_request = {
        "type": "response.create",
        "model": "gpt-5.1",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "first"}]}],
        "stream": True,
    }
    second_request = {
        "type": "response.create",
        "model": "gpt-5.2",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "second"}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(json.dumps(first_request))
            failed_events = [json.loads(websocket.receive_text()) for _ in range(2)]

            websocket.send_text(json.dumps(second_request))
            success_events = [json.loads(websocket.receive_text()) for _ in range(2)]

    assert [event["type"] for event in failed_events] == ["response.created", "response.failed"]
    assert failed_events[1]["response"]["error"]["code"] == "rate_limit_exceeded"
    assert [event["type"] for event in success_events] == ["response.created", "response.completed"]
    assert connect_models == ["gpt-5.1", "gpt-5.2"]
    assert handled_error_codes == ["rate_limit_exceeded"]
    assert first_upstream.closed is True
    assert first_upstream.sent_text == [json.dumps(first_request, separators=(",", ":"))]
    assert second_upstream.sent_text == [json.dumps(second_request, separators=(",", ":"))]


def test_backend_responses_websocket_emits_no_accounts_error(app_instance, monkeypatch):
    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "stream": True,
    }

    async def allow_firewall(_websocket):
        return None

    async def allow_proxy_api_key(authorization: str | None):
        assert authorization is None
        return None

    class _FakeSettingsCache:
        async def get(self):
            return SimpleNamespace(prefer_earlier_reset_accounts=False, routing_strategy="usage_weighted")

    async def fake_connect_proxy_websocket(
        self,
        headers,
        *,
        sticky_key,
        sticky_kind,
        prefer_earlier_reset,
        routing_strategy,
        model,
        request_state,
        api_key,
        client_send_lock,
        websocket,
    ):
        del (
            headers,
            sticky_key,
            sticky_kind,
            prefer_earlier_reset,
            routing_strategy,
            model,
            request_state,
            api_key,
            self,
        )
        async with client_send_lock:
            await websocket.send_text(json.dumps({"type": "error", "status": 503, "error": {"code": "no_accounts"}}))
        return None, None

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(json.dumps(request_payload))
            event = json.loads(websocket.receive_text())

    assert event["type"] == "error"
    assert event["status"] == 503
    assert event["error"]["code"] == "no_accounts"


def test_backend_responses_websocket_matches_terminal_events_by_response_id(app_instance, monkeypatch):
    upstream_messages = [
        _FakeUpstreamMessage(
            "text",
            text=json.dumps(
                {"type": "response.created", "response": {"id": "resp_ws_a", "status": "in_progress"}},
                separators=(",", ":"),
            ),
        ),
        _FakeUpstreamMessage(
            "text",
            text=json.dumps(
                {"type": "response.created", "response": {"id": "resp_ws_b", "status": "in_progress"}},
                separators=(",", ":"),
            ),
        ),
    ]
    fake_upstream = _SequencedUpstreamWebSocket(
        upstream_messages,
        deferred_message_batches=[
            [],
            [
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.completed",
                            "response": {
                                "id": "resp_ws_b",
                                "status": "completed",
                                "usage": {"input_tokens": 7, "output_tokens": 11, "total_tokens": 18},
                            },
                        },
                        separators=(",", ":"),
                    ),
                ),
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.completed",
                            "response": {
                                "id": "resp_ws_a",
                                "status": "completed",
                                "usage": {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8},
                            },
                        },
                        separators=(",", ":"),
                    ),
                ),
            ],
        ],
    )
    log_calls: list[dict[str, object]] = []

    class _FakeSettingsCache:
        async def get(self):
            return SimpleNamespace(prefer_earlier_reset_accounts=False, routing_strategy="usage_weighted")

    async def allow_firewall(_websocket):
        return None

    async def allow_proxy_api_key(_authorization: str | None):
        return None

    async def fake_connect_proxy_websocket(
        self,
        headers,
        *,
        sticky_key,
        sticky_kind,
        prefer_earlier_reset,
        routing_strategy,
        model,
        request_state,
        api_key,
        client_send_lock,
        websocket,
    ):
        del (
            self,
            headers,
            sticky_key,
            sticky_kind,
            prefer_earlier_reset,
            routing_strategy,
            model,
            request_state,
            api_key,
            client_send_lock,
            websocket,
        )
        return SimpleNamespace(id="acct_ws_proxy"), fake_upstream

    async def fake_write_request_log(self, **kwargs):
        del self
        log_calls.append(kwargs)

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", fake_write_request_log)

    first_request = {
        "type": "response.create",
        "model": "gpt-5.1",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "first"}]}],
        "stream": True,
    }
    second_request = {
        "type": "response.create",
        "model": "gpt-5.2",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "second"}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(json.dumps(first_request))
            websocket.send_text(json.dumps(second_request))
            events = [json.loads(websocket.receive_text()) for _ in range(4)]

    assert [event["type"] for event in events] == [
        "response.created",
        "response.created",
        "response.completed",
        "response.completed",
    ]
    assert len(log_calls) == 2
    assert log_calls[0]["request_id"] == "resp_ws_b"
    assert log_calls[0]["model"] == "gpt-5.2"
    assert log_calls[0]["input_tokens"] == 7
    assert log_calls[1]["request_id"] == "resp_ws_a"
    assert log_calls[1]["model"] == "gpt-5.1"
    assert log_calls[1]["input_tokens"] == 3


def test_backend_responses_websocket_emits_response_failed_before_close_on_upstream_eof(app_instance, monkeypatch):
    upstream_messages = [
        _FakeUpstreamMessage(
            "text",
            text=json.dumps(
                {"type": "response.created", "response": {"id": "resp_ws_eof", "status": "in_progress"}},
                separators=(",", ":"),
            ),
        ),
        _FakeUpstreamMessage("close", close_code=1011),
    ]
    fake_upstream = _FakeUpstreamWebSocket(upstream_messages)
    log_calls: list[dict[str, object]] = []

    class _FakeSettingsCache:
        async def get(self):
            return SimpleNamespace(prefer_earlier_reset_accounts=False, routing_strategy="usage_weighted")

    async def allow_firewall(_websocket):
        return None

    async def allow_proxy_api_key(_authorization: str | None):
        return None

    async def fake_connect_proxy_websocket(
        self,
        headers,
        *,
        sticky_key,
        sticky_kind,
        prefer_earlier_reset,
        routing_strategy,
        model,
        request_state,
        api_key,
        client_send_lock,
        websocket,
    ):
        del (
            self,
            headers,
            sticky_key,
            sticky_kind,
            prefer_earlier_reset,
            routing_strategy,
            model,
            request_state,
            api_key,
            client_send_lock,
            websocket,
        )
        return SimpleNamespace(id="acct_ws_proxy"), fake_upstream

    async def fake_write_request_log(self, **kwargs):
        del self
        log_calls.append(kwargs)

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", fake_write_request_log)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(json.dumps(request_payload))
            created_event = json.loads(websocket.receive_text())
            failed_event = json.loads(websocket.receive_text())

    assert created_event["type"] == "response.created"
    assert failed_event["type"] == "response.failed"
    assert failed_event["response"]["id"] == "resp_ws_eof"
    assert failed_event["response"]["error"]["code"] == "stream_incomplete"
    assert "close_code=1011" in failed_event["response"]["error"]["message"]
    assert len(log_calls) == 1
    assert log_calls[0]["request_id"] == "resp_ws_eof"
    assert log_calls[0]["status"] == "error"
    assert log_calls[0]["error_code"] == "stream_incomplete"

from __future__ import annotations

import asyncio
import json
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

    def push_message(self, message: _FakeUpstreamMessage) -> None:
        self._messages.put_nowait(message)

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)

    async def send_bytes(self, data: bytes) -> None:
        self.sent_bytes.append(data)

    async def receive(self) -> _FakeUpstreamMessage:
        return await self._messages.get()

    async def close(self) -> None:
        self.closed = True


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
                        "service_tier": "priority",
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
        prefer_earlier_reset,
        routing_strategy,
        model,
        request_state,
        client_send_lock,
        websocket,
        api_key=None,
        deadline=None,
        exclude_account_ids=None,
    ):
        del api_key, deadline, exclude_account_ids
        seen["headers"] = dict(headers)
        seen["sticky_key"] = sticky_key
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
        "service_tier": "priority",
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
    assert seen["prefer_earlier_reset"] is False
    assert seen["routing_strategy"] == "usage_weighted"
    assert seen["model"] == "gpt-5.4"
    assert len(fake_upstream.sent_text) == 1
    forwarded_payload = json.loads(fake_upstream.sent_text[0])
    assert forwarded_payload["type"] == "response.create"
    assert forwarded_payload["model"] == "gpt-5.4"
    assert forwarded_payload["service_tier"] == "priority"
    assert len(log_calls) == 1
    log = log_calls[0]
    assert log["account_id"] == "acct_ws_proxy"
    assert log["request_id"] == "resp_ws_1"
    assert log["model"] == "gpt-5.4"
    assert log["service_tier"] == "priority"
    assert log["transport"] == "websocket"
    assert log["status"] == "success"
    assert log["input_tokens"] == 3
    assert log["output_tokens"] == 5


def test_backend_responses_websocket_preserves_request_order_for_overlapping_creates(app_instance, monkeypatch):
    first_upstream = _FakeUpstreamWebSocket(
        [
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
        ]
    )
    second_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.created",
                        "response": {"id": "resp_ws_2", "object": "response", "status": "in_progress"},
                    },
                    separators=(",", ":"),
                ),
            ),
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.completed",
                        "response": {"id": "resp_ws_2", "object": "response", "status": "completed"},
                    },
                    separators=(",", ":"),
                ),
            ),
        ]
    )
    upstreams = [first_upstream, second_upstream]
    seen_models: list[str | None] = []

    class _FakeSettingsCache:
        async def get(self):
            return SimpleNamespace(prefer_earlier_reset_accounts=False, routing_strategy="usage_weighted")

    async def allow_firewall(_websocket):
        return None

    async def allow_proxy_api_key(authorization: str | None):
        assert authorization is None
        return None

    async def fake_connect_proxy_websocket(
        self,
        headers,
        *,
        sticky_key,
        prefer_earlier_reset,
        routing_strategy,
        model,
        request_state,
        client_send_lock,
        websocket,
        api_key=None,
        deadline=None,
        exclude_account_ids=None,
    ):
        del self, headers, sticky_key, prefer_earlier_reset, routing_strategy, request_state, client_send_lock, websocket, api_key, deadline
        seen_models.append(model)
        if exclude_account_ids:
            assert exclude_account_ids == {"acct_ws_one"}
        upstream = upstreams.pop(0)
        account_id = "acct_ws_one" if model == "gpt-5.4" else "acct_ws_two"
        return SimpleNamespace(id=account_id), upstream

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)

    first_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "stream": True,
    }
    second_payload = {
        "type": "response.create",
        "model": "gpt-5.5",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "again"}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(json.dumps(first_payload))
            websocket.send_text(json.dumps(second_payload))
            first_upstream.push_message(
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.completed",
                            "response": {"id": "resp_ws_1", "object": "response", "status": "completed"},
                        },
                        separators=(",", ":"),
                    ),
                )
            )
            events = [json.loads(websocket.receive_text()) for _ in range(4)]

    response_ids = [event["response"]["id"] for event in events]
    assert sorted(response_ids) == ["resp_ws_1", "resp_ws_1", "resp_ws_2", "resp_ws_2"]
    assert seen_models == ["gpt-5.4", "gpt-5.5"]
    assert len(first_upstream.sent_text) == 1
    assert len(second_upstream.sent_text) == 1
    assert first_upstream.closed is True
    assert second_upstream.closed is True


def test_backend_responses_websocket_forwards_non_create_events_to_active_upstream(app_instance, monkeypatch):
    fake_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.created",
                        "response": {"id": "resp_ws_control", "object": "response", "status": "in_progress"},
                    },
                    separators=(",", ":"),
                ),
            ),
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.completed",
                        "response": {"id": "resp_ws_control", "object": "response", "status": "completed"},
                    },
                    separators=(",", ":"),
                ),
            ),
        ]
    )

    class _FakeSettingsCache:
        async def get(self):
            return SimpleNamespace(prefer_earlier_reset_accounts=False, routing_strategy="usage_weighted")

    async def allow_firewall(_websocket):
        return None

    async def allow_proxy_api_key(authorization: str | None):
        assert authorization is None
        return None

    async def fake_connect_proxy_websocket(
        self,
        headers,
        *,
        sticky_key,
        prefer_earlier_reset,
        routing_strategy,
        model,
        request_state,
        client_send_lock,
        websocket,
        api_key=None,
        deadline=None,
        exclude_account_ids=None,
    ):
        del self, headers, sticky_key, prefer_earlier_reset, routing_strategy, model, request_state, client_send_lock, websocket, api_key, deadline, exclude_account_ids
        return SimpleNamespace(id="acct_ws_control"), fake_upstream

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "stream": True,
    }
    control_payload = {"type": "response.cancel", "response_id": "resp_ws_control"}

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(json.dumps(request_payload))
            assert json.loads(websocket.receive_text())["type"] == "response.created"
            websocket.send_text(json.dumps(control_payload))
            assert json.loads(websocket.receive_text())["type"] == "response.completed"

    assert len(fake_upstream.sent_text) == 2
    assert json.loads(fake_upstream.sent_text[1]) == control_payload


def test_backend_responses_websocket_surfaces_upstream_disconnect(app_instance, monkeypatch):
    fake_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage("error", error="upstream closed unexpectedly"),
        ]
    )

    class _FakeSettingsCache:
        async def get(self):
            return SimpleNamespace(prefer_earlier_reset_accounts=False, routing_strategy="usage_weighted")

    async def allow_firewall(_websocket):
        return None

    async def allow_proxy_api_key(authorization: str | None):
        assert authorization is None
        return None

    async def fake_connect_proxy_websocket(
        self,
        headers,
        *,
        sticky_key,
        prefer_earlier_reset,
        routing_strategy,
        model,
        request_state,
        client_send_lock,
        websocket,
        api_key=None,
        deadline=None,
        exclude_account_ids=None,
    ):
        del self, headers, sticky_key, prefer_earlier_reset, routing_strategy, model, request_state, client_send_lock, websocket, api_key, deadline, exclude_account_ids
        return SimpleNamespace(id="acct_ws_disconnect"), fake_upstream

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(json.dumps(request_payload))
            event = json.loads(websocket.receive_text())

    assert event["type"] == "error"
    assert event["status"] == 502
    assert event["error"]["code"] == "stream_incomplete"
    assert event["error"]["message"] == "upstream closed unexpectedly"


def test_v1_responses_websocket_uses_prompt_cache_affinity_and_http_normalization(app_instance, monkeypatch):
    fake_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.created",
                        "response": {"id": "resp_ws_v1", "object": "response", "status": "in_progress"},
                    },
                    separators=(",", ":"),
                ),
            ),
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.completed",
                        "response": {"id": "resp_ws_v1", "object": "response", "status": "completed"},
                    },
                    separators=(",", ":"),
                ),
            ),
        ]
    )
    seen: dict[str, object] = {}

    class _FakeSettingsCache:
        async def get(self):
            return SimpleNamespace(
                prefer_earlier_reset_accounts=False,
                routing_strategy="usage_weighted",
                sticky_threads_enabled=False,
            )

    async def allow_firewall(_websocket):
        return None

    async def allow_proxy_api_key(authorization: str | None):
        assert authorization is None
        return None

    async def fake_connect_proxy_websocket(
        self,
        headers,
        *,
        sticky_key,
        prefer_earlier_reset,
        routing_strategy,
        model,
        request_state,
        client_send_lock,
        websocket,
        api_key=None,
        deadline=None,
        exclude_account_ids=None,
    ):
        del self, headers, prefer_earlier_reset, routing_strategy, model, request_state, client_send_lock, websocket, api_key, deadline, exclude_account_ids
        seen["sticky_key"] = sticky_key
        return SimpleNamespace(id="acct_ws_v1"), fake_upstream

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "input": "cache me",
        "promptCacheKey": "thread_123",
        "promptCacheRetention": "4h",
        "temperature": 0.2,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/v1/responses") as websocket:
            websocket.send_text(json.dumps(request_payload))
            assert json.loads(websocket.receive_text())["type"] == "response.created"
            assert json.loads(websocket.receive_text())["type"] == "response.completed"

    assert seen["sticky_key"] == "thread_123"
    forwarded_payload = json.loads(fake_upstream.sent_text[0])
    assert forwarded_payload["prompt_cache_key"] == "thread_123"
    assert "promptCacheKey" not in forwarded_payload
    assert "prompt_cache_retention" not in forwarded_payload
    assert "temperature" not in forwarded_payload


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
        prefer_earlier_reset,
        routing_strategy,
        model,
        request_state,
        client_send_lock,
        websocket,
        api_key=None,
        deadline=None,
        exclude_account_ids=None,
    ):
        del headers, sticky_key, prefer_earlier_reset, routing_strategy, model, request_state, self, api_key, deadline, exclude_account_ids
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

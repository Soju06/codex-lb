from __future__ import annotations

import asyncio
import json
from collections import deque
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import app.modules.proxy.api as proxy_api_module
import app.modules.proxy.service as proxy_module

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _stub_request_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_write_request_log(self, **kwargs):
        del self, kwargs
        return None

    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", fake_write_request_log)


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


class _FailingSendUpstreamWebSocket(_FakeUpstreamWebSocket):
    async def send_text(self, text: str) -> None:
        await super().send_text(text)
        raise RuntimeError("socket closed during send")


class _DelayedUpstreamWebSocket(_FakeUpstreamWebSocket):
    def __init__(self, messages: list[_FakeUpstreamMessage], *, delays: list[float]) -> None:
        super().__init__(messages)
        self._delays = deque(delays)

    async def receive(self) -> _FakeUpstreamMessage:
        if self._delays:
            await asyncio.sleep(self._delays.popleft())
        return await super().receive()


def _websocket_settings(**overrides):
    values = {
        "prefer_earlier_reset_accounts": False,
        "sticky_threads_enabled": False,
        "openai_cache_affinity_max_age_seconds": 300,
        "openai_prompt_cache_key_derivation_enabled": True,
        "routing_strategy": "usage_weighted",
        "proxy_request_budget_seconds": 75.0,
        "stream_idle_timeout_seconds": 300.0,
        "proxy_downstream_websocket_idle_timeout_seconds": 120.0,
        "http_responses_session_bridge_instance_id": "test-instance",
        "log_proxy_request_shape": False,
        "log_proxy_request_shape_raw_cache_key": False,
        "proxy_token_refresh_limit": 32,
        "proxy_upstream_websocket_connect_limit": 64,
        "proxy_response_create_limit": 64,
        "proxy_compact_response_create_limit": 16,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


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
            return _websocket_settings()

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
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset,
        routing_strategy,
        model,
        request_state,
        api_key,
        client_send_lock,
        websocket,
    ):
        del api_key, reallocate_sticky, sticky_max_age_seconds
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
        "instructions": "",
        "client_metadata": {"x-codex-turn-metadata": '{"turn_id":"turn_123","sandbox":"workspace-write"}'},
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
    assert seen_headers["x-codex-turn-state"] == cast(str, seen["sticky_key"])
    assert seen["sticky_kind"] == proxy_module.StickySessionKind.CODEX_SESSION
    assert seen["prefer_earlier_reset"] is False
    assert seen["routing_strategy"] == "usage_weighted"
    assert seen["model"] == "gpt-5.4"
    assert [json.loads(message) for message in fake_upstream.sent_text] == [
        {
            "model": "gpt-5.4",
            "instructions": "",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
            "tools": [],
            "reasoning": {"effort": "high"},
            "client_metadata": {"x-codex-turn-metadata": '{"turn_id":"turn_123","sandbox":"workspace-write"}'},
            "service_tier": "priority",
            "store": False,
            "include": [],
            "type": "response.create",
        }
    ]
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


def test_backend_responses_websocket_strips_image_generation_tool_advertisement(app_instance, monkeypatch):
    upstream_messages = [
        _FakeUpstreamMessage(
            "text",
            text=json.dumps(
                {
                    "type": "response.created",
                    "response": {"id": "resp_ws_tools", "object": "response", "status": "in_progress"},
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
                        "id": "resp_ws_tools",
                        "object": "response",
                        "status": "completed",
                        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                    },
                },
                separators=(",", ":"),
            ),
        ),
    ]
    fake_upstream = _FakeUpstreamWebSocket(upstream_messages)

    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

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
        reallocate_sticky,
        sticky_max_age_seconds,
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
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset,
            routing_strategy,
            model,
            request_state,
            api_key,
            client_send_lock,
            websocket,
        )
        return SimpleNamespace(id="acct_ws_proxy"), fake_upstream

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)

    function_tool = {
        "type": "function",
        "name": "lookup_weather",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
    }
    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "tools": [{"type": "image_generation", "output_format": "png"}, function_tool],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect(
            "/backend-api/codex/responses",
            headers={"Authorization": "Bearer external-token"},
        ) as websocket:
            websocket.send_text(json.dumps(request_payload))
            first = json.loads(websocket.receive_text())
            second = json.loads(websocket.receive_text())

    assert first["type"] == "response.created"
    assert second["type"] == "response.completed"
    assert [json.loads(message) for message in fake_upstream.sent_text] == [
        {
            "model": "gpt-5.4",
            "instructions": "",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
            "tools": [function_tool],
            "store": False,
            "include": [],
            "type": "response.create",
        }
    ]


def test_backend_responses_websocket_accepts_and_reuses_generated_turn_state(app_instance, monkeypatch):
    upstream_messages = [
        _FakeUpstreamMessage(
            "text",
            text=json.dumps(
                {"type": "response.created", "response": {"id": "resp_turn_state", "status": "in_progress"}},
                separators=(",", ":"),
            ),
        ),
        _FakeUpstreamMessage(
            "text",
            text=json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_turn_state",
                        "status": "completed",
                        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                    },
                },
                separators=(",", ":"),
            ),
        ),
    ]
    fake_upstream = _FakeUpstreamWebSocket(upstream_messages)
    seen: dict[str, object] = {}

    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

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
        reallocate_sticky,
        sticky_max_age_seconds,
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
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset,
            routing_strategy,
            model,
            request_state,
            api_key,
            client_send_lock,
            websocket,
        )
        seen["headers"] = dict(headers)
        seen["sticky_key"] = sticky_key
        seen["sticky_kind"] = sticky_kind
        return SimpleNamespace(id="acct_turn_state"), fake_upstream

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect(
            "/backend-api/codex/responses",
            headers={"Authorization": "Bearer external-token"},
        ) as websocket:
            raw_extra_headers = cast(list[tuple[bytes, bytes]], websocket.extra_headers)
            extra_headers = {key.decode(): value.decode() for key, value in raw_extra_headers}
            turn_state = extra_headers["x-codex-turn-state"]
            websocket.send_text(json.dumps(request_payload))
            completed = json.loads(websocket.receive_text())
            assert completed["type"] == "response.created"
            _ = json.loads(websocket.receive_text())

    seen_headers = cast(dict[str, str], seen["headers"])
    assert turn_state
    assert seen_headers["x-codex-turn-state"] == turn_state
    assert seen["sticky_key"] == turn_state
    assert seen["sticky_kind"] == proxy_module.StickySessionKind.CODEX_SESSION


def test_backend_responses_websocket_echoes_existing_turn_state_header(app_instance, monkeypatch):
    fake_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.created", "response": {"id": "resp_existing_turn", "status": "in_progress"}},
                    separators=(",", ":"),
                ),
            ),
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_existing_turn",
                            "status": "completed",
                            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                        },
                    },
                    separators=(",", ":"),
                ),
            ),
        ]
    )
    seen: dict[str, object] = {}

    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

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
        reallocate_sticky,
        sticky_max_age_seconds,
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
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset,
            routing_strategy,
            model,
            request_state,
            api_key,
            client_send_lock,
            websocket,
        )
        seen["headers"] = dict(headers)
        seen["sticky_key"] = sticky_key
        seen["sticky_kind"] = sticky_kind
        return SimpleNamespace(id="acct_turn_state"), fake_upstream

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "stream": True,
    }
    existing_turn_state = "turn_state_existing_123"

    with TestClient(app_instance) as client:
        with client.websocket_connect(
            "/backend-api/codex/responses",
            headers={
                "Authorization": "Bearer external-token",
                "x-codex-turn-state": existing_turn_state,
            },
        ) as websocket:
            raw_extra_headers = cast(list[tuple[bytes, bytes]], websocket.extra_headers)
            extra_headers = {key.decode(): value.decode() for key, value in raw_extra_headers}
            assert extra_headers["x-codex-turn-state"] == existing_turn_state
            websocket.send_text(json.dumps(request_payload))
            _ = json.loads(websocket.receive_text())
            _ = json.loads(websocket.receive_text())

    seen_headers = cast(dict[str, str], seen["headers"])
    assert seen_headers["x-codex-turn-state"] == existing_turn_state
    assert seen["sticky_key"] == existing_turn_state
    assert seen["sticky_kind"] == proxy_module.StickySessionKind.CODEX_SESSION


def test_v1_responses_websocket_reuses_upstream_for_sequential_requests(app_instance, monkeypatch):
    first_upstream = _SequencedUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.created", "response": {"id": "resp_ws_first", "status": "in_progress"}},
                    separators=(",", ":"),
                ),
            ),
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_ws_first",
                            "status": "completed",
                            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                        },
                    },
                    separators=(",", ":"),
                ),
            ),
        ],
        deferred_message_batches=[
            [
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {"type": "response.created", "response": {"id": "resp_ws_second", "status": "in_progress"}},
                        separators=(",", ":"),
                    ),
                ),
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.completed",
                            "response": {
                                "id": "resp_ws_second",
                                "status": "completed",
                                "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
                            },
                        },
                        separators=(",", ":"),
                    ),
                ),
            ]
        ],
    )
    connect_calls: list[dict[str, object]] = []

    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

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
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset,
        routing_strategy,
        model,
        request_state,
        api_key,
        client_send_lock,
        websocket,
    ):
        del self, headers, request_state, api_key, client_send_lock, websocket
        connect_calls.append(
            {
                "sticky_key": sticky_key,
                "sticky_kind": sticky_kind,
                "reallocate_sticky": reallocate_sticky,
                "sticky_max_age_seconds": sticky_max_age_seconds,
                "prefer_earlier_reset": prefer_earlier_reset,
                "routing_strategy": routing_strategy,
                "model": model,
            }
        )
        return SimpleNamespace(id=f"acct_ws_proxy_{len(connect_calls)}"), first_upstream

    async def fake_write_request_log(self, **kwargs):
        del self, kwargs

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", fake_write_request_log)

    first_request = {
        "type": "response.create",
        "model": "gpt-5.4",
        "input": "first",
        "promptCacheKey": "thread_a",
        "stream": True,
    }
    second_request = {
        "type": "response.create",
        "model": "gpt-5.5",
        "input": "second",
        "promptCacheKey": "thread_b",
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/v1/responses") as websocket:
            raw_extra_headers = cast(list[tuple[bytes, bytes]], websocket.extra_headers)
            extra_headers = {key.decode(): value.decode() for key, value in raw_extra_headers}
            turn_state = extra_headers["x-codex-turn-state"]
            websocket.send_text(json.dumps(first_request))
            first_events = [json.loads(websocket.receive_text()) for _ in range(2)]

            websocket.send_text(json.dumps(second_request))
            second_events = [json.loads(websocket.receive_text()) for _ in range(2)]

    assert [event["type"] for event in first_events] == ["response.created", "response.completed"]
    assert [event["type"] for event in second_events] == ["response.created", "response.completed"]
    assert len(connect_calls) == 1
    assert connect_calls[0]["sticky_key"] == turn_state
    assert connect_calls[0]["sticky_kind"] == proxy_module.StickySessionKind.CODEX_SESSION
    assert connect_calls[0]["model"] == "gpt-5.4"
    assert [json.loads(message) for message in first_upstream.sent_text] == [
        {
            "model": "gpt-5.4",
            "instructions": "",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "first"}]}],
            "tools": [],
            "store": False,
            "include": [],
            "prompt_cache_key": "thread_a",
            "type": "response.create",
        },
        {
            "model": "gpt-5.5",
            "instructions": "",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "second"}]}],
            "tools": [],
            "store": False,
            "include": [],
            "prompt_cache_key": "thread_b",
            "type": "response.create",
        },
    ]


def test_v1_responses_websocket_accepts_and_reuses_generated_turn_state(app_instance, monkeypatch):
    fake_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.created", "response": {"id": "resp_v1_turn_state", "status": "in_progress"}},
                    separators=(",", ":"),
                ),
            ),
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_v1_turn_state",
                            "status": "completed",
                            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                        },
                    },
                    separators=(",", ":"),
                ),
            ),
        ]
    )
    seen: dict[str, object] = {}

    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

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
        reallocate_sticky,
        sticky_max_age_seconds,
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
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset,
            routing_strategy,
            model,
            request_state,
            api_key,
            client_send_lock,
            websocket,
        )
        seen["headers"] = dict(headers)
        seen["sticky_key"] = sticky_key
        seen["sticky_kind"] = sticky_kind
        return SimpleNamespace(id="acct_v1_turn_state"), fake_upstream

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "input": "hi",
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/v1/responses") as websocket:
            raw_extra_headers = cast(list[tuple[bytes, bytes]], websocket.extra_headers)
            extra_headers = {key.decode(): value.decode() for key, value in raw_extra_headers}
            turn_state = extra_headers["x-codex-turn-state"]
            websocket.send_text(json.dumps(request_payload))
            created = json.loads(websocket.receive_text())
            assert created["type"] == "response.created"
            _ = json.loads(websocket.receive_text())

    seen_headers = cast(dict[str, str], seen["headers"])
    assert turn_state
    assert seen_headers["x-codex-turn-state"] == turn_state
    assert seen["sticky_key"] == turn_state
    assert seen["sticky_kind"] == proxy_module.StickySessionKind.CODEX_SESSION


def test_v1_responses_websocket_normalizes_payload_before_forwarding(app_instance, monkeypatch):
    upstream_messages = [
        _FakeUpstreamMessage(
            "text",
            text=json.dumps(
                {"type": "response.created", "response": {"id": "resp_ws_v1", "status": "in_progress"}},
                separators=(",", ":"),
            ),
        ),
        _FakeUpstreamMessage(
            "text",
            text=json.dumps(
                {
                    "type": "response.completed",
                    "response": {
                        "id": "resp_ws_v1",
                        "status": "completed",
                        "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                    },
                },
                separators=(",", ":"),
            ),
        ),
    ]
    fake_upstream = _FakeUpstreamWebSocket(upstream_messages)
    seen: dict[str, object] = {}

    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

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
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset,
        routing_strategy,
        model,
        request_state,
        api_key,
        client_send_lock,
        websocket,
    ):
        del self, headers, request_state, api_key, client_send_lock, websocket
        seen["sticky_key"] = sticky_key
        seen["sticky_kind"] = sticky_kind
        seen["reallocate_sticky"] = reallocate_sticky
        seen["sticky_max_age_seconds"] = sticky_max_age_seconds
        seen["prefer_earlier_reset"] = prefer_earlier_reset
        seen["routing_strategy"] = routing_strategy
        seen["model"] = model
        return SimpleNamespace(id="acct_ws_proxy_v1"), fake_upstream

    async def fake_write_request_log(self, **kwargs):
        del self, kwargs

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", fake_write_request_log)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "input": "cache me",
        "promptCacheKey": "thread_alias",
        "promptCacheRetention": "12h",
        "tools": [{"type": "web_search_preview"}],
        "service_tier": "priority",
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/v1/responses") as websocket:
            raw_extra_headers = cast(list[tuple[bytes, bytes]], websocket.extra_headers)
            extra_headers = {key.decode(): value.decode() for key, value in raw_extra_headers}
            turn_state = extra_headers["x-codex-turn-state"]
            websocket.send_text(json.dumps(request_payload))
            first = json.loads(websocket.receive_text())
            second = json.loads(websocket.receive_text())

    assert first["type"] == "response.created"
    assert second["type"] == "response.completed"
    assert seen["sticky_key"] == turn_state
    assert seen["sticky_kind"] == proxy_module.StickySessionKind.CODEX_SESSION
    assert seen["reallocate_sticky"] is False
    assert seen["sticky_max_age_seconds"] is None
    assert seen["model"] == "gpt-5.4"
    assert [json.loads(message) for message in fake_upstream.sent_text] == [
        {
            "model": "gpt-5.4",
            "instructions": "",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "cache me"}]}],
            "tools": [{"type": "web_search"}],
            "service_tier": "priority",
            "store": False,
            "include": [],
            "prompt_cache_key": "thread_alias",
            "type": "response.create",
        }
    ]


def test_v1_responses_websocket_rejects_invalid_payload_before_connect(app_instance, monkeypatch):
    called = {"connect": False}

    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

    async def allow_firewall(_websocket):
        return None

    async def allow_proxy_api_key(_authorization: str | None):
        return None

    async def fail_connect_proxy_websocket(*args, **kwargs):
        del args, kwargs
        called["connect"] = True
        raise AssertionError("invalid websocket payload must not open upstream")

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fail_connect_proxy_websocket)

    with TestClient(app_instance) as client:
        with client.websocket_connect("/v1/responses") as websocket:
            websocket.send_text(
                json.dumps(
                    {
                        "type": "response.create",
                        "model": "gpt-5.4",
                        "input": "hi",
                        "store": True,
                    }
                )
            )
            json.loads(websocket.receive_text())

    assert called["connect"] is False


def test_backend_responses_websocket_forwards_previous_response_id(app_instance, monkeypatch):
    fake_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.created", "response": {"id": "resp_ws_prev", "status": "in_progress"}},
                    separators=(",", ":"),
                ),
            ),
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.completed", "response": {"id": "resp_ws_prev", "status": "completed"}},
                    separators=(",", ":"),
                ),
            ),
        ]
    )
    seen: dict[str, object] = {}

    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

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
        reallocate_sticky=False,
        sticky_max_age_seconds=None,
    ):
        del self, sticky_key, sticky_kind, prefer_earlier_reset, routing_strategy, model
        del request_state, api_key, client_send_lock, websocket, reallocate_sticky, sticky_max_age_seconds
        seen["headers"] = dict(headers)
        return SimpleNamespace(id="acct_ws_prev"), fake_upstream

    async def fake_write_request_log(self, **kwargs):
        del self, kwargs

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", fake_write_request_log)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "instructions": "",
        "previous_response_id": "resp_prev_123",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "continue"}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect(
            "/backend-api/codex/responses",
            headers={
                "Authorization": "Bearer external-token",
                "session_id": "thread-ws-prev-1",
                "openai-beta": "responses_websockets=2026-02-06",
            },
        ) as websocket:
            websocket.send_text(json.dumps(request_payload))
            first = json.loads(websocket.receive_text())
            second = json.loads(websocket.receive_text())

    assert first["type"] == "response.created"
    assert second["type"] == "response.completed"
    assert [json.loads(message) for message in fake_upstream.sent_text] == [
        {
            "model": "gpt-5.4",
            "instructions": "",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "continue"}]}],
            "tools": [],
            "store": False,
            "include": [],
            "previous_response_id": "resp_prev_123",
            "type": "response.create",
        }
    ]


def test_v1_responses_websocket_forwards_previous_response_id(app_instance, monkeypatch):
    fake_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.created", "response": {"id": "resp_ws_v1_prev", "status": "in_progress"}},
                    separators=(",", ":"),
                ),
            ),
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.completed", "response": {"id": "resp_ws_v1_prev", "status": "completed"}},
                    separators=(",", ":"),
                ),
            ),
        ]
    )
    seen: dict[str, object] = {}

    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

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
        reallocate_sticky=False,
        sticky_max_age_seconds=None,
    ):
        del self, headers, sticky_key, sticky_kind, prefer_earlier_reset, routing_strategy, model
        del request_state, api_key, client_send_lock, websocket, reallocate_sticky, sticky_max_age_seconds
        seen["connected"] = True
        return SimpleNamespace(id="acct_ws_v1_prev"), fake_upstream

    async def fake_write_request_log(self, **kwargs):
        del self, kwargs

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", fake_write_request_log)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "input": "continue",
        "previous_response_id": "resp_prev_v1_123",
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/v1/responses") as websocket:
            websocket.send_text(json.dumps(request_payload))
            first = json.loads(websocket.receive_text())
            second = json.loads(websocket.receive_text())

    assert seen["connected"] is True
    assert first["type"] == "response.created"
    assert second["type"] == "response.completed"
    assert [json.loads(message) for message in fake_upstream.sent_text] == [
        {
            "model": "gpt-5.4",
            "instructions": "",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "continue"}]}],
            "tools": [],
            "store": False,
            "include": [],
            "previous_response_id": "resp_prev_v1_123",
            "type": "response.create",
        }
    ]


@pytest.mark.parametrize("frame", ['{"type":"response.create"', "[]"])
def test_backend_responses_websocket_rejects_malformed_first_frame_as_invalid_payload(app_instance, monkeypatch, frame):
    called = {"connect": False}

    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

    async def allow_firewall(_websocket):
        return None

    async def allow_proxy_api_key(_authorization: str | None):
        return None

    async def fail_connect_proxy_websocket(*args, **kwargs):
        del args, kwargs
        called["connect"] = True
        raise AssertionError("malformed initial websocket frame must not open upstream")

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fail_connect_proxy_websocket)

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(frame)
            event = json.loads(websocket.receive_text())

    assert called["connect"] is False
    assert event["type"] == "error"
    assert event["status"] == 400
    assert event["error"]["type"] == "invalid_request_error"
    assert event["error"]["message"] == "Invalid request payload"


def test_backend_responses_websocket_emits_timeout_failure_for_stalled_upstream(app_instance, monkeypatch):
    fake_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.created", "response": {"id": "resp_ws_idle", "status": "in_progress"}},
                    separators=(",", ":"),
                ),
            ),
        ]
    )
    log_calls: list[dict[str, object]] = []
    connect_attempts = {"count": 0}

    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

    runtime_settings = _websocket_settings(
        proxy_request_budget_seconds=5.0,
        stream_idle_timeout_seconds=0.01,
    )

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
        reallocate_sticky,
        sticky_max_age_seconds,
        prefer_earlier_reset,
        routing_strategy,
        model,
        request_state,
        api_key,
        client_send_lock,
        websocket,
    ):
        del self, headers, sticky_key, sticky_kind, reallocate_sticky, sticky_max_age_seconds
        del prefer_earlier_reset, routing_strategy, model, api_key
        connect_attempts["count"] += 1
        if connect_attempts["count"] == 1:
            del client_send_lock, websocket, request_state
            return SimpleNamespace(id="acct_ws_proxy"), fake_upstream
        async with client_send_lock:
            await websocket.send_text(json.dumps({"type": "error", "status": 503, "error": {"code": "no_accounts"}}))
        return None, None

    async def fake_write_request_log(self, **kwargs):
        del self
        log_calls.append(kwargs)

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module, "get_settings", lambda: runtime_settings)
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", fake_write_request_log)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(json.dumps(request_payload))
            created_event = json.loads(websocket.receive_text())
            failed_event = json.loads(websocket.receive_text())

            websocket.send_text(json.dumps(request_payload))
            followup_event = json.loads(websocket.receive_text())

    assert created_event["type"] == "response.created"
    assert failed_event["type"] == "response.failed"
    assert failed_event["response"]["id"] == "resp_ws_idle"
    assert failed_event["response"]["error"]["code"] == "stream_idle_timeout"
    assert failed_event["response"]["error"]["message"] == "Upstream stream idle timeout"
    assert fake_upstream.closed is True
    assert connect_attempts["count"] == 2
    assert followup_event["type"] == "error"
    assert followup_event["status"] == 503
    assert followup_event["error"]["code"] == "no_accounts"
    assert len(log_calls) == 1
    assert log_calls[0]["request_id"] == "resp_ws_idle"
    assert log_calls[0]["error_code"] == "stream_idle_timeout"
    assert log_calls[0]["error_message"] == "Upstream stream idle timeout"


def test_backend_responses_websocket_emits_terminal_failure_when_upstream_send_breaks(app_instance, monkeypatch):
    fake_upstream = _FailingSendUpstreamWebSocket([])
    log_calls: list[dict[str, object]] = []

    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

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
        reallocate_sticky,
        sticky_max_age_seconds,
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
            reallocate_sticky,
            sticky_max_age_seconds,
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
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(json.dumps(request_payload))
            failed_event = json.loads(websocket.receive_text())

    assert failed_event["type"] == "response.failed"
    assert failed_event["response"]["error"]["code"] == "stream_incomplete"
    assert failed_event["response"]["error"]["message"] == "Upstream websocket closed before response.completed"
    assert len(log_calls) == 1
    assert log_calls[0]["error_code"] == "stream_incomplete"
    assert log_calls[0]["status"] == "error"


def test_backend_responses_websocket_rejects_oversized_response_create_before_upstream(
    app_instance,
    monkeypatch,
    tmp_path,
):
    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

    async def allow_firewall(_websocket):
        return None

    async def allow_proxy_api_key(_authorization: str | None):
        return None

    async def fail_connect_proxy_websocket(
        self,
        headers,
        *,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
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
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset,
            routing_strategy,
            model,
            request_state,
            api_key,
            client_send_lock,
            websocket,
        )
        raise AssertionError("oversized response.create must fail before upstream websocket connect")

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module, "_UPSTREAM_RESPONSE_CREATE_WARN_BYTES", 64)
    monkeypatch.setattr(proxy_module, "_UPSTREAM_RESPONSE_CREATE_MAX_BYTES", 128)
    monkeypatch.setattr(proxy_module, "_OVERSIZED_RESPONSE_CREATE_DUMP_DIR", tmp_path)
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fail_connect_proxy_websocket)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "x" * 256}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(json.dumps(request_payload))
            error_event = json.loads(websocket.receive_text())

    assert error_event["type"] == "error"
    assert error_event["status"] == 413
    assert error_event["error"]["code"] == "payload_too_large"
    assert error_event["error"]["type"] == "invalid_request_error"
    assert error_event["error"]["param"] == "input"
    assert "response.create is too large for upstream websocket" in error_event["error"]["message"]

    meta_files = list(tmp_path.glob("*.meta.json"))
    assert len(meta_files) == 1
    meta = json.loads(meta_files[0].read_text(encoding="utf-8"))
    assert meta["reason"]["error_code"] == "payload_too_large"
    assert meta["request"]["transport"] == "websocket"
    assert meta["request"]["request_text_bytes"] > 128


def test_backend_responses_websocket_slims_historical_inline_artifacts_and_succeeds(
    app_instance,
    monkeypatch,
):
    fake_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.created",
                        "response": {"id": "resp_ws_slim", "object": "response", "status": "in_progress"},
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
                            "id": "resp_ws_slim",
                            "object": "response",
                            "status": "completed",
                            "usage": {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
                        },
                    },
                    separators=(",", ":"),
                ),
            ),
        ]
    )

    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

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
        reallocate_sticky,
        sticky_max_age_seconds,
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
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset,
            routing_strategy,
            model,
            request_state,
            api_key,
            client_send_lock,
            websocket,
        )
        return SimpleNamespace(id="acct_ws_proxy"), fake_upstream

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module, "_UPSTREAM_RESPONSE_CREATE_WARN_BYTES", 64)
    monkeypatch.setattr(proxy_module, "_UPSTREAM_RESPONSE_CREATE_MAX_BYTES", 512)
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "instructions": "",
        "input": [
            {"role": "user", "content": [{"type": "input_text", "text": "old turn"}]},
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": "data:image/png;base64," + ("A" * 1500),
            },
            {"role": "assistant", "content": [{"type": "output_text", "text": "done"}]},
            {"role": "user", "content": [{"type": "input_text", "text": "ping"}]},
        ],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(json.dumps(request_payload))
            created_event = json.loads(websocket.receive_text())
            completed_event = json.loads(websocket.receive_text())

    assert created_event["type"] == "response.created"
    assert completed_event["type"] == "response.completed"
    sent_payload = json.loads(fake_upstream.sent_text[0])
    assert sent_payload["input"][-1]["content"][0]["text"] == "ping"
    assert "data:image/" not in json.dumps(sent_payload["input"], ensure_ascii=True)
    assert "historical tool output" in json.dumps(sent_payload["input"], ensure_ascii=True)


def test_backend_responses_websocket_keeps_downstream_open_after_clean_upstream_close(app_instance, monkeypatch):
    first_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.created", "response": {"id": "resp_ws_first", "status": "in_progress"}},
                    separators=(",", ":"),
                ),
            ),
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_ws_first",
                            "status": "completed",
                            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                        },
                    },
                    separators=(",", ":"),
                ),
            ),
            _FakeUpstreamMessage("close", close_code=1000),
        ]
    )
    second_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.created", "response": {"id": "resp_ws_second", "status": "in_progress"}},
                    separators=(",", ":"),
                ),
            ),
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_ws_second",
                            "status": "completed",
                            "usage": {"input_tokens": 2, "output_tokens": 2, "total_tokens": 4},
                        },
                    },
                    separators=(",", ":"),
                ),
            ),
        ]
    )
    upstreams = [first_upstream, second_upstream]
    connect_models: list[str | None] = []

    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

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
        reallocate_sticky,
        sticky_max_age_seconds,
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
            reallocate_sticky,
            sticky_max_age_seconds,
            prefer_earlier_reset,
            routing_strategy,
            request_state,
            api_key,
            client_send_lock,
            websocket,
        )
        connect_models.append(model)
        return SimpleNamespace(id=f"acct_ws_proxy_{len(connect_models)}"), upstreams[len(connect_models) - 1]

    async def fake_write_request_log(self, **kwargs):
        del self, kwargs

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", fake_write_request_log)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "stream": True,
    }
    second_request = {
        "type": "response.create",
        "model": "gpt-5.5",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "again"}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(json.dumps(request_payload))
            first_events = [json.loads(websocket.receive_text()) for _ in range(2)]

            websocket.send_text(json.dumps(second_request))
            second_events = [json.loads(websocket.receive_text()) for _ in range(2)]

    assert [event["type"] for event in first_events] == ["response.created", "response.completed"]
    assert [event["type"] for event in second_events] == ["response.created", "response.completed"]
    assert connect_models == ["gpt-5.4", "gpt-5.5"]


def test_backend_responses_websocket_reclaims_idle_downstream_session_and_upstream(app_instance, monkeypatch):
    fake_upstream = _FakeUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {"type": "response.created", "response": {"id": "resp_ws_idle_client", "status": "in_progress"}},
                    separators=(",", ":"),
                ),
            ),
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.completed",
                        "response": {
                            "id": "resp_ws_idle_client",
                            "status": "completed",
                            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                        },
                    },
                    separators=(",", ":"),
                ),
            ),
        ]
    )
    log_calls: list[dict[str, object]] = []

    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

    runtime_settings = _websocket_settings(proxy_downstream_websocket_idle_timeout_seconds=0.1)

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
        reallocate_sticky,
        sticky_max_age_seconds,
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
            reallocate_sticky,
            sticky_max_age_seconds,
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
    monkeypatch.setattr(proxy_module, "get_settings", lambda: runtime_settings)
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", fake_write_request_log)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(json.dumps(request_payload))
            created_event = json.loads(websocket.receive_text())
            completed_event = json.loads(websocket.receive_text())

            with pytest.raises(WebSocketDisconnect) as exc_info:
                websocket.receive_text()

    assert created_event["type"] == "response.created"
    assert completed_event["type"] == "response.completed"
    assert exc_info.value.code == 1001
    assert exc_info.value.reason == "Idle downstream websocket timeout"
    assert fake_upstream.closed is True
    assert len(log_calls) == 1
    assert log_calls[0]["request_id"] == "resp_ws_idle_client"
    assert log_calls[0]["status"] == "success"


def test_backend_responses_websocket_does_not_expire_downstream_while_request_pending(app_instance, monkeypatch):
    fake_upstream = _DelayedUpstreamWebSocket(
        [
            _FakeUpstreamMessage(
                "text",
                text=json.dumps(
                    {
                        "type": "response.created",
                        "response": {"id": "resp_ws_pending", "status": "in_progress"},
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
                            "id": "resp_ws_pending",
                            "status": "completed",
                            "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                        },
                    },
                    separators=(",", ":"),
                ),
            ),
        ],
        delays=[0.12, 0.12],
    )
    log_calls: list[dict[str, object]] = []

    class _FakeSettingsCache:
        async def get(self):
            return _websocket_settings()

    runtime_settings = _websocket_settings(
        proxy_downstream_websocket_idle_timeout_seconds=0.1,
        stream_idle_timeout_seconds=0.2,
    )

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
        reallocate_sticky,
        sticky_max_age_seconds,
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
            reallocate_sticky,
            sticky_max_age_seconds,
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
    monkeypatch.setattr(proxy_module, "get_settings", lambda: runtime_settings)
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", fake_write_request_log)

    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            websocket.send_text(json.dumps(request_payload))
            created_event = json.loads(websocket.receive_text())
            completed_event = json.loads(websocket.receive_text())

    assert created_event["type"] == "response.created"
    assert completed_event["type"] == "response.completed"
    assert fake_upstream.closed is True
    assert len(log_calls) == 1
    assert log_calls[0]["request_id"] == "resp_ws_pending"
    assert log_calls[0]["status"] == "success"


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
            return _websocket_settings()

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
        reallocate_sticky,
        sticky_max_age_seconds,
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
            reallocate_sticky,
            sticky_max_age_seconds,
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

    async def fake_write_request_log(self, **kwargs):
        del self, kwargs

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_api_key)
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _FakeSettingsCache())
    monkeypatch.setattr(proxy_module.ProxyService, "_connect_proxy_websocket", fake_connect_proxy_websocket)
    monkeypatch.setattr(proxy_module.ProxyService, "_handle_stream_error", fake_handle_stream_error)
    monkeypatch.setattr(proxy_module.ProxyService, "_write_request_log", fake_write_request_log)

    first_request = {
        "type": "response.create",
        "model": "gpt-5.1",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "first"}]}],
        "stream": True,
    }
    second_request = {
        "type": "response.create",
        "model": "gpt-5.2",
        "instructions": "",
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
    assert [json.loads(message) for message in first_upstream.sent_text] == [
        {
            "model": "gpt-5.1",
            "instructions": "",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "first"}]}],
            "tools": [],
            "store": False,
            "include": [],
            "type": "response.create",
        }
    ]
    assert [json.loads(message) for message in second_upstream.sent_text] == [
        {
            "model": "gpt-5.2",
            "instructions": "",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "second"}]}],
            "tools": [],
            "store": False,
            "include": [],
            "type": "response.create",
        }
    ]


def test_backend_responses_websocket_emits_no_accounts_error(app_instance, monkeypatch):
    request_payload = {
        "type": "response.create",
        "model": "gpt-5.4",
        "instructions": "",
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
            return _websocket_settings()

    async def fake_connect_proxy_websocket(
        self,
        headers,
        *,
        sticky_key,
        sticky_kind,
        reallocate_sticky,
        sticky_max_age_seconds,
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
            reallocate_sticky,
            sticky_max_age_seconds,
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
            return _websocket_settings()

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
        reallocate_sticky,
        sticky_max_age_seconds,
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
            reallocate_sticky,
            sticky_max_age_seconds,
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
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "first"}]}],
        "stream": True,
    }
    second_request = {
        "type": "response.create",
        "model": "gpt-5.2",
        "instructions": "",
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
            return _websocket_settings()

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
        reallocate_sticky,
        sticky_max_age_seconds,
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
            reallocate_sticky,
            sticky_max_age_seconds,
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
        "instructions": "",
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

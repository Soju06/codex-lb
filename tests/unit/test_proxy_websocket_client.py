from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from websockets.datastructures import Headers
from websockets.exceptions import InvalidHandshake, InvalidProxy, InvalidStatus
from websockets.http11 import Response

import app.core.clients.proxy_websocket as proxy_websocket_module
from app.core.clients.proxy import ProxyResponseError
from app.core.clients.proxy_websocket import connect_responses_websocket


class _UnexpectedAiohttpSession:
    async def ws_connect(self, *args, **kwargs):  # pragma: no cover - red-path guard
        raise AssertionError("aiohttp ws_connect should not be used for upstream websocket transport")


class _UnexpectedHttpClient:
    websocket_session = _UnexpectedAiohttpSession()


class _FakeConnection:
    def __init__(self) -> None:
        self.sent: list[str | bytes] = []
        self.closed = False

    async def send(self, data: str | bytes) -> None:
        self.sent.append(data)

    async def recv(self) -> str:
        return '{"type":"response.completed"}'

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_connect_responses_websocket_uses_websockets_transport(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            upstream_websocket_trust_env=False,
            max_sse_event_bytes=4321,
        ),
    )

    websocket = await connect_responses_websocket(
        {
            "openai-beta": "responses_websockets=2026-02-06",
            "session_id": "session-1",
            "User-Agent": "Codex CLI Test",
            "Origin": "https://chatgpt.com",
            "Cookie": "dashboard_session=secret",
        },
        "access-token",
        "account-123",
    )

    await websocket.send_text("hello")

    assert fake_connection.sent == ["hello"]
    assert seen["url"] == "wss://chatgpt.com/backend-api/codex/responses"
    kwargs = cast(dict[str, object], seen["kwargs"])
    assert kwargs["origin"] == "https://chatgpt.com"
    assert kwargs["user_agent_header"] == "Codex CLI Test"
    assert kwargs["open_timeout"] == 7.0
    assert kwargs["max_size"] == 4321
    assert kwargs["proxy"] is None
    additional_headers = cast(dict[str, str], kwargs["additional_headers"])
    assert additional_headers["Authorization"] == "Bearer access-token"
    assert additional_headers["chatgpt-account-id"] == "account-123"
    assert additional_headers["openai-beta"] == "responses_websockets=2026-02-06"
    assert additional_headers["session_id"] == "session-1"
    assert "Cookie" not in additional_headers
    assert "User-Agent" not in additional_headers
    assert "Origin" not in additional_headers


@pytest.mark.asyncio
async def test_connect_responses_websocket_maps_invalid_status(monkeypatch):
    async def fake_websocket_connect(url: str, **kwargs):
        raise InvalidStatus(
            Response(
                403,
                "Forbidden",
                Headers({"Content-Type": "application/json"}),
                b'{"error":{"message":"Forbidden","type":"permission_error","code":"forbidden"}}',
            )
        )

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            upstream_websocket_trust_env=False,
            max_sse_event_bytes=4321,
        ),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await connect_responses_websocket(
            {"openai-beta": "responses_websockets=2026-02-06"},
            "access-token",
            "account-123",
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.payload["error"]["code"] == "forbidden"
    assert exc_info.value.payload["error"]["type"] == "permission_error"


@pytest.mark.asyncio
async def test_connect_responses_websocket_honors_trust_env_proxy_setting(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            upstream_websocket_trust_env=True,
            max_sse_event_bytes=4321,
        ),
    )

    await connect_responses_websocket(
        {"openai-beta": "responses_websockets=2026-02-06"},
        "access-token",
        "account-123",
    )

    kwargs = cast(dict[str, object], seen["kwargs"])
    assert kwargs["proxy"] is True


@pytest.mark.asyncio
async def test_connect_responses_websocket_maps_invalid_handshake(monkeypatch):
    async def fake_websocket_connect(url: str, **kwargs):
        del url, kwargs
        raise InvalidHandshake("missing upgrade headers")

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            upstream_websocket_trust_env=False,
            max_sse_event_bytes=4321,
        ),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await connect_responses_websocket(
            {"openai-beta": "responses_websockets=2026-02-06"},
            "access-token",
            "account-123",
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.payload["error"]["code"] == "upstream_unavailable"


@pytest.mark.asyncio
async def test_connect_responses_websocket_maps_invalid_proxy(monkeypatch):
    async def fake_websocket_connect(url: str, **kwargs):
        del url, kwargs
        raise InvalidProxy("http://proxy.invalid", "unsupported proxy scheme")

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            upstream_websocket_trust_env=True,
            max_sse_event_bytes=4321,
        ),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await connect_responses_websocket(
            {"openai-beta": "responses_websockets=2026-02-06"},
            "access-token",
            "account-123",
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.payload["error"]["code"] == "upstream_unavailable"

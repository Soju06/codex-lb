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


def _proxy_error_code(exc: ProxyResponseError) -> str | None:
    return exc.payload["error"].get("code")


def _proxy_error_message(exc: ProxyResponseError) -> str | None:
    return exc.payload["error"].get("message")


def _proxy_error_type(exc: ProxyResponseError) -> str | None:
    return exc.payload["error"].get("type")


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
    archived: list[dict[str, object]] = []

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(proxy_websocket_module, "archive_text", lambda **kwargs: archived.append(kwargs))
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
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

    await websocket.send_text_with_archive_request_id("hello", request_id="req_log_1")
    await websocket.receive()

    assert fake_connection.sent == ["hello"]
    assert [record["request_id"] for record in archived] == ["req_log_1"]
    assert seen["url"] == "wss://chatgpt.com/backend-api/codex/responses"
    kwargs = cast(dict[str, object], seen["kwargs"])
    assert kwargs["origin"] == "https://chatgpt.com"
    assert kwargs["user_agent_header"] == "Codex CLI Test"
    assert kwargs["proxy"] is None
    assert kwargs["open_timeout"] == 7.0
    assert kwargs["max_size"] == 4321
    additional_headers = cast(dict[str, str], kwargs["additional_headers"])
    assert additional_headers["Authorization"] == "Bearer access-token"
    assert additional_headers["chatgpt-account-id"] == "account-123"
    assert additional_headers["openai-beta"] == "responses_websockets=2026-02-06"
    assert additional_headers["session_id"] == "session-1"
    assert "Cookie" not in additional_headers
    assert "User-Agent" not in additional_headers
    assert "Origin" not in additional_headers


@pytest.mark.asyncio
async def test_connect_responses_websocket_appends_required_beta_header(monkeypatch):
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
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )

    await connect_responses_websocket(
        {"OpenAI-Beta": "assistants=v2"},
        "access-token",
        None,
    )

    kwargs = cast(dict[str, object], seen["kwargs"])
    additional_headers = cast(dict[str, str], kwargs["additional_headers"])
    assert additional_headers["OpenAI-Beta"] == "assistants=v2, responses_websockets=2026-02-06"


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
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await connect_responses_websocket(
            {"openai-beta": "responses_websockets=2026-02-06"},
            "access-token",
            "account-123",
        )

    assert exc_info.value.status_code == 403
    assert _proxy_error_code(exc_info.value) == "forbidden"
    assert _proxy_error_type(exc_info.value) == "permission_error"


@pytest.mark.asyncio
async def test_connect_responses_websocket_can_opt_in_to_env_proxy(monkeypatch):
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
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )

    await connect_responses_websocket({"openai-beta": "responses_websockets=2026-02-06"}, "access-token", None)

    kwargs = cast(dict[str, object], seen["kwargs"])
    assert kwargs["proxy"] is True


@pytest.mark.asyncio
async def test_connect_responses_websocket_maps_generic_invalid_handshake(monkeypatch):
    async def fake_websocket_connect(url: str, **kwargs):
        del url, kwargs
        raise InvalidHandshake("proxy CONNECT failed")

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await connect_responses_websocket(
            {"openai-beta": "responses_websockets=2026-02-06"},
            "access-token",
            "account-123",
        )

    assert exc_info.value.status_code == 502
    assert _proxy_error_code(exc_info.value) == "upstream_unavailable"
    assert _proxy_error_message(exc_info.value) == "proxy CONNECT failed"


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
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await connect_responses_websocket(
            {"openai-beta": "responses_websockets=2026-02-06"},
            "access-token",
            "account-123",
        )

    assert exc_info.value.status_code == 502
    assert _proxy_error_code(exc_info.value) == "upstream_unavailable"

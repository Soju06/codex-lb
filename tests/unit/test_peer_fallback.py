from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from fastapi.responses import Response
from pydantic import SecretStr
from starlette.requests import Request

from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy import peer_fallback

pytestmark = pytest.mark.unit


async def _response_body(response: Response) -> bytes:
    body_iterator = getattr(response, "body_iterator", None)
    if body_iterator is None:
        return bytes(response.body)
    chunks: list[bytes] = []
    async for chunk in body_iterator:
        chunks.append(chunk.encode("utf-8") if isinstance(chunk, str) else chunk)
    return b"".join(chunks)


class _FakeHealthResponse:
    status = 204

    async def __aenter__(self) -> _FakeHealthResponse:
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class _FakeStreamContent:
    async def iter_chunked(self, _size: int):
        yield b"peer-stream"


class _FakePeerResponse:
    status = 201
    headers = {
        "content-type": "text/event-stream",
        "content-length": "999",
        "x-peer": "selected",
    }

    def __init__(self) -> None:
        self.content = _FakeStreamContent()
        self.released = False

    def release(self) -> None:
        self.released = True


class _FakeSession:
    def __init__(self) -> None:
        self.peer_response = _FakePeerResponse()
        self.health_url: str | None = None
        self.request_call: dict[str, object] | None = None

    def get(self, url: str, *, timeout) -> _FakeHealthResponse:
        self.health_url = url
        return _FakeHealthResponse()

    async def request(
        self,
        method: str,
        url: str,
        *,
        json,
        headers,
        timeout,
        auto_decompress: bool = True,
    ) -> _FakePeerResponse:
        self.request_call = {
            "method": method,
            "url": url,
            "json": json,
            "headers": headers,
            "timeout": timeout,
            "auto_decompress": auto_decompress,
        }
        return self.peer_response


@pytest.mark.asyncio
async def test_open_stream_response_forwards_original_request_shape(monkeypatch):
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/backend-api/codex/responses",
            "query_string": b"conversation=abc",
            "headers": [
                (b"authorization", b"Bearer local-key"),
                (b"host", b"local.example"),
                (b"content-length", b"123"),
                (b"connection", b"keep-alive"),
                (b"cookie", b"codex_lb_session=sensitive"),
                (b"x-client", b"client-value"),
            ],
            "client": ("10.0.0.12", 12345),
        }
    )
    session = _FakeSession()
    settings = SimpleNamespace(
        peer_fallback_base_urls=["http://peer.example"],
        peer_fallback_error_codes=["no_accounts"],
        peer_fallback_max_hops=1,
        peer_fallback_timeout_seconds=1.0,
    )
    payload = {"model": "gpt-5.4", "input": "hi", "stream": True}

    monkeypatch.setattr(peer_fallback, "get_settings", lambda: settings)
    monkeypatch.setattr(peer_fallback, "get_http_client", lambda: SimpleNamespace(session=session))

    response = await peer_fallback.open_stream_response(
        request,
        payload,
        reason_code="no_accounts",
        api_key=cast(ApiKeyData, SimpleNamespace(peer_fallback_base_urls=["http://peer.example"])),
    )

    assert response is not None
    assert session.health_url == "http://peer.example/health"
    assert session.request_call is not None
    assert session.request_call["method"] == "POST"
    assert session.request_call["url"] == "http://peer.example/backend-api/codex/responses?conversation=abc"
    assert session.request_call["json"] == payload
    assert session.request_call["auto_decompress"] is False
    assert getattr(session.request_call["timeout"], "total") is None
    forwarded_headers = cast(dict[str, str], session.request_call["headers"])
    assert forwarded_headers["authorization"] == "Bearer local-key"
    assert forwarded_headers["x-client"] == "client-value"
    assert "host" not in forwarded_headers
    assert "content-length" not in forwarded_headers
    assert "connection" not in forwarded_headers
    assert "cookie" not in forwarded_headers
    assert forwarded_headers[peer_fallback.PEER_FALLBACK_DEPTH_HEADER] == "1"
    assert forwarded_headers[peer_fallback.PEER_FALLBACK_REASON_HEADER] == "no_accounts"
    assert response.status_code == 201
    assert response.headers["x-peer"] == "selected"
    assert "content-length" not in response.headers
    assert await _response_body(response) == b"peer-stream"
    assert session.peer_response.released


@pytest.mark.asyncio
async def test_open_stream_response_uses_codex_api_key_for_peer_authorization(monkeypatch):
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/backend-api/codex/responses",
            "query_string": b"",
            "headers": [
                (b"authorization", b"Bearer local-key"),
                (b"x-client", b"client-value"),
            ],
            "client": ("10.0.0.12", 12345),
        }
    )
    session = _FakeSession()
    settings = SimpleNamespace(
        codex_api_key=SecretStr("peer-secret-key"),
        peer_fallback_base_urls=["http://peer.example"],
        peer_fallback_error_codes=["no_accounts"],
        peer_fallback_max_hops=1,
        peer_fallback_timeout_seconds=1.0,
    )

    monkeypatch.setattr(peer_fallback, "get_settings", lambda: settings)
    monkeypatch.setattr(peer_fallback, "get_http_client", lambda: SimpleNamespace(session=session))

    response = await peer_fallback.open_stream_response(
        request,
        {"model": "gpt-5.4", "input": "hi", "stream": True},
        reason_code="no_accounts",
        api_key=cast(ApiKeyData, SimpleNamespace(peer_fallback_base_urls=["http://peer.example"])),
    )

    assert response is not None
    assert session.request_call is not None
    forwarded_headers = cast(dict[str, str], session.request_call["headers"])
    assert forwarded_headers["authorization"] == "Bearer peer-secret-key"
    assert forwarded_headers["x-client"] == "client-value"


@pytest.mark.asyncio
async def test_open_stream_response_uses_api_key_peer_fallback_urls(monkeypatch):
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/backend-api/codex/responses",
            "query_string": b"",
            "headers": [],
            "client": ("10.0.0.12", 12345),
        }
    )
    session = _FakeSession()
    settings = SimpleNamespace(
        peer_fallback_base_urls=["http://env-peer.example"],
        peer_fallback_error_codes=["no_accounts"],
        peer_fallback_max_hops=1,
        peer_fallback_timeout_seconds=1.0,
    )

    monkeypatch.setattr(peer_fallback, "get_settings", lambda: settings)
    monkeypatch.setattr(peer_fallback, "get_http_client", lambda: SimpleNamespace(session=session))

    response = await peer_fallback.open_stream_response(
        request,
        {"model": "gpt-5.4", "input": "hi", "stream": True},
        reason_code="no_accounts",
        api_key=cast(ApiKeyData, SimpleNamespace(peer_fallback_base_urls=["http://assigned-peer.example"])),
    )

    assert response is not None
    assert session.health_url == "http://assigned-peer.example/health"
    assert session.request_call is not None
    assert session.request_call["url"] == "http://assigned-peer.example/backend-api/codex/responses"


@pytest.mark.asyncio
async def test_open_stream_response_without_api_key_does_not_use_env(monkeypatch):
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/backend-api/codex/responses",
            "headers": [],
            "client": ("10.0.0.12", 12345),
        }
    )
    session = _FakeSession()
    settings = SimpleNamespace(
        peer_fallback_base_urls=["http://env-peer.example"],
        peer_fallback_error_codes=["no_accounts"],
        peer_fallback_max_hops=1,
        peer_fallback_timeout_seconds=1.0,
    )

    monkeypatch.setattr(peer_fallback, "get_settings", lambda: settings)
    monkeypatch.setattr(peer_fallback, "get_http_client", lambda: SimpleNamespace(session=session))

    response = await peer_fallback.open_stream_response(
        request,
        {"model": "gpt-5.4", "input": "hi", "stream": True},
        reason_code="no_accounts",
    )

    assert response is None
    assert session.request_call is None


@pytest.mark.asyncio
async def test_open_stream_response_without_api_key_peer_fallback_urls_does_not_use_env(monkeypatch):
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/backend-api/codex/responses",
            "headers": [],
            "client": ("10.0.0.12", 12345),
        }
    )
    session = _FakeSession()
    settings = SimpleNamespace(
        peer_fallback_base_urls=["http://env-peer.example"],
        peer_fallback_error_codes=["no_accounts"],
        peer_fallback_max_hops=1,
        peer_fallback_timeout_seconds=1.0,
    )

    monkeypatch.setattr(peer_fallback, "get_settings", lambda: settings)
    monkeypatch.setattr(peer_fallback, "get_http_client", lambda: SimpleNamespace(session=session))

    response = await peer_fallback.open_stream_response(
        request,
        {"model": "gpt-5.4", "input": "hi", "stream": True},
        reason_code="no_accounts",
        api_key=cast(ApiKeyData, SimpleNamespace(peer_fallback_base_urls=[])),
    )

    assert response is None
    assert session.request_call is None

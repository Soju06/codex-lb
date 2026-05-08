from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from fastapi.responses import Response, StreamingResponse
from starlette.requests import Request

import app.modules.proxy.api as proxy_api_module
from app.core.openai.requests import ResponsesRequest
from app.modules.api_keys.service import ApiKeyData
from app.modules.proxy import peer_fallback

pytestmark = pytest.mark.unit


async def _single_chunk(value: str):
    yield value


async def _response_body_text(response: Response) -> str:
    body_iterator = getattr(response, "body_iterator", None)
    if body_iterator is None:
        return bytes(response.body).decode("utf-8")
    chunks: list[str] = []
    async for chunk in body_iterator:
        chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
    return "".join(chunks)


@pytest.mark.asyncio
async def test_stream_responses_peer_fallback_before_first_downstream_event(monkeypatch):
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/backend-api/codex/responses",
            "headers": [],
            "client": ("10.0.0.12", 12345),
        }
    )
    payload = ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": "hi"})
    captured: dict[str, object] = {}
    closed = False
    settled_after_first = False

    def fake_apply_api_key_enforcement(_payload, _api_key):
        return None

    def fake_validate_model_access(_api_key, _model):
        return None

    async def fake_enforce_request_limits(_api_key, *, request_model=None, request_service_tier=None):
        return None

    async def fake_release_reservation(_reservation):
        return None

    async def fake_rate_limit_headers():
        return {}

    async def fake_stream_responses(*args, **kwargs):
        nonlocal closed, settled_after_first
        del args, kwargs
        try:
            yield (
                'data: {"type":"response.failed","response":{"id":"resp_local","status":"failed",'
                '"error":{"code":"no_accounts","message":"No active accounts available",'
                '"type":"server_error"}}}\n\n'
            )
            settled_after_first = True
        finally:
            closed = True

    api_key = cast(ApiKeyData, SimpleNamespace(id="api-key-1", peer_fallback_base_urls=["http://peer.example"]))

    async def fake_peer_stream(_request, peer_payload, *, reason_code, api_key):
        captured["reason_code"] = reason_code
        captured["stream"] = peer_payload["stream"]
        captured["api_key"] = api_key
        return StreamingResponse(
            _single_chunk(
                'data: {"type":"response.completed","response":{"id":"resp_peer","object":"response",'
                '"status":"completed","output":[]}}\n\n'
            ),
            media_type="text/event-stream",
        )

    monkeypatch.setattr(proxy_api_module, "apply_api_key_enforcement", fake_apply_api_key_enforcement)
    monkeypatch.setattr(proxy_api_module, "validate_model_access", fake_validate_model_access)
    monkeypatch.setattr(proxy_api_module, "_enforce_request_limits", fake_enforce_request_limits)
    monkeypatch.setattr(proxy_api_module, "_release_reservation", fake_release_reservation)
    monkeypatch.setattr(proxy_api_module.peer_fallback, "open_stream_response", fake_peer_stream)

    context = cast(
        proxy_api_module.ProxyContext,
        SimpleNamespace(
            service=SimpleNamespace(
                rate_limit_headers=fake_rate_limit_headers,
                stream_responses=fake_stream_responses,
            )
        ),
    )

    response = await proxy_api_module._stream_responses(request, payload, context, api_key)

    body = await _response_body_text(response)
    assert captured == {"reason_code": "no_accounts", "stream": True, "api_key": api_key}
    assert closed
    assert settled_after_first
    assert "resp_peer" in body
    assert "resp_local" not in body


@pytest.mark.asyncio
async def test_stream_responses_peer_fallback_on_upstream_rate_limit(monkeypatch):
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/backend-api/codex/responses",
            "headers": [],
            "client": ("10.0.0.12", 12345),
        }
    )
    payload = ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": "hi"})
    captured: dict[str, object] = {}

    def fake_apply_api_key_enforcement(_payload, _api_key):
        return None

    def fake_validate_model_access(_api_key, _model):
        return None

    async def fake_enforce_request_limits(_api_key, *, request_model=None, request_service_tier=None):
        return None

    async def fake_release_reservation(_reservation):
        return None

    async def fake_rate_limit_headers():
        return {}

    async def fake_stream_responses(*args, **kwargs):
        del args, kwargs
        yield (
            'data: {"type":"response.failed","response":{"id":"resp_local","status":"failed",'
            '"error":{"code":"rate_limit_exceeded","message":"Too Many Requests",'
            '"type":"rate_limit_error"}}}\n\n'
        )

    api_key = cast(ApiKeyData, SimpleNamespace(id="api-key-1", peer_fallback_base_urls=["http://peer.example"]))

    async def fake_peer_stream(_request, peer_payload, *, reason_code, api_key):
        captured["reason_code"] = reason_code
        captured["stream"] = peer_payload["stream"]
        captured["api_key"] = api_key
        return StreamingResponse(
            _single_chunk(
                'data: {"type":"response.completed","response":{"id":"resp_peer","object":"response",'
                '"status":"completed","output":[]}}\n\n'
            ),
            media_type="text/event-stream",
        )

    monkeypatch.setattr(proxy_api_module, "apply_api_key_enforcement", fake_apply_api_key_enforcement)
    monkeypatch.setattr(proxy_api_module, "validate_model_access", fake_validate_model_access)
    monkeypatch.setattr(proxy_api_module, "_enforce_request_limits", fake_enforce_request_limits)
    monkeypatch.setattr(proxy_api_module, "_release_reservation", fake_release_reservation)
    monkeypatch.setattr(proxy_api_module.peer_fallback, "open_stream_response", fake_peer_stream)

    context = cast(
        proxy_api_module.ProxyContext,
        SimpleNamespace(
            service=SimpleNamespace(
                rate_limit_headers=fake_rate_limit_headers,
                stream_responses=fake_stream_responses,
            )
        ),
    )

    response = await proxy_api_module._stream_responses(request, payload, context, api_key)

    body = await _response_body_text(response)
    assert captured == {"reason_code": "rate_limit_exceeded", "stream": True, "api_key": api_key}
    assert "resp_peer" in body
    assert "rate_limit_exceeded" not in body


@pytest.mark.asyncio
async def test_stream_responses_does_not_peer_fallback_after_first_event(monkeypatch):
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/backend-api/codex/responses",
            "headers": [],
            "client": ("10.0.0.12", 12345),
        }
    )
    payload = ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": "hi"})
    peer_stream = AsyncMock()

    def fake_apply_api_key_enforcement(_payload, _api_key):
        return None

    def fake_validate_model_access(_api_key, _model):
        return None

    async def fake_enforce_request_limits(_api_key, *, request_model=None, request_service_tier=None):
        return None

    async def fake_rate_limit_headers():
        return {}

    async def fake_stream_responses(*args, **kwargs):
        del args, kwargs
        yield 'data: {"type":"response.created","response":{"id":"resp_local","status":"in_progress"}}\n\n'
        yield (
            'data: {"type":"response.failed","response":{"id":"resp_local","status":"failed",'
            '"error":{"code":"no_accounts","message":"No active accounts available",'
            '"type":"server_error"}}}\n\n'
        )

    monkeypatch.setattr(proxy_api_module, "apply_api_key_enforcement", fake_apply_api_key_enforcement)
    monkeypatch.setattr(proxy_api_module, "validate_model_access", fake_validate_model_access)
    monkeypatch.setattr(proxy_api_module, "_enforce_request_limits", fake_enforce_request_limits)
    monkeypatch.setattr(proxy_api_module.peer_fallback, "open_stream_response", peer_stream)

    context = cast(
        proxy_api_module.ProxyContext,
        SimpleNamespace(
            service=SimpleNamespace(
                rate_limit_headers=fake_rate_limit_headers,
                stream_responses=fake_stream_responses,
            )
        ),
    )

    response = await proxy_api_module._stream_responses(request, payload, context, None)

    body = await _response_body_text(response)
    peer_stream.assert_not_awaited()
    assert "response.created" in body
    assert "response.failed" in body


@pytest.mark.asyncio
async def test_collect_responses_peer_fallback_uses_non_stream_payload(monkeypatch):
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/responses",
            "headers": [],
            "client": ("10.0.0.12", 12345),
        }
    )
    payload = ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": "hi"})
    captured: dict[str, object] = {}

    def fake_apply_api_key_enforcement(_payload, _api_key):
        return None

    def fake_validate_model_access(_api_key, _model):
        return None

    async def fake_enforce_request_limits(_api_key, *, request_model=None, request_service_tier=None):
        return None

    async def fake_rate_limit_headers():
        return {}

    async def fake_stream_responses(*args, **kwargs):
        del args, kwargs
        yield (
            'data: {"type":"response.failed","response":{"id":"resp_local","status":"failed",'
            '"error":{"code":"no_accounts","message":"No active accounts available",'
            '"type":"server_error"}}}\n\n'
        )

    api_key = cast(ApiKeyData, SimpleNamespace(id="api-key-1", peer_fallback_base_urls=["http://peer.example"]))

    async def fake_peer_buffered(_request, peer_payload, *, reason_code, api_key):
        captured["reason_code"] = reason_code
        captured["stream"] = peer_payload["stream"]
        captured["api_key"] = api_key
        return Response(
            content=b'{"id":"resp_peer","object":"response","status":"completed","output":[]}',
            media_type="application/json",
        )

    monkeypatch.setattr(proxy_api_module, "apply_api_key_enforcement", fake_apply_api_key_enforcement)
    monkeypatch.setattr(proxy_api_module, "validate_model_access", fake_validate_model_access)
    monkeypatch.setattr(proxy_api_module, "_enforce_request_limits", fake_enforce_request_limits)
    monkeypatch.setattr(proxy_api_module.peer_fallback, "open_buffered_response", fake_peer_buffered)

    context = cast(
        proxy_api_module.ProxyContext,
        SimpleNamespace(
            service=SimpleNamespace(
                rate_limit_headers=fake_rate_limit_headers,
                stream_responses=fake_stream_responses,
            )
        ),
    )

    response = await proxy_api_module._collect_responses(request, payload, context, api_key)

    assert captured == {"reason_code": "no_accounts", "stream": False, "api_key": api_key}
    assert response.status_code == 200
    assert b"resp_peer" in response.body


@pytest.mark.asyncio
async def test_collect_responses_preserves_local_error_when_peer_fallback_unavailable(monkeypatch):
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/responses",
            "headers": [],
            "client": ("10.0.0.12", 12345),
        }
    )
    payload = ResponsesRequest.model_validate({"model": "gpt-5.4", "instructions": "hi", "input": "hi"})
    peer_buffered = AsyncMock(return_value=None)

    def fake_apply_api_key_enforcement(_payload, _api_key):
        return None

    def fake_validate_model_access(_api_key, _model):
        return None

    async def fake_enforce_request_limits(_api_key, *, request_model=None, request_service_tier=None):
        return None

    async def fake_rate_limit_headers():
        return {}

    async def fake_stream_responses(*args, **kwargs):
        del args, kwargs
        yield (
            'data: {"type":"response.failed","response":{"id":"resp_local","status":"failed",'
            '"error":{"code":"no_accounts","message":"No active accounts available",'
            '"type":"server_error"}}}\n\n'
        )

    monkeypatch.setattr(proxy_api_module, "apply_api_key_enforcement", fake_apply_api_key_enforcement)
    monkeypatch.setattr(proxy_api_module, "validate_model_access", fake_validate_model_access)
    monkeypatch.setattr(proxy_api_module, "_enforce_request_limits", fake_enforce_request_limits)
    monkeypatch.setattr(proxy_api_module.peer_fallback, "open_buffered_response", peer_buffered)

    context = cast(
        proxy_api_module.ProxyContext,
        SimpleNamespace(
            service=SimpleNamespace(
                rate_limit_headers=fake_rate_limit_headers,
                stream_responses=fake_stream_responses,
            )
        ),
    )

    response = await proxy_api_module._collect_responses(request, payload, context, None)

    peer_buffered.assert_awaited_once()
    await_args = peer_buffered.await_args
    assert await_args is not None
    assert await_args.kwargs["api_key"] is None
    assert response.status_code == 503
    payload_body = json.loads(bytes(response.body))
    assert payload_body["error"]["code"] == "no_accounts"


@pytest.mark.asyncio
async def test_peer_fallback_loop_marker_disables_forwarding(monkeypatch):
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/backend-api/codex/responses",
            "headers": [(peer_fallback.PEER_FALLBACK_DEPTH_HEADER.encode("ascii"), b"1")],
            "client": ("10.0.0.12", 12345),
        }
    )

    settings = SimpleNamespace(
        peer_fallback_base_urls=["http://peer.example"],
        peer_fallback_error_codes=["no_accounts"],
        peer_fallback_max_hops=2,
        peer_fallback_timeout_seconds=1.0,
    )
    monkeypatch.setattr(peer_fallback, "get_settings", lambda: settings)

    response = await peer_fallback.open_stream_response(
        request,
        {"model": "gpt-5.4", "input": "hi", "stream": True},
        reason_code="no_accounts",
    )

    assert response is None

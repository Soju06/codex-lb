from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.core.clients.anthropic_proxy import AnthropicProxyError, anthropic_error_payload
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.db.models import RequestLog
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeysService

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_anthropic_messages_non_stream_success(async_client, monkeypatch):
    async def _stub_create_message(payload, headers, *, base_url=None, session=None):
        return {
            "id": "msg_1",
            "type": "message",
            "model": "claude-sonnet-4-20250514",
            "usage": {"input_tokens": 12, "output_tokens": 5, "cache_read_input_tokens": 2},
            "content": [{"type": "text", "text": "ok"}],
        }

    monkeypatch.setattr("app.modules.anthropic.service.core_create_message", _stub_create_message)

    response = await async_client.post(
        "/claude-sdk/v1/messages",
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 128,
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"x-request-id": "req_anthropic_non_stream"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "msg_1"

    async with SessionLocal() as session:
        result = await session.execute(
            select(RequestLog).where(RequestLog.request_id == "req_anthropic_non_stream")
        )
        log = result.scalar_one()

    assert log.status == "success"
    assert log.model == "claude-sonnet-4-20250514"
    assert log.input_tokens == 12
    assert log.output_tokens == 5
    assert log.cached_input_tokens == 2


@pytest.mark.asyncio
async def test_anthropic_messages_stream_success(async_client, monkeypatch):
    async def _stub_stream_messages(payload, headers, *, base_url=None, session=None):
        yield (
            "event: message_start\n"
            "data: {\"type\":\"message_start\",\"message\":{\"model\":\"claude-sonnet-4-20250514\","
            "\"usage\":{\"input_tokens\":10,\"cache_read_input_tokens\":1}}}\n\n"
        )
        yield "event: message_delta\ndata: {\"type\":\"message_delta\",\"usage\":{\"output_tokens\":7}}\n\n"
        yield "event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n"

    monkeypatch.setattr("app.modules.anthropic.service.core_stream_messages", _stub_stream_messages)

    async with async_client.stream(
        "POST",
        "/claude-sdk/v1/messages",
        json={
            "model": "claude-sonnet-4-20250514",
            "stream": True,
            "max_tokens": 128,
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"x-request-id": "req_anthropic_stream"},
    ) as response:
        assert response.status_code == 200
        lines = [line async for line in response.aiter_lines() if line.startswith("data: ")]

    assert len(lines) == 3
    first_payload = json.loads(lines[0][6:])
    assert first_payload["type"] == "message_start"

    async with SessionLocal() as session:
        result = await session.execute(
            select(RequestLog).where(RequestLog.request_id == "req_anthropic_stream")
        )
        log = result.scalar_one()

    assert log.status == "success"
    assert log.input_tokens == 10
    assert log.output_tokens == 7
    assert log.cached_input_tokens == 1


@pytest.mark.asyncio
async def test_anthropic_messages_non_stream_upstream_error(async_client, monkeypatch):
    async def _stub_create_message(payload, headers, *, base_url=None, session=None):
        raise AnthropicProxyError(
            status_code=400,
            payload=anthropic_error_payload("invalid_request_error", "bad request"),
        )

    monkeypatch.setattr("app.modules.anthropic.service.core_create_message", _stub_create_message)

    response = await async_client.post(
        "/claude-sdk/v1/messages",
        json={
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"x-request-id": "req_anthropic_error"},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["type"] == "error"
    assert payload["error"]["type"] == "invalid_request_error"

    async with SessionLocal() as session:
        result = await session.execute(
            select(RequestLog).where(RequestLog.request_id == "req_anthropic_error")
        )
        log = result.scalar_one()

    assert log.status == "error"
    assert log.error_code == "invalid_request_error"


@pytest.mark.asyncio
async def test_anthropic_api_messages_non_stream_success(async_client, monkeypatch):
    seen = {}

    def _stub_stream_responses(
        self,
        payload,
        headers,
        *,
        propagate_http_errors=False,
        api_key=None,
        api_key_reservation=None,
        suppress_text_done_events=False,
    ):
        seen["model"] = payload.model
        seen["stream"] = payload.stream

        async def _events():
            yield 'data: {"type":"response.output_text.delta","delta":"ok api"}\n\n'
            yield (
                'data: {"type":"response.completed","response":{"id":"resp_api_1","usage":'
                '{"input_tokens":8,"output_tokens":3,"total_tokens":11}}}\n\n'
            )

        return _events()

    monkeypatch.setattr("app.modules.proxy.service.ProxyService.stream_responses", _stub_stream_responses)

    response = await async_client.post(
        "/claude/v1/messages",
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 128,
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"x-request-id": "req_anthropic_api_non_stream"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["type"] == "message"
    assert payload["model"] == "claude-sonnet-4-20250514"
    assert payload["content"] == [{"type": "text", "text": "ok api"}]
    assert payload["usage"]["input_tokens"] == 8
    assert payload["usage"]["output_tokens"] == 3
    assert seen["model"] == "gpt-5.3-codex"
    assert seen["stream"] is True


@pytest.mark.asyncio
async def test_anthropic_api_messages_stream_success(async_client, monkeypatch):
    def _stub_stream_responses(
        self,
        payload,
        headers,
        *,
        propagate_http_errors=False,
        api_key=None,
        api_key_reservation=None,
        suppress_text_done_events=False,
    ):
        async def _events():
            yield 'data: {"type":"response.output_text.delta","delta":"streamed"}\n\n'
            yield (
                'data: {"type":"response.completed","response":{"id":"resp_api_2","usage":'
                '{"input_tokens":3,"output_tokens":2,"total_tokens":5}}}\n\n'
            )

        return _events()

    monkeypatch.setattr("app.modules.proxy.service.ProxyService.stream_responses", _stub_stream_responses)

    async with async_client.stream(
        "POST",
        "/claude/v1/messages",
        json={
            "model": "claude-sonnet-4-20250514",
            "stream": True,
            "max_tokens": 128,
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"x-request-id": "req_anthropic_api_stream"},
    ) as response:
        assert response.status_code == 200
        lines = [line async for line in response.aiter_lines() if line.startswith("data: ")]

    assert len(lines) >= 4
    payloads = [json.loads(line[6:]) for line in lines]
    assert payloads[0]["type"] == "message_start"
    assert payloads[-1]["type"] == "message_stop"


@pytest.mark.asyncio
async def test_anthropic_api_messages_non_stream_proxy_error(async_client, monkeypatch):
    def _stub_stream_responses(
        self,
        payload,
        headers,
        *,
        propagate_http_errors=False,
        api_key=None,
        api_key_reservation=None,
        suppress_text_done_events=False,
    ):
        raise ProxyResponseError(
            429,
            openai_error("rate_limit_exceeded", "too many requests", "rate_limit_error"),
        )

    monkeypatch.setattr("app.modules.proxy.service.ProxyService.stream_responses", _stub_stream_responses)

    response = await async_client.post(
        "/claude/v1/messages",
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 128,
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 429
    payload = response.json()
    assert payload["type"] == "error"
    assert payload["error"]["type"] == "rate_limit_error"
    assert "too many requests" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_anthropic_api_messages_accepts_x_api_key_header(async_client, monkeypatch):
    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200

    async with SessionLocal() as session:
        service = ApiKeysService(ApiKeysRepository(session))
        created = await service.create_key(
            ApiKeyCreateData(
                name="anthropic-x-api-key",
                allowed_models=None,
                expires_at=None,
            )
        )

    def _stub_stream_responses(
        self,
        payload,
        headers,
        *,
        propagate_http_errors=False,
        api_key=None,
        api_key_reservation=None,
        suppress_text_done_events=False,
    ):
        async def _events():
            yield 'data: {"type":"response.output_text.delta","delta":"ok"}\n\n'
            yield (
                'data: {"type":"response.completed","response":{"id":"resp_api_key_1","usage":'
                '{"input_tokens":2,"output_tokens":1,"total_tokens":3}}}\n\n'
            )

        return _events()

    monkeypatch.setattr("app.modules.proxy.service.ProxyService.stream_responses", _stub_stream_responses)

    response = await async_client.post(
        "/claude/v1/messages",
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 128,
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"x-api-key": created.key},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["type"] == "message"


@pytest.mark.asyncio
async def test_claude_desktop_bootstrap_returns_account(async_client):
    response = await async_client.get("/api/bootstrap")

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload.get("account"), dict)
    assert isinstance(payload["account"].get("uuid"), str)
    assert isinstance(payload["account"].get("email"), str)
    assert isinstance(payload.get("organization"), dict)


@pytest.mark.asyncio
async def test_claude_desktop_features_returns_empty_feature_map(async_client):
    response = await async_client.get("/api/desktop/features")

    assert response.status_code == 200
    payload = response.json()
    assert payload == {"features": {}}


@pytest.mark.asyncio
async def test_claude_desktop_event_logging_batch_acknowledges(async_client):
    response = await async_client.post(
        "/api/event_logging/batch",
        json={"events": [{"event_name": "desktop_started"}]},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}

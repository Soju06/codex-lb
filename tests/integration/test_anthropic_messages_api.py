from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.core.clients.anthropic_proxy import AnthropicProxyError, anthropic_error_payload
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, RequestLog
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository

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
        "/claude/v1/messages",
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
        "/claude/v1/messages",
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
        "/claude/v1/messages",
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
    async def _stub_create_message_api(payload, headers, *, base_url=None, session=None, credentials=None):
        return {
            "id": "msg_api_1",
            "type": "message",
            "model": "claude-sonnet-4-20250514",
            "usage": {"input_tokens": 8, "output_tokens": 3, "cache_read_input_tokens": 0},
            "content": [{"type": "text", "text": "ok api"}],
        }

    monkeypatch.setattr("app.modules.anthropic.service.core_create_message_api", _stub_create_message_api)

    response = await async_client.post(
        "/claude-api/v1/messages",
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 128,
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"x-request-id": "req_anthropic_api_non_stream"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "msg_api_1"

    async with SessionLocal() as session:
        result = await session.execute(
            select(RequestLog).where(RequestLog.request_id == "req_anthropic_api_non_stream")
        )
        log = result.scalar_one()

    assert log.status == "success"
    assert log.model == "claude-sonnet-4-20250514"
    assert log.input_tokens == 8
    assert log.output_tokens == 3


@pytest.mark.asyncio
async def test_anthropic_api_messages_stream_success(async_client, monkeypatch):
    async def _stub_stream_messages_api(payload, headers, *, base_url=None, session=None, credentials=None):
        yield (
            "event: message_start\n"
            "data: {\"type\":\"message_start\",\"message\":{\"model\":\"claude-sonnet-4-20250514\","
            "\"usage\":{\"input_tokens\":3,\"cache_read_input_tokens\":0}}}\n\n"
        )
        yield "event: message_delta\ndata: {\"type\":\"message_delta\",\"usage\":{\"output_tokens\":2}}\n\n"
        yield "event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n"

    monkeypatch.setattr("app.modules.anthropic.service.core_stream_messages_api", _stub_stream_messages_api)

    async with async_client.stream(
        "POST",
        "/claude-api/v1/messages",
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

    assert len(lines) == 3
    first_payload = json.loads(lines[0][6:])
    assert first_payload["type"] == "message_start"

    async with SessionLocal() as session:
        result = await session.execute(
            select(RequestLog).where(RequestLog.request_id == "req_anthropic_api_stream")
        )
        log = result.scalar_one()

    assert log.status == "success"
    assert log.input_tokens == 3
    assert log.output_tokens == 2


@pytest.mark.asyncio
async def test_anthropic_api_uses_imported_db_credentials(async_client, monkeypatch):
    settings = get_settings()
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account_id = settings.anthropic_default_account_id
        existing = await repo.get_by_id(account_id)
        if existing is None:
            account = Account(
                id=account_id,
                chatgpt_account_id=None,
                email="claude@example.com",
                plan_type="pro",
                access_token_encrypted=encryptor.encrypt("sk-ant-oat-db-access"),
                refresh_token_encrypted=encryptor.encrypt("refresh-db"),
                id_token_encrypted=encryptor.encrypt(""),
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
                deactivation_reason=None,
            )
            await repo.upsert(account, merge_by_email=False)
        else:
            await repo.update_tokens(
                account_id=account_id,
                access_token_encrypted=encryptor.encrypt("sk-ant-oat-db-access"),
                refresh_token_encrypted=encryptor.encrypt("refresh-db"),
                id_token_encrypted=encryptor.encrypt(""),
                last_refresh=utcnow(),
                email="claude@example.com",
                plan_type="pro",
            )

    async def _stub_create_message_api(payload, headers, *, base_url=None, session=None, credentials=None):
        assert credentials is not None
        assert credentials.bearer_token == "sk-ant-oat-db-access"
        return {
            "id": "msg_api_db_creds",
            "type": "message",
            "model": "claude-sonnet-4-20250514",
            "usage": {"input_tokens": 5, "output_tokens": 1, "cache_read_input_tokens": 0},
            "content": [{"type": "text", "text": "ok api db"}],
        }

    monkeypatch.setattr("app.modules.anthropic.service.core_create_message_api", _stub_create_message_api)

    response = await async_client.post(
        "/claude-api/v1/messages",
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 128,
            "messages": [{"role": "user", "content": "hello"}],
        },
        headers={"x-request-id": "req_anthropic_api_db_credentials"},
    )

    assert response.status_code == 200
    assert response.json()["id"] == "msg_api_db_creds"

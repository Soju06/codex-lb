from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy import select

import app.modules.proxy.service as proxy_module
from app.core.clients.proxy import CodexControlRequestPrivacyPolicy, CodexControlResponse, ProxyResponseError
from app.core.config.settings_cache import get_settings_cache
from app.db.models import Account, AccountStatus, RequestLog
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from app.modules.settings.repository import SettingsRepository
from tests.integration.test_proxy_api_extended import _import_account

pytestmark = pytest.mark.integration

CONTEXT_PATHS = [
    "alpha/history/v2/list_windows",
    "alpha/history/v2/list_items",
    "alpha/history/v2/read_item",
    "alpha/history/v2/search_contents",
    "alpha/notes/v2/thread_hint",
    "alpha/notes/v2/list_files_by_prefix",
    "alpha/notes/v2/read_file",
    "alpha/notes/v2/search_contents",
    "alpha/notes/v2/append_to_file",
    "alpha/notes/v2/write_file",
]


async def _context_key(client: AsyncClient, account_ids: list[str]) -> dict[str, str]:
    response = await client.post("/api/api-keys/", json={"name": "context-test", "assignedAccountIds": account_ids})
    assert response.status_code == 200
    async with SessionLocal() as session:
        settings = await SettingsRepository(session).get_or_create()
        settings.api_key_auth_enabled = True
        await session.commit()
    await get_settings_cache().invalidate()
    return {"authorization": "Bearer " + response.json()["key"]}


@pytest.mark.parametrize("path", CONTEXT_PATHS)
async def test_context_route_forwards_opaque_payload_with_scoped_credentials(async_client, monkeypatch, path):
    owner = await _import_account(async_client, "context-owner", "owner@example.com")
    await _import_account(async_client, "context-other", "other@example.com")
    headers = await _context_key(async_client, [owner])
    payload = (
        b'{ "context": {"session_id":"00000000-0000-4000-8000-000000000001",'
        b'"current_agent_name":"/root"}, "opaque":"ciphertext" }'
    )
    upstream_body = b'{ "files": [], "future_field": true }'
    upstream = AsyncMock(
        return_value=CodexControlResponse(
            status_code=200,
            body=upstream_body,
            headers={"content-type": "application/json", "x-request-id": "ctx-request", "set-cookie": "private=1"},
        )
    )
    monkeypatch.setattr(proxy_module, "core_codex_control_request", upstream)

    response = await async_client.post(
        "/backend-api/codex/" + path + "?limit=1&limit=2",
        content=payload,
        headers={
            **headers,
            "content-type": "application/json",
            "x-openai-encrypted-tool-arguments": "true",
            "x-openai-tool-output-truncation-policy": '{"Bytes":4000}',
        },
    )

    assert response.status_code == 200
    if path == "alpha/notes/v2/thread_hint":
        assert response.content == upstream_body
    else:
        from app.core.crypto import TokenEncryptor
        from app.modules.proxy.context_codec import PREFIX

        token = response.json()["encrypted_output"]
        envelope = json.loads(TokenEncryptor().decrypt(token[len(PREFIX) :].encode()))
        assert envelope["partitions"][0]["result"] == json.loads(upstream_body)
    assert response.headers["x-request-id"] == "ctx-request"
    assert "set-cookie" not in response.headers
    upstream.assert_awaited_once()
    args, kwargs = upstream.call_args
    assert args == (path,)
    assert kwargs["method"] == "POST"
    assert kwargs["payload"] == payload
    assert kwargs["query_params"] == [("limit", "1"), ("limit", "2")]
    assert kwargs["account_id"] == "context-owner"
    assert kwargs["access_token"] == "access-token"
    assert "authorization" not in kwargs["headers"]
    assert kwargs["headers"]["x-openai-encrypted-tool-arguments"] == "true"
    assert kwargs["headers"]["x-openai-tool-output-truncation-policy"] == '{"Bytes":4000}'
    assert kwargs["privacy_policy"] is CodexControlRequestPrivacyPolicy.PRIVATE_CONTEXT


async def test_context_rejects_missing_identity(async_client, monkeypatch):
    headers = await _context_key(async_client, [])
    upstream = AsyncMock()
    monkeypatch.setattr(proxy_module, "core_codex_control_request", upstream)
    response = await async_client.post("/backend-api/codex/alpha/notes/v2/write_file", json={}, headers=headers)
    assert response.status_code == 400
    upstream.assert_not_awaited()


@pytest.mark.parametrize("path", CONTEXT_PATHS)
@pytest.mark.parametrize("authorization", [None, "Bearer invalid"])
async def test_context_always_requires_valid_proxy_auth(async_client, monkeypatch, path, authorization):
    upstream = AsyncMock()
    monkeypatch.setattr(proxy_module, "core_codex_control_request", upstream)
    response = await async_client.post(
        "/backend-api/codex/" + path,
        json={},
        headers=({} if authorization is None else {"authorization": authorization}),
    )
    assert response.status_code == 401
    upstream.assert_not_awaited()


async def test_context_trailing_slash_preserves_post(async_client, monkeypatch):
    owner = await _import_account(async_client, "context-owner", "owner@example.com")
    headers = await _context_key(async_client, [owner])
    upstream = AsyncMock(return_value=CodexControlResponse(status_code=200, body=b"{}", headers={}))
    monkeypatch.setattr(proxy_module, "core_codex_control_request", upstream)
    response = await async_client.post(
        "/backend-api/codex/alpha/notes/v2/read_file/",
        json={
            "path": "a",
            "context": {"session_id": "00000000-0000-4000-8000-000000000001", "current_agent_name": "/root"},
        },
        headers=headers,
        follow_redirects=True,
    )
    assert not response.history
    assert response.status_code == 200
    assert upstream.call_args.kwargs["method"] == "POST"
    assert json.loads(upstream.call_args.kwargs["payload"])["path"] == "a"


async def test_context_unavailable_owner_does_not_use_another_account(async_client, monkeypatch):
    owner = await _import_account(async_client, "context-owner", "owner@example.com")
    await _import_account(async_client, "context-other", "other@example.com")
    headers = await _context_key(async_client, [owner])
    async with SessionLocal() as session:
        account = await session.get(Account, owner)
        assert account is not None
        account.status = AccountStatus.PAUSED
        await session.commit()
    upstream = AsyncMock()
    monkeypatch.setattr(proxy_module, "core_codex_control_request", upstream)
    response = await async_client.post(
        "/backend-api/codex/alpha/history/v2/list_windows",
        json={"context": {"session_id": "00000000-0000-4000-8000-000000000001", "current_agent_name": "/root"}},
        headers=headers,
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "context_backend_unavailable"
    upstream.assert_not_awaited()


@pytest.mark.parametrize("status_code", [400, 403, 429, 500])
async def test_context_failure_stays_scoped_and_redacts_private_errors(
    async_client,
    monkeypatch,
    caplog,
    status_code,
):
    owner = await _import_account(async_client, "context-owner", "owner@example.com")
    await _import_account(async_client, "context-other", "other@example.com")
    headers = await _context_key(async_client, [owner])
    secret = "private-note-sentinel-never-log"
    upstream = AsyncMock(
        side_effect=ProxyResponseError(
            status_code,
            {
                "error": {
                    "code": "context_test_failure",
                    "message": secret,
                    "type": "invalid_request_error",
                }
            },
        )
    )
    monkeypatch.setattr(proxy_module, "core_codex_control_request", upstream)
    caplog.set_level(logging.DEBUG)
    response = await async_client.post(
        "/backend-api/codex/alpha/notes/v2/append_to_file",
        json={
            "content": secret,
            "context": {"session_id": "00000000-0000-4000-8000-000000000001", "current_agent_name": "/root"},
        },
        headers=headers,
    )
    assert response.status_code == status_code
    assert response.json()["error"]["code"] == "context_backend_unavailable"
    assert all(call.kwargs["account_id"] == "context-owner" for call in upstream.call_args_list)
    assert secret not in response.text
    assert secret not in caplog.text
    service = get_proxy_service_for_app(async_client._transport.app)
    assert await service.drain_persistence_tasks(timeout_seconds=1)
    async with SessionLocal() as session:
        logs = list((await session.execute(select(RequestLog))).scalars())
    assert logs
    assert all(log.error_message is None and log.error_code is None for log in logs)


async def test_context_unexpected_failure_is_generic(async_client, monkeypatch):
    owner = await _import_account(async_client, "context-owner", "owner@example.com")
    headers = await _context_key(async_client, [owner])
    monkeypatch.setattr(
        proxy_module.ProxyService, "codex_context_request", AsyncMock(side_effect=RuntimeError("private"))
    )
    response = await async_client.post(
        "/backend-api/codex/alpha/notes/v2/thread_hint",
        json={"context": {"session_id": "00000000-0000-4000-8000-000000000001", "current_agent_name": "/root"}},
        headers=headers,
    )
    assert response.status_code == 503
    assert "private" not in response.text


async def test_unknown_context_operation_is_not_relayed(async_client, monkeypatch):
    upstream = AsyncMock()
    monkeypatch.setattr(proxy_module, "core_codex_control_request", upstream)
    response = await async_client.post("/backend-api/codex/alpha/notes/v2/delete_everything", json={})
    assert response.status_code in {404, 405}
    upstream.assert_not_awaited()

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

import app.modules.proxy.service as proxy_module
from app.core.clients.proxy import CodexControlResponse, ProxyResponseError
from app.core.config.settings_cache import get_settings_cache
from app.core.openai.requests import ResponsesRequest
from app.db.models import Account, AccountStatus, CodexContextParticipant, CodexContextSession
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeysService
from app.modules.proxy.context_codec import expand_history_input
from app.modules.proxy.context_dispatch import record_context_dispatch
from app.modules.proxy.request_policy import apply_api_key_enforcement
from app.modules.settings.repository import SettingsRepository
from tests.integration.test_codex_history_notes import _context_key
from tests.integration.test_proxy_api_extended import _import_account

pytestmark = pytest.mark.integration
SID = "00000000-0000-4000-8000-000000000011"
CONTEXT = {"session_id": SID, "current_agent_name": "/root"}


async def setup_pool(client):
    a = await _import_account(client, "context-a", "a@example.com")
    b = await _import_account(client, "context-b", "b@example.com")
    headers = await _context_key(client, [])
    async with SessionLocal() as session:
        settings = await SettingsRepository(session).get_or_create()
        settings.api_key_auth_enabled = True
        await session.commit()
        key = await ApiKeysService(ApiKeysRepository(session)).validate_key(headers["authorization"][7:])
    await get_settings_cache().invalidate()
    return a, b, headers, key


def envelope():
    return {
        "model": "gpt-5.1",
        "instructions": "Test",
        "input": "Hello",
        "stream": True,
        "reasoning": {"context": "all_turns"},
        "client_metadata": {"session_id": SID},
    }


async def test_pool_notes_keep_owner_and_quota_status_after_rotation(async_client, monkeypatch):
    a, b, headers, key = await setup_pool(async_client)
    await record_context_dispatch(envelope(), key, a)
    await record_context_dispatch(envelope(), key, b)
    async with SessionLocal() as session:
        row = await session.get(Account, a)
        assert row is not None
        row.status = AccountStatus.RATE_LIMITED
        await session.commit()
    upstream = AsyncMock(return_value=CodexControlResponse(status_code=200, body=b'{"ok":true}', headers={}))
    monkeypatch.setattr(proxy_module, "core_codex_control_request", upstream)
    for action in ("write_file", "read_file"):
        r = await async_client.post(
            "/backend-api/codex/alpha/notes/v2/" + action,
            json={"context": CONTEXT, "path": "test", "text": "opaque"},
            headers=headers,
        )
        assert r.status_code == 200
    assert [call.kwargs["account_id"] for call in upstream.call_args_list] == ["context-a", "context-a"]
    async with SessionLocal() as session:
        account = await session.get(Account, a)
        binding = await session.get(CodexContextSession, SID)
        assert account is not None and account.status == AccountStatus.RATE_LIMITED
        assert binding is not None and binding.owner_account_id == a


async def test_history_fans_out_and_expands_native_ciphertexts_for_child_agent(async_client, monkeypatch):
    a, b, headers, key = await setup_pool(async_client)
    await record_context_dispatch(envelope(), key, a)
    await record_context_dispatch(envelope(), key, b)

    async def upstream(path, **kwargs):
        assert json.loads(kwargs["payload"])["context"]["current_agent_name"] == "/root/child"
        return CodexControlResponse(
            status_code=200, body=json.dumps({"encrypted_output": kwargs["account_id"]}).encode(), headers={}
        )

    monkeypatch.setattr(proxy_module, "core_codex_control_request", upstream)
    r = await async_client.post(
        "/backend-api/codex/alpha/history/v2/search_contents",
        headers=headers,
        json={"context": {**CONTEXT, "current_agent_name": "/root/child"}, "query": "ciphertext"},
    )
    assert r.status_code == 200
    body = envelope()
    body["input"] = [
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": [{"type": "encrypted_content", "encrypted_content": r.json()["encrypted_output"]}],
        }
    ]
    payload = ResponsesRequest.model_validate(body)
    apply_api_key_enforcement(payload, key)
    parts = cast(list[dict[str, Any]], payload.input)[0]["output"]
    assert [p["encrypted_content"] for p in parts if p["type"] == "encrypted_content"] == ["context-a", "context-b"]
    assert "codex-lb-context-v1:" not in json.dumps(payload.to_payload())


async def test_other_key_and_removed_account_scope_cannot_read_bound_context(async_client, monkeypatch):
    a, b, headers, key = await setup_pool(async_client)
    await record_context_dispatch(envelope(), key, a)
    other = await _context_key(async_client, [])
    upstream = AsyncMock()
    monkeypatch.setattr(proxy_module, "core_codex_control_request", upstream)
    r = await async_client.post("/backend-api/codex/alpha/notes/v2/read_file", json={"context": CONTEXT}, headers=other)
    assert r.status_code == 403
    upstream.assert_not_awaited()
    from app.modules.api_keys.service import ApiKeyUpdateData

    async with SessionLocal() as session:
        await ApiKeysService(ApiKeysRepository(session)).update_key(
            key.id, ApiKeyUpdateData(assigned_account_ids=[b], assigned_account_ids_set=True)
        )
    r = await async_client.post(
        "/backend-api/codex/alpha/notes/v2/read_file", json={"context": CONTEXT}, headers=headers
    )
    assert r.status_code == 403
    upstream.assert_not_awaited()


async def test_concurrent_dispatches_keep_one_durable_owner(async_client):
    a, b, _, key = await setup_pool(async_client)
    await asyncio.gather(*(record_context_dispatch(envelope(), key, account) for account in [a, b, a, b]))
    async with SessionLocal() as session:
        row = await session.get(CodexContextSession, SID)
        assert row is not None and row.owner_account_id in [a, b]
        owners = list(
            await session.scalars(
                select(CodexContextParticipant.account_id).where(CodexContextParticipant.session_id == SID)
            )
        )
        assert set(owners) == {a, b}


async def test_partial_history_failure_cancels_sibling_and_returns_no_partial_result(async_client, monkeypatch):
    a, b, headers, key = await setup_pool(async_client)
    await record_context_dispatch(envelope(), key, a)
    await record_context_dispatch(envelope(), key, b)
    sibling_started = asyncio.Event()
    sibling_finished = asyncio.Event()

    async def upstream(path, **kwargs):
        if kwargs["account_id"] == "context-a":
            await sibling_started.wait()
            raise RuntimeError("private-history-error")
        sibling_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            sibling_finished.set()

    monkeypatch.setattr(proxy_module, "core_codex_control_request", upstream)
    r = await async_client.post(
        "/backend-api/codex/alpha/history/v2/list_items", json={"context": CONTEXT}, headers=headers
    )
    assert r.status_code == 503
    assert sibling_finished.is_set()
    assert "private-history-error" not in r.text


@pytest.mark.parametrize("failure", [TimeoutError(), ProxyResponseError(500, {"error": {"message": "private"}})])
async def test_ambiguous_note_write_is_not_retried(async_client, monkeypatch, failure):
    a, _, headers, key = await setup_pool(async_client)
    await record_context_dispatch(envelope(), key, a)
    upstream = AsyncMock(side_effect=failure)
    monkeypatch.setattr(proxy_module, "core_codex_control_request", upstream)
    r = await async_client.post(
        "/backend-api/codex/alpha/notes/v2/append_to_file", json={"context": CONTEXT}, headers=headers
    )
    assert r.status_code >= 500
    upstream.assert_awaited_once()


async def test_http_responses_dispatch_is_recorded(async_client, monkeypatch):
    a, b, headers, key = await setup_pool(async_client)

    seen = []

    async def stream(*args, **kwargs):
        seen.append(args[0].to_payload())
        yield 'data: {"type":"response.completed","response":{"id":"resp_ctx","status":"completed","output":[]}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", stream)
    r = await async_client.post("/backend-api/codex/responses", headers=headers, json=envelope())
    assert r.status_code == 200
    assert "response.completed" in r.text
    async with SessionLocal() as session:
        row = await session.get(CodexContextSession, SID)
        assert row is not None and row.api_key_id == key.id and row.owner_account_id in [a, b], [
            x.get("client_metadata") for x in seen
        ]


async def test_history_envelope_rejects_tampering_and_cross_scope(async_client):
    from app.modules.proxy.context_codec import HistoryPartition, pack_history

    _, b, _, key = await setup_pool(async_client)
    token = json.loads(
        pack_history(key.id, SID, [HistoryPartition(account_id=b, result={"encrypted_output": "opaque"})])
    )["encrypted_output"]

    def body(value):
        return [{"type": "function_call_output", "output": [{"type": "encrypted_content", "encrypted_content": value}]}]

    for candidate_key, metadata, value in [
        (replace(key, id="other"), CONTEXT, token),
        (key, {"session_id": "other"}, token),
        (replace(key, account_assignment_scope_enabled=True, assigned_account_ids=[]), CONTEXT, token),
        (key, CONTEXT, token[:-8] + "tampered"),
    ]:
        with pytest.raises(ProxyResponseError):
            expand_history_input(body(value), metadata, candidate_key)


async def test_verified_context_ciphertext_is_portable_but_unknown_reasoning_is_not(async_client):
    from app.modules.proxy.context_codec import HistoryPartition, pack_history
    from app.modules.proxy.replay_safety import responses_payload_is_account_neutral_fresh_replay

    a, _, _, key = await setup_pool(async_client)
    token = json.loads(
        pack_history(
            key.id, SID, [HistoryPartition(account_id=a, result={"encrypted_output": "context-cipher"})], kind="notes"
        )
    )["encrypted_output"]
    body = envelope()
    body["tools"] = [{"type": "function", "name": "read_file", "parameters": {"type": "object"}}]
    body["input"] = [
        {"role": "user", "content": [{"type": "input_text", "text": "Recover"}]},
        {
            "type": "function_call",
            "id": "fc_" + "a" * 50,
            "namespace": "notes",
            "call_id": "call_1",
            "name": "read_file",
            "arguments": "{}",
            "internal_chat_message_metadata_passthrough": {"create_time": 1, "turn_id": "turn"},
        },
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": [{"type": "encrypted_content", "encrypted_content": token}],
        },
    ]
    body["input"][-1]["id"] = "fco_00000000-0000-4000-8000-000000000001"
    body["input"][-1]["internal_chat_message_metadata_passthrough"] = {"create_time": 1, "turn_id": "turn"}
    payload = ResponsesRequest.model_validate(body)
    apply_api_key_enforcement(payload, key)
    wire = cast(dict[str, Any], payload.to_payload())
    assert responses_payload_is_account_neutral_fresh_replay(payload.to_replay_safety_payload())
    assert payload.to_payload() == wire
    assert wire["input"][-1]["output"][0]["encrypted_content"] == "context-cipher"
    assert isinstance(payload.input, list)
    payload.input.append({"type": "reasoning", "encrypted_content": "owner-state"})
    assert not responses_payload_is_account_neutral_fresh_replay(payload.to_replay_safety_payload())


async def test_bad_context_envelope_returns_http_error_without_dispatch(async_client, monkeypatch):
    _, _, headers, _ = await setup_pool(async_client)
    body = envelope()
    body["input"] = [
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": [{"type": "encrypted_content", "encrypted_content": "codex-lb-context-v1:tampered"}],
        }
    ]
    upstream = AsyncMock()
    monkeypatch.setattr(proxy_module, "core_stream_responses", upstream)
    r = await async_client.post("/backend-api/codex/responses", headers=headers, json=body)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "context_result_invalid"
    upstream.assert_not_called()


@pytest.mark.parametrize("all_turns", [True, False])
async def test_other_key_cannot_dispatch_into_bound_session_or_penalize_account(async_client, monkeypatch, all_turns):
    from app.modules.proxy.load_balancer import LoadBalancer

    a, _, _, key = await setup_pool(async_client)
    await record_context_dispatch(envelope(), key, a)
    other = await _context_key(async_client, [])
    upstream = AsyncMock()
    health = AsyncMock()
    monkeypatch.setattr(proxy_module, "core_stream_responses", upstream)
    monkeypatch.setattr(LoadBalancer, "record_errors", health)
    body = envelope()
    if not all_turns:
        body.pop("reasoning")
    r = await async_client.post("/backend-api/codex/responses", headers=other, json=body)
    assert r.status_code == 403
    upstream.assert_not_called()
    health.assert_not_awaited()
    async with SessionLocal() as session:
        binding = await session.get(CodexContextSession, SID)
        assert binding is not None and binding.api_key_id == key.id


async def test_context_requires_authenticated_responses(async_client, monkeypatch):
    _, _, headers, _ = await setup_pool(async_client)
    async with SessionLocal() as session:
        settings = await SettingsRepository(session).get_or_create()
        settings.api_key_auth_enabled = False
        await session.commit()
    await get_settings_cache().invalidate()
    upstream = AsyncMock()
    monkeypatch.setattr(proxy_module, "core_codex_control_request", upstream)
    r = await async_client.post(
        "/backend-api/codex/alpha/notes/v2/read_file", headers=headers, json={"context": CONTEXT}
    )
    assert r.status_code == 409
    upstream.assert_not_awaited()


@pytest.mark.parametrize("state", [AccountStatus.PAUSED, AccountStatus.DEACTIVATED, "deleted"])
async def test_unavailable_bound_owner_keeps_tombstone_and_never_moves_notes(async_client, monkeypatch, state):
    a, _, headers, key = await setup_pool(async_client)
    await record_context_dispatch(envelope(), key, a)
    async with SessionLocal() as session:
        account = await session.get(Account, a)
        assert account is not None
        if state == "deleted":
            await session.delete(account)
        else:
            account.status = state
        await session.commit()
    upstream = AsyncMock()
    monkeypatch.setattr(proxy_module, "core_codex_control_request", upstream)
    r = await async_client.post(
        "/backend-api/codex/alpha/notes/v2/write_file", headers=headers, json={"context": CONTEXT}
    )
    assert r.status_code == 503
    upstream.assert_not_awaited()
    async with SessionLocal() as session:
        binding = await session.get(CodexContextSession, SID)
        assert binding is not None and binding.owner_account_id == a


@pytest.mark.parametrize("transport", ["websocket", "http_bridge"])
@pytest.mark.parametrize("quota_rejection", [False, True])
def test_context_dispatch_and_ciphertext_expansion_on_websocket_transports(
    app_instance, monkeypatch, transport, quota_rejection
):
    from fastapi.testclient import TestClient
    from httpx import ASGITransport, AsyncClient

    from app.dependencies import get_proxy_service_for_app
    from app.modules.proxy.context_codec import HistoryPartition, pack_history
    from tests.integration.test_http_responses_bridge import (
        _FakeBridgeUpstreamWebSocket,
        _FakeUpstreamMessage,
        _install_bridge_settings,
    )

    async def prepare():
        async with AsyncClient(transport=ASGITransport(app=app_instance), base_url="http://testserver") as client:
            return await setup_pool(client)

    class Upstream(_FakeBridgeUpstreamWebSocket):
        reject = False

        async def send_text(self, text):
            async with SessionLocal() as session:
                row = await session.get(CodexContextSession, SID)
                assert row is not None and row.api_key_id == key.id
                assert await session.scalar(
                    select(CodexContextParticipant.account_id).where(CodexContextParticipant.session_id == SID)
                )
            sent = json.loads(text)
            assert sent["input"][-1]["output"][0]["encrypted_content"] == "native-cipher"
            assert "codex-lb-context-v1:" not in text
            if self.reject:
                self.reject = False
                self.sent_text.append(text)
                await self._messages.put(
                    _FakeUpstreamMessage(
                        "text",
                        text=json.dumps(
                            {
                                "type": "error",
                                "status": 429,
                                "error": {
                                    "type": "usage_limit_reached",
                                    "code": "usage_limit_reached",
                                    "message": "Test quota",
                                },
                            }
                        ),
                    )
                )
                return
            await super().send_text(text)

    with TestClient(app_instance) as client:
        assert client.portal is not None
        a, _, headers, key = client.portal.call(prepare)
        _install_bridge_settings(monkeypatch, enabled=transport == "http_bridge")
        upstream = Upstream()
        upstream.reject = quota_rejection
        second = Upstream()
        connect = AsyncMock(side_effect=[upstream, second])
        monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
        token = json.loads(
            pack_history(key.id, SID, [HistoryPartition(account_id=a, result={"encrypted_output": "native-cipher"})])
        )["encrypted_output"]
        body = envelope()
        body["input"] = [
            {"role": "user", "content": "Read the note"},
            {"type": "function_call", "name": "read_file", "call_id": "call_1", "arguments": "{}"},
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": [{"type": "encrypted_content", "encrypted_content": token}],
            },
        ]
        if transport == "websocket":
            with client.websocket_connect("/backend-api/codex/responses", headers=headers) as websocket:
                websocket.send_json({**body, "type": "response.create"})
                events = [websocket.receive_json(), websocket.receive_json()]
                assert events[-1]["type"] == "response.completed"
        else:
            r = client.post("/backend-api/codex/responses", headers=headers, json=body)
            assert r.status_code == 200 and "response.completed" in r.text
            service = get_proxy_service_for_app(app_instance)
            for session in list(service._http_bridge_sessions.values()):
                client.portal.call(service._close_http_bridge_session, session)
        assert len(upstream.sent_text) == 1
        if quota_rejection:
            assert len(second.sent_text) == 1
            assert connect.call_args_list[0].args[2] != connect.call_args_list[1].args[2]

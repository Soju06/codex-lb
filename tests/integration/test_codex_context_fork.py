from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

import app.modules.proxy.service as proxy_module
from app.core.clients.proxy import CodexControlResponse
from app.db.models import Account, CodexContextParticipant, CodexContextSession
from app.db.session import SessionLocal
from app.modules.proxy.context_codec import HistoryPartition, pack_history
from app.modules.proxy.context_dispatch import get_context_dispatch_cache, record_context_dispatch
from tests.integration.test_codex_context_pool import SID, envelope, setup_pool
from tests.integration.test_codex_history_notes import _context_key

pytestmark = pytest.mark.integration
FORK_SID = "00000000-0000-4000-8000-000000000012"


def fork_request(key_id, account_id):
    token = json.loads(
        pack_history(
            key_id,
            SID,
            [HistoryPartition(account_id=account_id, result={"encrypted_output": "parent-note"})],
            kind="notes",
        )
    )["encrypted_output"]
    return {
        **envelope(),
        "client_metadata": {"session_id": FORK_SID},
        "input": [
            {"role": "user", "content": "Read the note"},
            {"type": "function_call", "name": "read_file", "call_id": "call_1", "arguments": "{}"},
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": [{"type": "encrypted_content", "encrypted_content": token}],
            },
        ],
    }


@pytest.mark.parametrize("path", ["/backend-api/codex/responses", "/v1/responses"])
async def test_fork_replays_result_and_keeps_independent_notes(async_client, monkeypatch, path):
    owner, other, headers, key = await setup_pool(async_client)
    await record_context_dispatch(envelope(), key, owner)
    await record_context_dispatch(envelope(), key, other)
    seen = []

    async def stream(payload, *_args, **_kwargs):
        seen.append(payload.to_payload())
        assert seen[-1]["client_metadata"]["session_id"] == FORK_SID
        assert seen[-1]["input"][-1]["output"][0]["encrypted_content"] == "parent-note"
        async with SessionLocal() as session:
            fork = await session.get(CodexContextSession, FORK_SID)
            assert fork is not None and fork.api_key_id == key.id
        yield 'data: {"type":"response.output_text.delta","delta":"recovered"}\n\n'
        yield 'data: {"type":"response.completed","response":{"id":"resp_fork","status":"completed","output":[]}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", stream)
    response = await async_client.post(path, headers=headers, json=fork_request(key.id, owner))
    assert response.status_code == 200 and "response.completed" in response.text
    assert len(seen) == 1
    async with SessionLocal() as session:
        parent = await session.get(CodexContextSession, SID)
        fork = await session.get(CodexContextSession, FORK_SID)
        assert parent is not None and parent.owner_account_id == owner
        assert fork is not None
        fork_owner = fork.owner_account_id
        participants = set(
            await session.scalars(
                select(CodexContextParticipant.account_id).where(CodexContextParticipant.session_id == FORK_SID)
            )
        )
        assert participants == {fork_owner}

    upstream = AsyncMock(return_value=CodexControlResponse(status_code=200, body=b'{"ok":true}', headers={}))
    monkeypatch.setattr(proxy_module, "core_codex_control_request", upstream)
    for session_id, expected_owner in [(FORK_SID, fork_owner), (SID, owner)]:
        context = {"session_id": session_id, "current_agent_name": "/root"}
        response = await async_client.post(
            "/backend-api/codex/alpha/notes/v2/write_file", headers=headers, json={"context": context}
        )
        assert response.status_code == 200
        async with SessionLocal() as session:
            account = await session.get(Account, expected_owner)
            assert account is not None
            assert upstream.call_args.kwargs["account_id"] == account.chatgpt_account_id
        assert json.loads(upstream.call_args.kwargs["payload"])["context"] == context


@pytest.mark.parametrize("denial", ["other_key", "foreign_target", "missing_context", "excluded_account"])
async def test_fork_rejects_scope_violations_before_dispatch(async_client, monkeypatch, denial):
    owner, other, headers, key = await setup_pool(async_client)
    await record_context_dispatch(envelope(), key, owner)
    payload = fork_request(key.id, owner)
    if denial == "other_key":
        headers = await _context_key(async_client, [])
    elif denial == "foreign_target":
        await record_context_dispatch(payload, replace(key, id="foreign-key"), other)
        get_context_dispatch_cache().clear()
    elif denial == "missing_context":
        payload.pop("reasoning")
    else:
        from app.modules.api_keys.repository import ApiKeysRepository
        from app.modules.api_keys.service import ApiKeysService, ApiKeyUpdateData

        async with SessionLocal() as session:
            await ApiKeysService(ApiKeysRepository(session)).update_key(
                key.id, ApiKeyUpdateData(assigned_account_ids=[other], assigned_account_ids_set=True)
            )
    upstream = AsyncMock()
    monkeypatch.setattr(proxy_module, "core_stream_responses", upstream)
    response = await async_client.post("/backend-api/codex/responses", headers=headers, json=payload)
    assert response.status_code == 403
    upstream.assert_not_called()

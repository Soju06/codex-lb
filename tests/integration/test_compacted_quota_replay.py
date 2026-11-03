import copy

import pytest

import app.modules.proxy.service as proxy_module
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from tests.integration.test_proxy_transient_retry import (
    _extract_events,
    _import_account,
    _sse_event,
    _success_sse_event,
)


def compacted_input(tool_loop=False):
    items = [
        {
            "type": "additional_tools",
            "role": "developer",
            "id": "at_original",
            "tools": [
                {
                    "type": "namespace",
                    "name": "functions",
                    "description": "local tools",
                    "tools": [
                        {
                            "type": "function",
                            "name": "read",
                            "parameters": {"type": "object", "properties": {}},
                            "strict": True,
                        }
                    ],
                }
            ],
        },
        {"type": "compaction", "id": "cmp_original", "encrypted_content": "opaque-task-must-not-be-lost"},
        {
            "type": "message",
            "role": "user",
            "content": "Continue the task.",
            "internal_chat_message_metadata_passthrough": {
                "turn_id": "turn1",
                "create_time": 1780000000,
                "content_item_kinds": ["user.text"],
                "content_item_metadata": [{"provenance": {"type": "user"}}],
            },
        },
    ]
    if tool_loop:
        items += [
            {
                "type": "reasoning",
                "id": "rs_original",
                "summary": [],
                "content": [],
                "encrypted_content": "opaque-reasoning",
            },
            {"type": "custom_tool_call", "id": "ct1", "call_id": "c1", "name": "exec", "input": "read data"},
            {"type": "custom_tool_call", "id": "ct2", "call_id": "c2", "name": "exec", "input": "read more"},
            {"type": "custom_tool_call_output", "call_id": "c1", "output": "saved result one"},
            {"type": "custom_tool_call_output", "call_id": "c2", "output": "saved result two"},
        ]
    # Match the real Codex failure: inline sub-agent reply followed by a
    # namespaced collaboration call and its client-side result.
    items[2:2] = [
        {
            "type": "agent_message",
            "id": "am_child",
            "author": "/root/worker",
            "recipient": "/root",
            "content": [{"type": "input_text", "text": "Worker completed its inspection."}],
        },
    ]
    if tool_loop:
        items += [
            {"type": "function_call", "namespace": "collaboration", "name": "wait", "call_id": "c3", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "c3", "output": "worker finished"},
        ]
    return items


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/backend-api/codex/responses", "/v1/responses"])
@pytest.mark.parametrize("delivery", ["status", "sse"])
@pytest.mark.parametrize("tool_loop", [False, True])
async def test_compacted_history_survives_five_quota_failovers(async_client, monkeypatch, path, delivery, tool_loop):
    for n in range(6):
        await _import_account(async_client, f"quota_{n}", f"quota{n}@example.com")
    seen = []

    async def upstream(payload, headers, access_token, account_id, **kwargs):
        seen.append((account_id, copy.deepcopy(payload.input)))
        if len(seen) <= 5:
            if delivery == "status":
                raise ProxyResponseError(
                    429, openai_error("usage_limit_reached", "usage limit reached"), failure_phase="status"
                )
            yield _sse_event(
                {
                    "type": "response.failed",
                    "response": {"error": {"code": "usage_limit_reached", "message": "usage limit reached"}},
                }
            )
        else:
            yield _success_sse_event("resp_full_recovery")

    monkeypatch.setattr(proxy_module, "core_stream_responses", upstream)
    body = {"model": "gpt-5.6-sol", "instructions": "work", "input": compacted_input(tool_loop), "stream": True}
    response = await async_client.post(path, json=body, headers={"session_id": "compacted-quota"})
    assert response.status_code == 200
    events = _extract_events(response.text.splitlines())
    assert any(e.get("type") == "response.completed" for e in events)
    assert not any(e.get("type") == "response.failed" for e in events)
    assert len(seen) == 6 and len({account for account, _ in seen}) == 6
    assert all(items == seen[0][1] for _, items in seen)
    assert all(any(i.get("encrypted_content") == "opaque-task-must-not-be-lost" for i in items) for _, items in seen)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["visible", "unauthorized", "invalid_ciphertext", "disabled"])
async def test_compacted_replay_never_broadens_retry_conditions(async_client, monkeypatch, failure):
    await _import_account(async_client, "guard_a", "guard-a@example.com")
    await _import_account(async_client, "guard_b", "guard-b@example.com")
    seen = []
    if failure == "disabled":
        monkeypatch.setattr(proxy_module.get_settings(), "deterministic_failover_enabled", False)

    async def upstream(payload, headers, access_token, account_id, **kwargs):
        seen.append(account_id)
        if failure == "visible":
            yield _sse_event({"type": "response.output_text.delta", "delta": "already visible"})
            yield _sse_event(
                {
                    "type": "response.failed",
                    "response": {"error": {"code": "usage_limit_reached", "message": "usage limit reached"}},
                }
            )
        elif failure == "unauthorized":
            raise ProxyResponseError(401, openai_error("authentication_error", "invalid token"), failure_phase="status")
        elif failure == "invalid_ciphertext":
            raise ProxyResponseError(
                400, openai_error("invalid_encrypted_content", "ciphertext invalid"), failure_phase="status"
            )
        else:
            raise ProxyResponseError(
                429, openai_error("usage_limit_reached", "usage limit reached"), failure_phase="status"
            )

    monkeypatch.setattr(proxy_module, "core_stream_responses", upstream)
    await async_client.post(
        "/backend-api/codex/responses",
        json={"model": "gpt-5.6-sol", "instructions": "work", "input": compacted_input(True), "stream": True},
    )
    assert seen and len(set(seen)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("neutral_body", [False, True])
async def test_legacy_session_affinity_does_not_block_stateless_quota_failover(async_client, monkeypatch, neutral_body):
    from app.db.models import StickySession, StickySessionKind
    from app.db.session import SessionLocal

    owner = await _import_account(async_client, "legacy_a", "legacy-a@example.com")
    await _import_account(async_client, "legacy_b", "legacy-b@example.com")
    async with SessionLocal() as session:
        session.add(StickySession(key="legacy-hard-session", kind=StickySessionKind.CODEX_SESSION, account_id=owner))
        await session.commit()
    attempts = []

    async def upstream(payload, headers, access_token, account_id, **kwargs):
        attempts.append(account_id)
        if account_id == "legacy_a":
            raise ProxyResponseError(
                429, openai_error("usage_limit_reached", "quota exhausted"), failure_phase="status"
            )
        yield _success_sse_event("resp_legacy_moved")

    monkeypatch.setattr(proxy_module, "core_stream_responses", upstream)
    body = {
        "model": "gpt-5.6-sol",
        "instructions": "work",
        "input": ([{"role": "user", "content": "hello"}] if neutral_body else compacted_input(True)),
        "stream": True,
    }
    response = await async_client.post(
        "/backend-api/codex/responses", json=body, headers={"session_id": "legacy-hard-session"}
    )
    assert response.status_code == 200
    assert "resp_legacy_moved" in response.text
    assert attempts == ["legacy_a", "legacy_b"]


@pytest.mark.asyncio
async def test_unavailable_legacy_session_owner_can_move_before_dispatch(async_client, monkeypatch):
    import time

    from app.db.models import Account, AccountStatus, StickySession, StickySessionKind
    from app.db.session import SessionLocal
    from app.modules.proxy.load_balancer import get_account_selection_cache

    owner = await _import_account(async_client, "retired_a", "retired-a@example.com")
    await _import_account(async_client, "retired_b", "retired-b@example.com")
    async with SessionLocal() as session:
        session.add(StickySession(key="retired-hard-session", kind=StickySessionKind.CODEX_SESSION, account_id=owner))
        account = await session.get(Account, owner)
        account.status = AccountStatus.RATE_LIMITED
        account.blocked_at = int(time.time())
        account.reset_at = int(time.time()) + 3600
        await session.commit()
    get_account_selection_cache().invalidate()
    attempts = []

    async def upstream(payload, headers, access_token, account_id, **kwargs):
        attempts.append(account_id)
        yield _success_sse_event("resp_retired_moved")

    monkeypatch.setattr(proxy_module, "core_stream_responses", upstream)
    body = {"model": "gpt-5.6-sol", "instructions": "work", "input": compacted_input(True), "stream": True}
    response = await async_client.post(
        "/backend-api/codex/responses", json=body, headers={"session_id": "retired-hard-session"}
    )
    assert response.status_code == 200
    assert "resp_retired_moved" in response.text
    assert attempts == ["retired_b"]


@pytest.mark.asyncio
@pytest.mark.parametrize("guard", ["owned_resource", "disabled"])
async def test_unavailable_legacy_owner_does_not_bypass_migration_guards(async_client, monkeypatch, guard):
    import time

    from app.db.models import Account, AccountStatus, StickySession, StickySessionKind
    from app.db.session import SessionLocal
    from app.modules.proxy.load_balancer import get_account_selection_cache

    owner = await _import_account(async_client, "blocked_a", "blocked-a@example.com")
    await _import_account(async_client, "blocked_b", "blocked-b@example.com")
    async with SessionLocal() as session:
        session.add(StickySession(key="blocked-hard-session", kind=StickySessionKind.CODEX_SESSION, account_id=owner))
        account = await session.get(Account, owner)
        account.status = AccountStatus.RATE_LIMITED
        account.blocked_at = int(time.time())
        account.reset_at = int(time.time()) + 3600
        await session.commit()
    get_account_selection_cache().invalidate()
    if guard == "disabled":
        monkeypatch.setattr(proxy_module.get_settings(), "deterministic_failover_enabled", False)
    monkeypatch.setattr(proxy_module, "_stream_request_budget_seconds", lambda *args, **kwargs: 1.0)

    attempts = []

    async def upstream(payload, headers, access_token, account_id, **kwargs):
        attempts.append(account_id)
        yield _success_sse_event("resp_must_not_move")

    monkeypatch.setattr(proxy_module, "core_stream_responses", upstream)
    items = compacted_input(True)
    if guard == "owned_resource":
        items.append({"type": "item_reference", "id": "owned_item"})
    body = {"model": "gpt-5.6-sol", "instructions": "work", "input": items, "stream": True}
    response = await async_client.post(
        "/backend-api/codex/responses", json=body, headers={"session_id": "blocked-hard-session"}
    )
    assert "resp_must_not_move" not in response.text
    assert "blocked_b" not in attempts


@pytest.mark.asyncio
async def test_inline_mcp_and_computer_tool_history_can_move_on_quota(async_client, monkeypatch):
    await _import_account(async_client, "inline_a", "inline-a@example.com")
    await _import_account(async_client, "inline_b", "inline-b@example.com")
    attempts = []

    async def upstream(payload, headers, access_token, account_id, **kwargs):
        attempts.append((account_id, copy.deepcopy(payload.to_payload())))
        if len(attempts) == 1:
            raise ProxyResponseError(
                429, openai_error("usage_limit_reached", "quota exhausted"), failure_phase="status"
            )
        yield _success_sse_event("resp_inline_moved")

    monkeypatch.setattr(proxy_module, "core_stream_responses", upstream)
    items = compacted_input(True)
    items.extend(
        [
            {"type": "computer_call", "call_id": "call_pc", "action": {"type": "click", "x": 1, "y": 2}},
            {
                "type": "computer_call_output",
                "call_id": "call_pc",
                "output": {"type": "computer_screenshot", "image_url": "data:image/png;base64,abc"},
            },
            {"type": "mcp_call", "server_label": "service", "arguments": "{}", "output": "result"},
        ]
    )
    body = {
        "model": "gpt-5.6-sol",
        "instructions": "work",
        "input": items,
        "tools": [{"type": "mcp", "server_label": "service", "server_url": "https://example.com/mcp"}],
        "stream": True,
    }
    response = await async_client.post("/backend-api/codex/responses", json=body)
    assert "resp_inline_moved" in response.text
    assert len(attempts) == 2 and attempts[0][0] != attempts[1][0]
    assert attempts[0][1] == attempts[1][1]

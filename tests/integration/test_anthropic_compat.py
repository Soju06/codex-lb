from __future__ import annotations

import base64
import json

import pytest

import app.modules.proxy.service as proxy_module
from app.core.openai.model_registry import ReasoningLevel, UpstreamModel
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeysService

pytestmark = pytest.mark.integration


def _make_upstream_model(slug: str) -> UpstreamModel:
    return UpstreamModel(
        slug=slug,
        display_name=slug,
        description=f"Test model {slug}",
        context_window=128000,
        input_modalities=("text",),
        supported_reasoning_levels=(ReasoningLevel(effort="medium", description="default"),),
        default_reasoning_level="medium",
        supports_reasoning_summaries=False,
        support_verbosity=False,
        default_verbosity=None,
        prefer_websockets=False,
        supports_parallel_tool_calls=True,
        supported_in_api=True,
        minimal_client_version=None,
        priority=0,
        available_in_plans=frozenset({"plus"}),
        raw={},
    )


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _make_auth_json(account_id: str, email: str) -> dict:
    payload = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    return {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": account_id,
        },
    }


async def _import_account(async_client, account_id: str, email: str) -> None:
    auth_json = _make_auth_json(account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200


def _parse_sse_events(lines: list[str]) -> list[tuple[str | None, dict]]:
    events: list[tuple[str | None, dict]] = []
    current_event: str | None = None
    for line in lines:
        if line.startswith("event: "):
            current_event = line[7:]
            continue
        if not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload == "[DONE]":
            continue
        events.append((current_event, json.loads(payload)))
        current_event = None
    return events


@pytest.mark.asyncio
async def test_anthropic_messages_non_stream(async_client, monkeypatch):
    await _import_account(async_client, "acc_anthropic_non_stream", "anthropic-non-stream@example.com")

    seen: dict[str, object] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield 'data: {"type":"response.output_text.delta","delta":"Hello"}\n\n'
        yield (
            'data: {"type":"response.completed","response":'
            '{"id":"resp_1","usage":{"input_tokens":3,"output_tokens":5}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    request_payload = {
        "model": "gpt-5.1",
        "messages": [{"role": "user", "content": "hi"}],
    }
    response = await async_client.post("/v1/messages", json=request_payload)
    assert response.status_code == 200

    body = response.json()
    assert body["type"] == "message"
    assert body["role"] == "assistant"
    assert body["model"] == "gpt-5.1"
    assert body["content"] == [{"type": "text", "text": "Hello"}]
    assert body["stop_reason"] == "end_turn"
    assert body["usage"] == {"input_tokens": 3, "output_tokens": 5}

    seen_payload = seen["payload"]
    assert getattr(seen_payload, "instructions", None) == ""
    assert getattr(seen_payload, "input", None) == [
        {"role": "user", "content": [{"type": "input_text", "text": "hi"}]}
    ]


@pytest.mark.asyncio
async def test_anthropic_messages_accept_system_role_and_cache_control(async_client, monkeypatch):
    await _import_account(async_client, "acc_anthropic_system_role", "anthropic-system-role@example.com")

    seen: dict[str, object] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield 'data: {"type":"response.output_text.delta","delta":"Hello"}\n\n'
        yield (
            'data: {"type":"response.completed","response":'
            '{"id":"resp_system_role","usage":{"input_tokens":3,"output_tokens":5}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    request_payload = {
        "model": "gpt-5.1",
        "messages": [
            {
                "role": "system",
                "content": [
                    {"type": "text", "text": "Use concise answers", "cache_control": {"type": "ephemeral"}}
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}},
                ],
            },
        ],
    }
    response = await async_client.post("/v1/messages", json=request_payload)
    assert response.status_code == 200

    seen_payload = seen["payload"]
    assert getattr(seen_payload, "instructions", None) == "Use concise answers"
    assert getattr(seen_payload, "input", None) == [
        {"role": "user", "content": [{"type": "input_text", "text": "hi"}]}
    ]


@pytest.mark.asyncio
async def test_anthropic_messages_forces_claude_model_and_reasoning_effort(async_client, monkeypatch):
    await _import_account(async_client, "acc_anthropic_model_map", "anthropic-model-map@example.com")

    seen: dict[str, object] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["model"] = getattr(payload, "model", None)
        reasoning = getattr(payload, "reasoning", None)
        seen["reasoning_effort"] = getattr(reasoning, "effort", None) if reasoning is not None else None
        seen["instructions"] = getattr(payload, "instructions", None)
        seen["input"] = getattr(payload, "input", None)
        seen["prompt_cache_key"] = getattr(payload, "prompt_cache_key", None)
        seen["temperature"] = getattr(payload, "temperature", None)
        seen["top_p"] = getattr(payload, "top_p", None)
        seen["top_k"] = getattr(payload, "top_k", None)
        yield 'data: {"type":"response.output_text.delta","delta":"Hello"}\n\n'
        yield (
            'data: {"type":"response.completed","response":'
            '{"id":"resp_model_map","usage":{"input_tokens":3,"output_tokens":5}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    request_payload = {
        "model": "claude-sonnet-4-6",
        "system": [
            {
                "type": "text",
                "text": "Cached context",
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.2,
        "top_p": 0.9,
        "top_k": 32,
    }
    response = await async_client.post("/anthropic/v1/messages?beta=true", json=request_payload)
    assert response.status_code == 200
    assert seen["model"] == "gpt-5.3-codex"
    assert seen["reasoning_effort"] == "xhigh"
    assert isinstance(seen["prompt_cache_key"], str)
    assert seen["prompt_cache_key"].startswith("anthropic-cache:")
    assert seen["temperature"] is None
    assert seen["top_p"] is None
    assert seen["top_k"] is None
    assert seen["instructions"] == ""
    assert seen["input"] == [
        {
            "role": "developer",
            "content": [{"type": "input_text", "text": "Cached context"}],
        },
        {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
    ]
    assert response.json()["model"] == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_anthropic_messages_claude_preserves_explicit_prompt_cache_key(async_client, monkeypatch):
    await _import_account(async_client, "acc_anthropic_explicit_cache", "anthropic-explicit-cache@example.com")

    seen: dict[str, object] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["prompt_cache_key"] = getattr(payload, "prompt_cache_key", None)
        seen["prompt_cache_retention"] = getattr(payload, "prompt_cache_retention", None)
        yield 'data: {"type":"response.output_text.delta","delta":"Hello"}\n\n'
        yield (
            'data: {"type":"response.completed","response":'
            '{"id":"resp_explicit_cache","usage":{"input_tokens":3,"output_tokens":5}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    request_payload = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "hi"}],
        "prompt_cache_key": "thread_123",
        "prompt_cache_retention": "24h",
    }
    response = await async_client.post("/anthropic/v1/messages?beta=true", json=request_payload)
    assert response.status_code == 200
    assert seen["prompt_cache_key"] == "thread_123"
    assert seen["prompt_cache_retention"] == "24h"


@pytest.mark.asyncio
async def test_anthropic_messages_sets_anchor_prompt_cache_key_without_cache_control(async_client, monkeypatch):
    await _import_account(async_client, "acc_anthropic_anchor_cache", "anthropic-anchor-cache@example.com")

    seen: dict[str, object] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["prompt_cache_key"] = getattr(payload, "prompt_cache_key", None)
        yield 'data: {"type":"response.output_text.delta","delta":"Hello"}\n\n'
        yield (
            'data: {"type":"response.completed","response":'
            '{"id":"resp_anchor_cache","usage":{"input_tokens":3,"output_tokens":5}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    request_payload = {
        "model": "claude-sonnet-4-6",
        "system": "You are Claude Code harness",
        "messages": [{"role": "user", "content": "Initial task"}],
    }
    response = await async_client.post("/anthropic/v1/messages?beta=true", json=request_payload)
    assert response.status_code == 200
    assert isinstance(seen["prompt_cache_key"], str)
    assert seen["prompt_cache_key"].startswith("claude-shared:")


@pytest.mark.asyncio
async def test_anthropic_messages_claude_ignores_volatile_prompt_cache_metadata(async_client, monkeypatch):
    await _import_account(async_client, "acc_anthropic_shared_cache", "anthropic-shared-cache@example.com")

    seen_prompt_cache_keys: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_prompt_cache_keys.append(getattr(payload, "prompt_cache_key", None))
        yield 'data: {"type":"response.output_text.delta","delta":"Hello"}\n\n'
        yield (
            'data: {"type":"response.completed","response":'
            '{"id":"resp_shared_cache","usage":{"input_tokens":3,"output_tokens":5}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    first_payload = {
        "model": "claude-sonnet-4-6",
        "system": "You are Claude Code harness",
        "messages": [{"role": "user", "content": "Initial task"}],
        "metadata": {"session_id": "volatile-session-a"},
    }
    second_payload = {
        "model": "claude-sonnet-4-6",
        "system": "You are Claude Code harness",
        "messages": [{"role": "user", "content": "Initial task"}],
        "metadata": {"session_id": "volatile-session-b"},
    }

    first_response = await async_client.post("/anthropic/v1/messages?beta=true", json=first_payload)
    assert first_response.status_code == 200
    second_response = await async_client.post("/anthropic/v1/messages?beta=true", json=second_payload)
    assert second_response.status_code == 200

    assert len(seen_prompt_cache_keys) == 2
    assert isinstance(seen_prompt_cache_keys[0], str)
    assert isinstance(seen_prompt_cache_keys[1], str)
    assert seen_prompt_cache_keys[0].startswith("claude-shared:")
    assert seen_prompt_cache_keys[0] == seen_prompt_cache_keys[1]


@pytest.mark.asyncio
async def test_anthropic_messages_alias_stream(async_client, monkeypatch):
    await _import_account(async_client, "acc_anthropic_stream", "anthropic-stream@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield 'data: {"type":"response.output_text.delta","delta":"Hi"}\n\n'
        yield 'data: {"type":"response.output_text.delta","delta":" there"}\n\n'
        yield (
            'data: {"type":"response.completed","response":'
            '{"id":"resp_stream","usage":{"input_tokens":2,"output_tokens":4}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    request_payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }
    async with async_client.stream("POST", "/anthropic/v1/messages", json=request_payload) as response:
        assert response.status_code == 200
        lines = [line async for line in response.aiter_lines() if line]

    events = _parse_sse_events(lines)
    event_names = [event_name for event_name, _ in events]
    assert "message_start" in event_names
    assert "content_block_start" in event_names
    assert "content_block_delta" in event_names
    assert "content_block_stop" in event_names
    assert "message_delta" in event_names
    assert "message_stop" in event_names


@pytest.mark.asyncio
async def test_anthropic_count_tokens(async_client, monkeypatch):
    await _import_account(async_client, "acc_anthropic_count_tokens", "anthropic-count@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        raise AssertionError("count_tokens must not call upstream")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    request_payload = {
        "model": "gpt-5.1",
        "messages": [{"role": "user", "content": "count me"}],
    }
    response = await async_client.post("/v1/messages/count_tokens", json=request_payload)
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body.get("input_tokens"), int)
    assert body["input_tokens"] > 0


@pytest.mark.asyncio
async def test_anthropic_count_tokens_uses_dedicated_cache_lane_for_claude(async_client, monkeypatch):
    await _import_account(async_client, "acc_anthropic_count_cache_lane", "anthropic-count-lane@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        raise AssertionError("count_tokens must not call upstream")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    request_payload = {
        "model": "claude-sonnet-4-6",
        "system": "You are Claude Code harness",
        "messages": [{"role": "user", "content": "count me"}],
    }
    response = await async_client.post("/anthropic/v1/messages/count_tokens?beta=true", json=request_payload)
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body.get("input_tokens"), int)
    assert body["input_tokens"] > 0


@pytest.mark.asyncio
async def test_anthropic_count_tokens_missing_usage_returns_5xx(async_client, monkeypatch):
    await _import_account(async_client, "acc_anthropic_count_missing", "anthropic-count-missing@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        raise AssertionError("count_tokens must not call upstream")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    request_payload = {
        "model": "gpt-5.1",
        "messages": [{"role": "user", "content": "count me"}],
    }
    response = await async_client.post("/v1/messages/count_tokens", json=request_payload)
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body.get("input_tokens"), int)
    assert body["input_tokens"] > 0


@pytest.mark.asyncio
async def test_anthropic_event_logging_batch_stub(async_client):
    response = await async_client.post("/api/event_logging/batch", json={"events": []})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    alias_response = await async_client.post("/anthropic/api/event_logging/batch", json={"events": []})
    assert alias_response.status_code == 200
    assert alias_response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_anthropic_auth_accepts_x_api_key_and_bearer(async_client, monkeypatch):
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
                name="anthropic-auth-key",
                allowed_models=None,
                expires_at=None,
            )
        )

    await _import_account(async_client, "acc_anthropic_auth", "anthropic-auth@example.com")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield (
            'data: {"type":"response.completed","response":'
            '{"id":"resp_auth","usage":{"input_tokens":1,"output_tokens":1}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.1",
        "messages": [{"role": "user", "content": "hi"}],
    }

    missing = await async_client.post("/v1/messages", json=payload)
    assert missing.status_code == 401
    missing_body = missing.json()
    assert missing_body["type"] == "error"
    assert missing_body["error"]["type"] == "authentication_error"

    bearer = await async_client.post(
        "/v1/messages",
        json=payload,
        headers={"Authorization": f"Bearer {created.key}"},
    )
    assert bearer.status_code == 200

    x_api_key = await async_client.post(
        "/v1/messages",
        json=payload,
        headers={"x-api-key": created.key},
    )
    assert x_api_key.status_code == 200

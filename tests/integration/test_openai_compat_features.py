from __future__ import annotations

import base64
import json

import pytest

import app.modules.proxy.service as proxy_module

pytestmark = pytest.mark.integration

_BUILTIN_RESPONSE_TOOL_PAYLOADS = [
    {"type": "file_search", "vector_store_ids": ["vs_dummy"]},
    {"type": "code_interpreter", "container": {"type": "auto"}},
    {
        "type": "computer_use_preview",
        "display_width": 1024,
        "display_height": 768,
        "environment": "browser",
    },
    {"type": "computer_use", "display_width": 1024, "display_height": 768, "environment": "browser"},
    {"type": "image_generation", "output_format": "png"},
]


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


def _completed_event(response_id: str) -> str:
    return 'data: {"type":"response.completed","response":{"id":"' + response_id + '","status":"completed"}}\n\n'


@pytest.mark.asyncio
async def test_v1_responses_forwards_input_file_url(async_client, monkeypatch):
    await _import_account(async_client, "acc_file_url", "file-url@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_file_url")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Summarize this file."},
                    {"type": "input_file", "file_url": "https://example.com/file.pdf"},
                ],
            }
        ],
    }
    resp = await async_client.post("/v1/responses", json=payload)
    assert resp.status_code == 200
    assert seen["payload"].input == payload["input"]


@pytest.mark.asyncio
async def test_v1_responses_rejects_input_file_id(async_client):
    payload = {
        "model": "gpt-5.2",
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "Summarize this file."},
                    {"type": "input_file", "file_id": "file-123"},
                ],
            }
        ],
    }
    resp = await async_client.post("/v1/responses", json=payload)
    assert resp.status_code == 400
    payload = resp.json()
    assert payload["error"]["type"] == "invalid_request_error"
    assert payload["error"]["message"] == "Invalid request payload"
    assert payload["error"]["param"] == "input"


@pytest.mark.asyncio
async def test_v1_responses_accepts_previous_response_id(async_client, monkeypatch):
    await _import_account(async_client, "acc_prev_response_id", "prev-response-id@example.com")
    seen_previous_response_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        del headers, access_token, account_id, base_url, raise_for_status, _kw
        seen_previous_response_ids.append(getattr(payload, "previous_response_id", None))
        yield 'data: {"type":"response.completed","response":{"id":"resp_abc123"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "previous_response_id": "resp_abc123",
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": "Continue."}],
            }
        ],
        "stream": True,
    }
    resp = await async_client.post("/v1/responses", json=payload)
    assert resp.status_code == 200
    assert seen_previous_response_ids == ["resp_abc123"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_payload", _BUILTIN_RESPONSE_TOOL_PAYLOADS)
async def test_v1_responses_allows_builtin_tools(async_client, monkeypatch, tool_payload):
    await _import_account(async_client, "acc_builtin_tools", "builtin-tools@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_builtin_tools")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    request_payload = {
        "model": "gpt-5.2",
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": "Run tool."}],
            }
        ],
        "tools": [tool_payload],
    }

    resp = await async_client.post("/v1/responses", json=request_payload)
    assert resp.status_code == 200
    assert seen["payload"].tools == [tool_payload]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_payload", _BUILTIN_RESPONSE_TOOL_PAYLOADS)
async def test_backend_responses_allows_builtin_tools(async_client, monkeypatch, tool_payload):
    await _import_account(
        async_client,
        f"acc_backend_{tool_payload['type']}",
        f"backend-{tool_payload['type']}@example.com",
    )

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_backend_builtin_tools")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    request_payload = {
        "model": "gpt-5.2",
        "instructions": "",
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": "Run tool."}],
            }
        ],
        "tools": [tool_payload],
    }

    resp = await async_client.post("/backend-api/codex/responses", json=request_payload)
    assert resp.status_code == 200
    assert seen["payload"].tools == [tool_payload]


@pytest.mark.asyncio
async def test_v1_responses_forwards_input_string(async_client, monkeypatch):
    await _import_account(async_client, "acc_input_string", "input-string@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_input_string")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.2", "input": "Hello"}
    resp = await async_client.post("/v1/responses", json=payload)
    assert resp.status_code == 200
    assert seen["payload"].input == [
        {"role": "user", "content": [{"type": "input_text", "text": "Hello"}]},
    ]


@pytest.mark.asyncio
async def test_v1_responses_forwards_include_logprobs(async_client, monkeypatch):
    await _import_account(async_client, "acc_include_logprobs", "include-logprobs@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_include")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}],
        "include": ["message.output_text.logprobs"],
    }
    resp = await async_client.post("/v1/responses", json=payload)
    assert resp.status_code == 200
    assert seen["payload"].include == ["message.output_text.logprobs"]


@pytest.mark.asyncio
async def test_v1_responses_preserves_prompt_cache_controls(async_client, monkeypatch):
    await _import_account(async_client, "acc_prompt_cache_v1", "prompt-cache-v1@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload.to_payload()
        yield _completed_event("resp_prompt_cache_v1")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "input": "cache me",
        "prompt_cache_key": "thread_123",
        "prompt_cache_retention": "4h",
    }
    resp = await async_client.post("/v1/responses", json=payload)
    assert resp.status_code == 200
    assert seen["payload"]["prompt_cache_key"] == "thread_123"
    assert "prompt_cache_retention" not in seen["payload"]


@pytest.mark.asyncio
async def test_v1_responses_normalizes_prompt_cache_aliases(async_client, monkeypatch):
    await _import_account(async_client, "acc_prompt_cache_alias", "prompt-cache-alias@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload.to_payload()
        yield _completed_event("resp_prompt_cache_alias")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "input": "cache me",
        "promptCacheKey": "thread_alias",
        "promptCacheRetention": "12h",
    }
    resp = await async_client.post("/v1/responses", json=payload)
    assert resp.status_code == 200
    assert seen["payload"]["prompt_cache_key"] == "thread_alias"
    assert "prompt_cache_retention" not in seen["payload"]
    assert "promptCacheKey" not in seen["payload"]
    assert "promptCacheRetention" not in seen["payload"]


@pytest.mark.asyncio
async def test_backend_responses_forwards_service_tier(async_client, monkeypatch):
    await _import_account(async_client, "acc_backend_service_tier", "backend-service-tier@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_backend_service_tier")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    request_payload = {
        "model": "gpt-5.2",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Fast"}]}],
        "service_tier": "priority",
    }
    resp = await async_client.post("/backend-api/codex/responses", json=request_payload)
    assert resp.status_code == 200
    assert seen["payload"].service_tier == "priority"


@pytest.mark.asyncio
async def test_backend_responses_normalizes_fast_service_tier_for_upstream(async_client, monkeypatch):
    await _import_account(async_client, "acc_backend_fast_tier", "backend-fast-tier@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload.to_payload()
        yield _completed_event("resp_backend_fast_tier")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    request_payload = {
        "model": "gpt-5.2",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Fast"}]}],
        "service_tier": "fast",
    }
    resp = await async_client.post("/backend-api/codex/responses", json=request_payload)
    assert resp.status_code == 200
    assert seen["payload"]["service_tier"] == "priority"


@pytest.mark.asyncio
async def test_v1_responses_rejects_invalid_include(async_client):
    payload = {"model": "gpt-5.2", "input": "hi", "include": ["not_allowed"]}
    resp = await async_client.post("/v1/responses", json=payload)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_v1_responses_rejects_store_true(async_client):
    payload = {"model": "gpt-5.2", "input": "hi", "store": True}
    resp = await async_client.post("/v1/responses", json=payload)
    assert resp.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("truncation", ["auto", "disabled"])
async def test_v1_responses_rejects_truncation(async_client, truncation):
    payload = {"model": "gpt-5.2", "input": "hi", "truncation": truncation}
    resp = await async_client.post("/v1/responses", json=payload)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_v1_responses_rejects_conversation_and_previous(async_client):
    payload = {
        "model": "gpt-5.2",
        "input": "hi",
        "conversation": "conv_1",
        "previous_response_id": "resp_1",
    }
    resp = await async_client.post("/v1/responses", json=payload)
    assert resp.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_type", ["web_search", "web_search_preview"])
async def test_v1_responses_allows_web_search(async_client, monkeypatch, tool_type):
    await _import_account(async_client, "acc_web_search", "web-search@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_web_search")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    request_payload = {
        "model": "gpt-5.2",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Search"}]}],
        "tools": [{"type": tool_type}],
    }
    resp = await async_client.post("/v1/responses", json=request_payload)
    assert resp.status_code == 200
    assert seen["payload"].tools == [{"type": "web_search"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_type", ["web_search", "web_search_preview"])
async def test_backend_responses_allows_web_search(async_client, monkeypatch, tool_type):
    await _import_account(async_client, "acc_backend_web_search", "backend-web-search@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_backend_web_search")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    request_payload = {
        "model": "gpt-5.2",
        "instructions": "",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "Search"}]}],
        "tools": [{"type": tool_type}],
    }
    resp = await async_client.post("/backend-api/codex/responses", json=request_payload)
    assert resp.status_code == 200
    assert seen["payload"].tools == [{"type": "web_search"}]


@pytest.mark.asyncio
async def test_v1_chat_completions_rejects_non_text_developer(async_client):
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {
                "role": "developer",
                "content": [{"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}],
            },
            {"role": "user", "content": "hi"},
        ],
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_v1_chat_completions_rejects_invalid_audio(async_client):
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "input_audio", "input_audio": {"data": "AAA", "format": "ogg"}},
                ],
            }
        ],
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_v1_chat_completions_maps_response_format(async_client, monkeypatch):
    await _import_account(async_client, "acc_chat_format", "chat-format@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_chat_format")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Return JSON."}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "result_schema",
                "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
                "strict": True,
            },
        },
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    text = seen["payload"].text
    assert text is not None
    assert text.format is not None
    assert text.format.type == "json_schema"
    assert text.format.name == "result_schema"


@pytest.mark.asyncio
async def test_v1_chat_completions_rejects_missing_json_schema(async_client):
    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Return JSON."}],
        "response_format": {"type": "json_schema"},
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_v1_chat_completions_forwards_multimodal(async_client, monkeypatch):
    await _import_account(async_client, "acc_chat_multi", "chat-multi@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_chat_multi")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Check image and audio."},
                    {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
                    {"type": "file", "file": {"file_url": "https://example.com/file.pdf"}},
                ],
            }
        ],
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    assert isinstance(seen["payload"].input, list)
    assert seen["payload"].input[0]["role"] == "user"
    content = seen["payload"].input[0]["content"]
    assert content[0] == {"type": "input_text", "text": "Check image and audio."}
    assert content[1] == {"type": "input_image", "image_url": "https://example.com/a.png"}
    assert content[2] == {"type": "input_file", "file_url": "https://example.com/file.pdf"}


@pytest.mark.asyncio
async def test_v1_chat_completions_rejects_file_id(async_client):
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Summarize file."},
                    {"type": "file", "file": {"file_id": "file-123"}},
                ],
            }
        ],
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 400
    payload = resp.json()
    assert payload["error"]["type"] == "invalid_request_error"
    assert payload["error"]["message"] == "Invalid request payload"
    assert payload["error"]["param"] == "messages"


@pytest.mark.asyncio
async def test_v1_chat_completions_rejects_audio_input(async_client):
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Transcribe audio."},
                    {"type": "input_audio", "input_audio": {"data": "AAA", "format": "wav"}},
                ],
            }
        ],
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_v1_chat_completions_rejects_builtin_tools(async_client):
    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Search the web."}],
        "tools": [{"type": "image_generation"}],
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_type", ["web_search", "web_search_preview"])
async def test_v1_chat_completions_allows_web_search(async_client, monkeypatch, tool_type):
    await _import_account(async_client, "acc_chat_web_search", "chat-web-search@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_chat_web_search")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Search the web."}],
        "tools": [{"type": tool_type}],
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    assert seen["payload"].tools == [{"type": "web_search"}]


@pytest.mark.asyncio
async def test_v1_chat_completions_normalizes_tools_and_tool_choice(async_client, monkeypatch):
    await _import_account(async_client, "acc_chat_tools", "chat-tools@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_chat_tools")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Weather?"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
                },
            }
        ],
        "tool_choice": {"type": "function", "function": {"name": "get_weather"}},
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    assert seen["payload"].tools == [
        {
            "type": "function",
            "name": "get_weather",
            "description": "Get weather",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
        }
    ]
    assert seen["payload"].tool_choice == {"type": "function", "name": "get_weather"}


@pytest.mark.asyncio
async def test_v1_chat_completions_does_not_enable_codex_session_affinity(async_client, monkeypatch):
    await _import_account(async_client, "acc_chat_affinity_a", "chat-affinity-a@example.com")
    await _import_account(async_client, "acc_chat_affinity_b", "chat-affinity-b@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["account_id"] = account_id
        seen["prompt_cache_key"] = getattr(payload, "prompt_cache_key", None)
        yield _completed_event("resp_chat_affinity")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Weather?"}],
    }
    resp = await async_client.post("/v1/chat/completions", json=payload, headers={"session_id": "chat-session-123"})
    assert resp.status_code == 200
    assert isinstance(seen["prompt_cache_key"], str)
    assert seen["prompt_cache_key"]


@pytest.mark.asyncio
async def test_v1_chat_completions_maps_reasoning_effort(async_client, monkeypatch):
    await _import_account(async_client, "acc_chat_reason", "chat-reason@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_chat_reason")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Think."}],
        "reasoning_effort": "low",
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    assert seen["payload"].reasoning is not None
    assert seen["payload"].reasoning.effort == "low"


@pytest.mark.asyncio
async def test_v1_chat_completions_forwards_service_tier(async_client, monkeypatch):
    await _import_account(async_client, "acc_chat_service_tier", "chat-service-tier@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield _completed_event("resp_chat_service_tier")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Think fast."}],
        "service_tier": "priority",
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    assert seen["payload"].service_tier == "priority"


@pytest.mark.asyncio
async def test_v1_chat_completions_preserves_prompt_cache_controls(async_client, monkeypatch):
    await _import_account(async_client, "acc_chat_prompt_cache", "chat-prompt-cache@example.com")

    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload.to_payload()
        yield _completed_event("resp_chat_prompt_cache")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Cache this chat."}],
        "prompt_cache_key": "chat_thread_123",
        "prompt_cache_retention": "8h",
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    assert seen["payload"]["prompt_cache_key"] == "chat_thread_123"
    assert "prompt_cache_retention" not in seen["payload"]

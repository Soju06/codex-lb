from __future__ import annotations

import base64
import json

import pytest

import app.modules.proxy.provider_adapters as provider_adapters_module

pytestmark = pytest.mark.integration


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


@pytest.mark.asyncio
async def test_v1_chat_completions_stream(async_client, monkeypatch):
    email = "chatstream@example.com"
    raw_account_id = "acc_chatstream"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        yield 'data: {"type":"response.output_text.delta","delta":"hi"}\n\n'
        yield 'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n'

    monkeypatch.setattr(provider_adapters_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.2", "messages": [{"role": "user", "content": "hi"}], "stream": True}
    async with async_client.stream("POST", "/v1/chat/completions", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    assert any("chat.completion.chunk" in line for line in lines)


@pytest.mark.asyncio
async def test_v1_chat_completions_non_stream_forces_stream(async_client, monkeypatch):
    email = "chatnonstr@example.com"
    raw_account_id = "acc_chatnonstr"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    observed_stream: dict[str, bool | None] = {"value": None}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        observed_stream["value"] = payload.stream
        yield 'data: {"type":"response.output_text.delta","delta":"hi"}\n\n'
        yield 'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n'

    monkeypatch.setattr(provider_adapters_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.2", "messages": [{"role": "user", "content": "hi"}]}
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    body = resp.json()

    assert observed_stream["value"] is True
    assert body["object"] == "chat.completion"


@pytest.mark.asyncio
async def test_v1_chat_completions_non_stream_deduplicates_tool_call_snapshots(async_client, monkeypatch):
    email = "chat-tool-snapshot@example.com"
    raw_account_id = "acc_chat_tool_snapshot"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        yield (
            'data: {"type":"response.output_tool_call.delta","call_id":"call_1",'
            '"name":"get_weather","arguments":"{\\"city\\":\\"Zur"}\n\n'
        )
        yield (
            'data: {"type":"response.function_call_arguments.done","call_id":"call_1",'
            '"name":"get_weather","arguments":"{\\"city\\":\\"Zurich\\",\\"unit\\":\\"C\\"}"}\n\n'
        )
        yield (
            'data: {"type":"response.output_item.done","output_index":0,"item":{"id":"call_1",'
            '"type":"function_call","name":"get_weather","arguments":"{\\"city\\":\\"Zurich\\",\\"unit\\":\\"C\\"}"}}\n\n'
        )
        yield 'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n'

    monkeypatch.setattr(provider_adapters_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Weather in Zurich?"}],
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
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)
    assert resp.status_code == 200
    body = resp.json()

    tool_call = body["choices"][0]["message"]["tool_calls"][0]
    assert tool_call["function"]["arguments"] == '{"city":"Zurich","unit":"C"}'


@pytest.mark.asyncio
async def test_v1_chat_completions_stream_deduplicates_tool_call_snapshots(async_client, monkeypatch):
    email = "chat-tool-stream@example.com"
    raw_account_id = "acc_chat_tool_stream"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        yield (
            'data: {"type":"response.output_tool_call.delta","call_id":"call_1",'
            '"name":"get_weather","arguments":"{\\"city\\":\\"Zur"}\n\n'
        )
        yield (
            'data: {"type":"response.function_call_arguments.done","call_id":"call_1",'
            '"name":"get_weather","arguments":"{\\"city\\":\\"Zurich\\",\\"unit\\":\\"C\\"}"}\n\n'
        )
        yield (
            'data: {"type":"response.output_item.done","output_index":0,"item":{"id":"call_1",'
            '"type":"function_call","name":"get_weather","arguments":"{\\"city\\":\\"Zurich\\",\\"unit\\":\\"C\\"}"}}\n\n'
        )
        yield 'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n'

    monkeypatch.setattr(provider_adapters_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Weather in Zurich?"}],
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
        "stream": True,
    }
    async with async_client.stream("POST", "/v1/chat/completions", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    collected_arguments = ""
    for line in lines:
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        payload = json.loads(line[6:])
        choices = payload.get("choices") or []
        if not choices:
            continue
        tool_calls = (choices[0].get("delta") or {}).get("tool_calls") or []
        if not tool_calls:
            continue
        arguments = (tool_calls[0].get("function") or {}).get("arguments")
        if arguments:
            collected_arguments += arguments

    assert collected_arguments == '{"city":"Zurich","unit":"C"}'


@pytest.mark.asyncio
async def test_v1_chat_completions_stream_skips_incompatible_snapshot_rewrites(
    async_client,
    monkeypatch,
):
    email = "chat-tool-stream-rewrite@example.com"
    raw_account_id = "acc_chat_tool_stream_rewrite"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        yield (
            'data: {"type":"response.output_tool_call.delta","call_id":"call_1",'
            '"name":"get_weather","arguments":"{\\"city\\":\\"Zur"}\n\n'
        )
        yield (
            'data: {"type":"response.function_call_arguments.done","call_id":"call_1",'
            '"name":"get_weather","arguments":"{\\"city\\": \\"Zurich\\", \\"unit\\": \\"C\\"}"}\n\n'
        )
        yield 'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n'

    monkeypatch.setattr(provider_adapters_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Weather in Zurich?"}],
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
        "stream": True,
    }
    async with async_client.stream("POST", "/v1/chat/completions", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    collected_arguments = ""
    for line in lines:
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        payload = json.loads(line[6:])
        choices = payload.get("choices") or []
        if not choices:
            continue
        tool_calls = (choices[0].get("delta") or {}).get("tool_calls") or []
        if not tool_calls:
            continue
        arguments = (tool_calls[0].get("function") or {}).get("arguments")
        if arguments:
            collected_arguments += arguments

    assert collected_arguments == '{"city":"Zur'


@pytest.mark.asyncio
async def test_v1_chat_completions_stream_preserves_tool_call_delta_before_failure(async_client, monkeypatch):
    email = "chat-tool-stream-failed@example.com"
    raw_account_id = "acc_chat_tool_stream_failed"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        yield (
            'data: {"type":"response.output_tool_call.delta","call_id":"call_1",'
            '"name":"get_weather","arguments":"{\\"city\\":\\"Zur"}\n\n'
        )
        yield (
            'data: {"type":"response.failed","response":{"id":"resp_1","status":"failed","error":'
            '{"message":"bad","type":"server_error","code":"no_accounts"}}}\n\n'
        )

    monkeypatch.setattr(provider_adapters_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "Weather in Zurich?"}],
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
        "stream": True,
    }
    async with async_client.stream("POST", "/v1/chat/completions", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    tool_argument_deltas = ""
    for line in lines:
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        payload = json.loads(line[6:])
        choices = payload.get("choices") or []
        if not choices:
            continue
        tool_calls = (choices[0].get("delta") or {}).get("tool_calls") or []
        if not tool_calls:
            continue
        arguments = (tool_calls[0].get("function") or {}).get("arguments")
        if arguments:
            tool_argument_deltas += arguments

    assert tool_argument_deltas == '{"city":"Zur'
    assert any('"error"' in line for line in lines)


@pytest.mark.asyncio
async def test_v1_chat_completions_stream_include_usage(async_client, monkeypatch):
    email = "chatusage@example.com"
    raw_account_id = "acc_chatusage"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        yield 'data: {"type":"response.output_text.delta","delta":"hi"}\n\n'
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_1","usage":'
            '{"input_tokens":2,"output_tokens":3,"total_tokens":5}}}\n\n'
        )

    monkeypatch.setattr(provider_adapters_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.2",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    async with async_client.stream("POST", "/v1/chat/completions", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    chunks = [json.loads(line[6:]) for line in lines if line.startswith("data: ") and line != "data: [DONE]"]
    assert chunks
    assert all("usage" in chunk for chunk in chunks)
    assert chunks[0]["usage"] is None
    assert chunks[-1]["usage"]["total_tokens"] == 5

from __future__ import annotations

import base64
import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import app.modules.proxy.service as proxy_module
from app.modules.proxy.response_context_cache import get_response_context_cache
from app.core.auth import generate_unique_account_id
from app.db.models import RequestLog
from app.db.session import SessionLocal

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


def _extract_first_event(lines: list[str]) -> dict:
    for line in lines:
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise AssertionError("No SSE data event found")


@pytest.mark.asyncio
async def test_proxy_responses_no_accounts(async_client):
    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    request_id = "req_stream_123"
    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json=payload,
        headers={"x-request-id": request_id},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.failed"
    assert event["response"]["object"] == "response"
    assert event["response"]["status"] == "failed"
    assert event["response"]["id"] == request_id
    assert event["response"]["error"]["code"] == "no_accounts"


@pytest.mark.asyncio
async def test_proxy_responses_requires_instructions(async_client):
    payload = {"model": "gpt-5.1", "input": []}
    resp = await async_client.post("/backend-api/codex/responses", json=payload)

    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_v1_responses_routes(async_client):
    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    request_id = "req_v1_stream_123"
    async with async_client.stream(
        "POST",
        "/v1/responses",
        json=payload,
        headers={"x-request-id": request_id},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.failed"
    assert event["response"]["object"] == "response"
    assert event["response"]["status"] == "failed"
    assert event["response"]["id"] == request_id
    assert event["response"]["error"]["code"] == "no_accounts"


@pytest.mark.asyncio
async def test_v1_responses_routes_under_root_path(app_instance):
    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    request_id = "req_v1_root_path_123"
    async with app_instance.router.lifespan_context(app_instance):
        transport = ASGITransport(app=app_instance, root_path="/api")
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            async with client.stream(
                "POST",
                "/v1/responses",
                json=payload,
                headers={"x-request-id": request_id},
            ) as resp:
                assert resp.status_code == 200
                lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.failed"
    assert event["response"]["object"] == "response"
    assert event["response"]["status"] == "failed"
    assert event["response"]["id"] == request_id
    assert event["response"]["error"]["code"] == "no_accounts"


@pytest.mark.asyncio
async def test_v1_responses_accepts_messages(async_client):
    payload = {
        "model": "gpt-5.1",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True,
    }
    request_id = "req_v1_messages_123"
    async with async_client.stream(
        "POST",
        "/v1/responses",
        json=payload,
        headers={"x-request-id": request_id},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.failed"
    assert event["response"]["object"] == "response"
    assert event["response"]["status"] == "failed"
    assert event["response"]["id"] == request_id
    assert event["response"]["error"]["code"] == "no_accounts"


@pytest.mark.asyncio
async def test_v1_responses_without_instructions(async_client):
    payload = {"model": "gpt-5.1", "input": [{"role": "user", "content": "hi"}], "stream": True}
    request_id = "req_v1_no_instructions_123"
    async with async_client.stream(
        "POST",
        "/v1/responses",
        json=payload,
        headers={"x-request-id": request_id},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.failed"
    assert event["response"]["object"] == "response"
    assert event["response"]["status"] == "failed"
    assert event["response"]["id"] == request_id
    assert event["response"]["error"]["code"] == "no_accounts"


@pytest.mark.asyncio
async def test_v1_responses_non_streaming_failed_returns_error(async_client):
    payload = {"model": "gpt-5.1", "input": "hi"}
    resp = await async_client.post("/v1/responses", json=payload)

    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "no_accounts"


@pytest.mark.asyncio
async def test_proxy_responses_streams_upstream(async_client, monkeypatch):
    email = "streamer@example.com"
    raw_account_id = "acc_live"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    seen = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["access_token"] = access_token
        seen["account_id"] = account_id
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_1","usage":'
            '{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    request_id = "req_stream_123"
    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json=payload,
        headers={"x-request-id": request_id},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.completed"
    assert seen["access_token"] == "access-token"
    assert seen["account_id"] == raw_account_id

    async with SessionLocal() as session:
        result = await session.execute(
            select(RequestLog)
            .where(RequestLog.account_id == expected_account_id)
            .order_by(RequestLog.requested_at.desc())
        )
        log = result.scalars().first()
        assert log is not None
        assert log.request_id == request_id


@pytest.mark.asyncio
async def test_v1_responses_stream_preserves_done_text_events(async_client, monkeypatch):
    email = "done-filter@example.com"
    raw_account_id = "acc_done_filter"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield 'data: {"type":"response.output_text.delta","delta":"Hey there! "}\n\n'
        yield 'data: {"type":"response.output_text.delta","delta":"What are we tackling?"}\n\n'
        yield 'data: {"type":"response.output_text.done","text":"Hey there! What are we tackling?"}\n\n'
        yield (
            'data: {"type":"response.content_part.done","part":{"type":"output_text",'
            '"text":"Hey there! What are we tackling?"}}\n\n'
        )
        yield 'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.2", "input": "hi", "stream": True}
    async with async_client.stream("POST", "/v1/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event_types: list[str] = []
    for line in lines:
        if not line.startswith("data: "):
            continue
        data = json.loads(line[6:])
        event_type = data.get("type")
        if isinstance(event_type, str):
            event_types.append(event_type)

    assert "response.output_text.delta" in event_types
    assert "response.completed" in event_types
    assert "response.output_text.done" in event_types
    assert "response.content_part.done" in event_types


@pytest.mark.asyncio
async def test_v1_responses_stream_keeps_non_text_content_part_done_events(async_client, monkeypatch):
    email = "done-filter-non-text@example.com"
    raw_account_id = "acc_done_filter_non_text"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield 'data: {"type":"response.output_text.delta","delta":"First line"}\n\n'
        yield (
            'data: {"type":"response.content_part.done","part":{"type":"output_image",'
            '"image_url":"https://example.com/a.png"}}\n\n'
        )
        yield 'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.2", "input": "hi", "stream": True}
    async with async_client.stream("POST", "/v1/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    content_part_events: list[dict[str, object]] = []
    for line in lines:
        if not line.startswith("data: "):
            continue
        raw_payload = line[6:]
        if raw_payload == "[DONE]":
            continue
        data = json.loads(raw_payload)
        if data.get("type") == "response.content_part.done":
            content_part_events.append(data)

    assert content_part_events
    assert content_part_events[0]["part"] == {"type": "output_image", "image_url": "https://example.com/a.png"}


@pytest.mark.asyncio
async def test_backend_responses_stream_preserves_done_text_events(async_client, monkeypatch):
    email = "done-preserve@example.com"
    raw_account_id = "acc_done_preserve"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield 'data: {"type":"response.output_text.delta","delta":"Hey there! "}\n\n'
        yield 'data: {"type":"response.output_text.delta","delta":"What are we tackling?"}\n\n'
        yield 'data: {"type":"response.output_text.done","text":"Hey there! What are we tackling?"}\n\n'
        yield (
            'data: {"type":"response.content_part.done","part":{"type":"output_text",'
            '"text":"Hey there! What are we tackling?"}}\n\n'
        )
        yield 'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.2", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event_types: list[str] = []
    for line in lines:
        if not line.startswith("data: "):
            continue
        raw_payload = line[6:]
        if raw_payload == "[DONE]":
            continue
        data = json.loads(raw_payload)
        event_type = data.get("type")
        if isinstance(event_type, str):
            event_types.append(event_type)

    assert "response.output_text.delta" in event_types
    assert "response.output_text.done" in event_types
    assert "response.content_part.done" in event_types
    assert "response.completed" in event_types


@pytest.mark.asyncio
async def test_v1_responses_sanitizes_interleaved_reasoning_fields(async_client, monkeypatch):
    email = "reasoning-sanitize@example.com"
    raw_account_id = "acc_reasoning_sanitize"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    seen_input: dict[str, object] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_input["input"] = payload.input
        yield 'data: {"type":"response.completed","response":{"id":"resp_reasoning_sanitize"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.1",
        "input": [
            {
                "role": "user",
                "reasoning_content": "drop",
                "tool_calls": [{"id": "call_1", "type": "function"}],
                "function_call": {"name": "noop", "arguments": "{}"},
                "content": [
                    {"type": "input_text", "text": "hello"},
                    {"type": "reasoning", "reasoning_details": {"tokens": 4}},
                    {"type": "input_text", "text": "world", "reasoning_content": "drop"},
                ],
            }
        ],
        "stream": True,
    }
    async with async_client.stream("POST", "/v1/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.completed"
    assert seen_input["input"] == [
        {
            "role": "user",
            "content": [
                {"type": "input_text", "text": "hello"},
                {"type": "input_text", "text": "world"},
            ],
        }
    ]


@pytest.mark.asyncio
async def test_v1_responses_drops_top_level_reasoning_artifact_items(async_client, monkeypatch):
    email = "reasoning-top-level@example.com"
    raw_account_id = "acc_reasoning_top_level"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    seen_input: dict[str, object] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_input["input"] = payload.input
        yield 'data: {"type":"response.completed","response":{"id":"resp_reasoning_top_level"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.1",
        "input": [
            {"id": "rs_abc123", "type": "reasoning", "summary": []},
            {"role": "user", "content": [{"type": "input_text", "text": "follow-up"}]},
        ],
        "stream": True,
    }
    async with async_client.stream("POST", "/v1/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.completed"
    assert seen_input["input"] == [
        {"role": "user", "content": [{"type": "input_text", "text": "follow-up"}]}
    ]


@pytest.mark.asyncio
async def test_proxy_responses_forces_stream(async_client, monkeypatch):
    email = "stream-force@example.com"
    raw_account_id = "acc_stream_force"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    observed_stream: dict[str, bool | None] = {"value": None}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        observed_stream["value"] = payload.stream
        yield 'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": False}
    async with async_client.stream("POST", "/backend-api/codex/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.completed"
    assert observed_stream["value"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_type", ["web_search", "web_search_preview"])
async def test_proxy_responses_accepts_builtin_tools(async_client, monkeypatch, tool_type):
    email = "tools@example.com"
    raw_account_id = "acc_tools"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    seen: dict[str, object] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["payload"] = payload
        yield 'data: {"type":"response.completed","response":{"id":"resp_tools"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "tools": [{"type": tool_type}],
        "stream": True,
    }
    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json=payload,
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.completed"
    assert getattr(seen.get("payload"), "tools", None) == [{"type": "web_search"}]


@pytest.mark.asyncio
async def test_v1_responses_streams_event_sequence(async_client, monkeypatch):
    email = "stream-seq@example.com"
    raw_account_id = "acc_stream_seq"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield 'data: {"type":"response.created","response":{"id":"resp_1"}}\n\n'
        yield 'data: {"type":"response.output_text.delta","delta":"hi"}\n\n'
        yield 'data: {"type":"response.function_call_arguments.delta","delta":"{}"}\n\n'
        yield 'data: {"type":"response.refusal.delta","delta":"no"}\n\n'
        yield 'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    async with async_client.stream("POST", "/v1/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    assert any("response.output_text.delta" in line for line in lines)
    assert any("response.function_call_arguments.delta" in line for line in lines)
    assert any("response.refusal.delta" in line for line in lines)


@pytest.mark.asyncio
async def test_proxy_responses_stream_large_event_line(async_client, monkeypatch):
    email = "stream-large@example.com"
    raw_account_id = "acc_stream_large"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        delta = "A" * (200 * 1024)
        yield f'data: {{"type":"response.output_text.delta","delta":"{delta}"}}\n\n'
        yield 'data: {"type":"response.completed","response":{"id":"resp_large"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    request_id = "req_stream_large_123"
    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json=payload,
        headers={"x-request-id": request_id},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    assert any("response.output_text.delta" in line for line in lines)
    assert any("response.completed" in line for line in lines)
    assert not any("stream_event_too_large" in line for line in lines)


@pytest.mark.asyncio
async def test_v1_responses_non_streaming_returns_response(async_client, monkeypatch):
    email = "responses-nonstream@example.com"
    raw_account_id = "acc_responses_nonstream"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    observed_stream: dict[str, bool | None] = {"value": None}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        observed_stream["value"] = payload.stream
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_1","object":"response",'
            '"status":"completed","output":[],"usage":{"input_tokens":1,"output_tokens":2,"total_tokens":3}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "input": [{"role": "user", "content": "hi"}], "stream": False}
    resp = await async_client.post("/v1/responses", json=payload)

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "resp_1"
    assert body["object"] == "response"
    assert body["status"] == "completed"
    assert observed_stream["value"] is True


@pytest.mark.asyncio
async def test_v1_responses_non_streaming_preserves_sse_error_payload(async_client, monkeypatch):
    email = "responses-error-event@example.com"
    raw_account_id = "acc_responses_error_event"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield (
            'data: {"type":"error","error":{"message":"No active accounts available",'
            '"type":"server_error","code":"no_accounts"}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "input": "hi", "stream": False}
    resp = await async_client.post("/v1/responses", json=payload)

    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "no_accounts"
    assert body["error"]["type"] == "server_error"
    assert body["error"]["message"] == "No active accounts available"


@pytest.mark.asyncio
async def test_v1_responses_non_streaming_failed_without_status_returns_error(async_client, monkeypatch):
    email = "responses-error-no-status@example.com"
    raw_account_id = "acc_responses_error_no_status"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield (
            'data: {"type":"response.failed","response":{"error":{"message":"No active accounts available",'
            '"type":"server_error","code":"no_accounts"}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "input": "hi", "stream": False}
    resp = await async_client.post("/v1/responses", json=payload)

    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["code"] == "no_accounts"
    assert body["error"]["type"] == "server_error"


@pytest.mark.asyncio
async def test_v1_responses_invalid_messages_returns_openai_400(async_client):
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {
                "role": "system",
                "content": [{"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}],
            },
            {"role": "user", "content": "hi"},
        ],
    }
    resp = await async_client.post("/v1/responses", json=payload)

    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["code"] == "invalid_request_error"
    assert body["error"]["param"] == "messages"


@pytest.mark.asyncio
async def test_v1_responses_compact_invalid_messages_returns_openai_400(async_client):
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {
                "role": "developer",
                "content": [{"type": "file", "file": {"file_url": "https://example.com/a.pdf"}}],
            },
            {"role": "user", "content": "hi"},
        ],
    }
    resp = await async_client.post("/v1/responses/compact", json=payload)

    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["code"] == "invalid_request_error"
    assert body["error"]["param"] == "messages"


@pytest.mark.asyncio
async def test_v1_chat_completions_invalid_tool_calls_returns_openai_400(async_client):
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "type": "function", "function": {"arguments": "{}"}}],
            },
            {"role": "user", "content": "continue"},
        ],
    }
    resp = await async_client.post("/v1/chat/completions", json=payload)

    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["code"] == "invalid_request_error"


@pytest.mark.asyncio
async def test_v1_responses_normalizes_assistant_input_text(async_client, monkeypatch):
    email = "assistant-normalize@example.com"
    raw_account_id = "acc_assistant_normalize"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    seen_input: dict[str, object] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_input["input"] = payload.input
        yield 'data: {"type":"response.completed","response":{"id":"resp_assistant_normalize"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.1",
        "input": [
            {"role": "assistant", "content": [{"type": "input_text", "text": "Prior answer"}]},
            {"role": "user", "content": [{"type": "input_text", "text": "Continue"}]},
        ],
        "stream": True,
    }
    async with async_client.stream("POST", "/v1/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.completed"
    assert seen_input["input"] == [
        {"role": "assistant", "content": [{"type": "output_text", "text": "Prior answer"}]},
        {"role": "user", "content": [{"type": "input_text", "text": "Continue"}]},
    ]


@pytest.mark.asyncio
async def test_v1_responses_normalizes_tool_messages(async_client, monkeypatch):
    email = "tool-normalize@example.com"
    raw_account_id = "acc_tool_normalize"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    seen_input: dict[str, object] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_input["input"] = payload.input
        yield 'data: {"type":"response.completed","response":{"id":"resp_tool_normalize"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.1",
        "messages": [
            {"role": "assistant", "content": "Running tool."},
            {"role": "tool", "tool_call_id": "call_1", "content": '{"ok":true}'},
            {"role": "user", "content": "continue"},
        ],
        "stream": True,
    }
    async with async_client.stream("POST", "/v1/responses", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    event = _extract_first_event(lines)
    assert event["type"] == "response.completed"
    assert seen_input["input"] == [
        {"role": "assistant", "content": [{"type": "output_text", "text": "Running tool."}]},
        {"type": "function_call_output", "call_id": "call_1", "output": '{"ok":true}'},
        {"role": "user", "content": [{"type": "input_text", "text": "continue"}]},
    ]


@pytest.mark.asyncio
async def test_v1_responses_resolves_item_reference_from_cached_response(async_client, monkeypatch):
    cache = get_response_context_cache()
    cache.clear()

    email = "ctx-cache@example.com"
    raw_account_id = "acc_ctx_cache"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    seen_inputs: list[object] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_inputs.append(json.loads(json.dumps(payload.input)))
        if len(seen_inputs) == 1:
            yield (
                'data: {"type":"response.completed","response":{"id":"rs_ctx_1","usage":'
                '{"input_tokens":1,"output_tokens":1,"total_tokens":2},"output":['
                '{"id":"msg_ctx_1","type":"message","role":"assistant","content":['
                '{"type":"output_text","text":"cached reply"}]'
                '}]}}\n\n'
            )
            return

        yield (
            'data: {"type":"response.completed","response":{"id":"rs_ctx_2","usage":'
            '{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    first_payload = {"model": "gpt-5.3-codex", "input": "hello", "stream": True}
    async with async_client.stream("POST", "/v1/responses", json=first_payload) as resp:
        assert resp.status_code == 200
        _ = [line async for line in resp.aiter_lines() if line]

    second_payload = {
        "model": "gpt-5.3-codex",
        "input": [
            {"type": "item_reference", "id": "rs_ctx_1"},
            {"role": "user", "content": [{"type": "input_text", "text": "follow-up"}]},
        ],
        "stream": True,
    }
    async with async_client.stream("POST", "/v1/responses", json=second_payload) as resp:
        assert resp.status_code == 200
        _ = [line async for line in resp.aiter_lines() if line]

    assert len(seen_inputs) == 2
    second_input = seen_inputs[1]
    assert isinstance(second_input, list)
    assert second_input[0]["type"] == "message"
    assert second_input[0]["content"][0]["text"] == "cached reply"
    assert second_input[1]["role"] == "user"


@pytest.mark.asyncio
async def test_v1_responses_resolves_previous_response_id_from_cached_response(async_client, monkeypatch):
    cache = get_response_context_cache()
    cache.clear()

    email = "ctx-prev@example.com"
    raw_account_id = "acc_ctx_prev"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    seen_inputs: list[object] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_inputs.append(json.loads(json.dumps(payload.input)))
        if len(seen_inputs) == 1:
            yield (
                'data: {"type":"response.completed","response":{"id":"rs_prev_1","usage":'
                '{"input_tokens":1,"output_tokens":1,"total_tokens":2},"output":['
                '{"id":"msg_prev_1","type":"message","role":"assistant","content":['
                '{"type":"output_text","text":"first reply"}'
                ']}]}}\n\n'
            )
            return

        yield (
            'data: {"type":"response.completed","response":{"id":"rs_prev_2","usage":'
            '{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    first_payload = {"model": "gpt-5.3-codex", "input": "hello", "store": True, "stream": True}
    async with async_client.stream("POST", "/v1/responses", json=first_payload) as resp:
        assert resp.status_code == 200
        _ = [line async for line in resp.aiter_lines() if line]

    second_payload = {
        "model": "gpt-5.3-codex",
        "previous_response_id": "rs_prev_1",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "follow-up"}]}],
        "stream": True,
    }
    async with async_client.stream("POST", "/v1/responses", json=second_payload) as resp:
        assert resp.status_code == 200
        _ = [line async for line in resp.aiter_lines() if line]

    assert len(seen_inputs) == 2
    second_input = seen_inputs[1]
    assert isinstance(second_input, list)
    assert second_input[0]["type"] == "message"
    assert second_input[0]["content"][0]["text"] == "first reply"
    assert second_input[1]["role"] == "user"


@pytest.mark.asyncio
async def test_v1_responses_resolves_previous_response_id_after_cache_clear_with_store_true(async_client, monkeypatch):
    cache = get_response_context_cache()
    cache.clear()

    email = "ctx-prev-durable@example.com"
    raw_account_id = "acc_ctx_prev_durable"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    seen_inputs: list[object] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_inputs.append(json.loads(json.dumps(payload.input)))
        if len(seen_inputs) == 1:
            yield (
                'data: {"type":"response.completed","response":{"id":"rs_prev_durable_1","usage":'
                '{"input_tokens":1,"output_tokens":1,"total_tokens":2},"output":['
                '{"id":"msg_prev_durable_1","type":"message","role":"assistant","content":['
                '{"type":"output_text","text":"durable reply"}'
                ']}]}}\n\n'
            )
            return

        yield (
            'data: {"type":"response.completed","response":{"id":"rs_prev_durable_2","usage":'
            '{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    first_payload = {
        "model": "gpt-5.3-codex",
        "input": "hello",
        "store": True,
        "stream": True,
    }
    async with async_client.stream("POST", "/v1/responses", json=first_payload) as resp:
        assert resp.status_code == 200
        _ = [line async for line in resp.aiter_lines() if line]

    cache.clear()

    second_payload = {
        "model": "gpt-5.3-codex",
        "previous_response_id": "rs_prev_durable_1",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "follow-up"}]}],
        "stream": True,
    }
    async with async_client.stream("POST", "/v1/responses", json=second_payload) as resp:
        assert resp.status_code == 200
        _ = [line async for line in resp.aiter_lines() if line]

    assert len(seen_inputs) == 2
    second_input = seen_inputs[1]
    assert isinstance(second_input, list)
    assert second_input[0]["type"] == "message"
    assert second_input[0]["content"][0]["text"] == "durable reply"


@pytest.mark.asyncio
async def test_v1_responses_returns_not_found_for_unresolved_item_reference(async_client, monkeypatch):
    cache = get_response_context_cache()
    cache.clear()

    email = "ctx-unresolved@example.com"
    raw_account_id = "acc_ctx_unresolved"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    seen_inputs: list[object] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen_inputs.append(json.loads(json.dumps(payload.input)))
        yield (
            'data: {"type":"response.completed","response":{"id":"rs_ctx_unknown","usage":'
            '{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.3-codex",
        "input": [
            {"type": "item_reference", "id": "rs_not_in_cache"},
            {"role": "user", "content": [{"type": "input_text", "text": "fresh"}]},
        ],
        "stream": True,
    }
    resp = await async_client.post("/v1/responses", json=payload)
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "not_found"
    assert "store" in body["error"]["message"]
    assert seen_inputs == []


@pytest.mark.asyncio
async def test_v1_responses_returns_not_found_for_unresolved_previous_response_id(async_client, monkeypatch):
    cache = get_response_context_cache()
    cache.clear()

    email = "ctx-prev-unresolved@example.com"
    raw_account_id = "acc_ctx_prev_unresolved"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield (
            'data: {"type":"response.completed","response":{"id":"rs_ctx_unknown","usage":'
            '{"input_tokens":1,"output_tokens":1,"total_tokens":2}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.3-codex",
        "previous_response_id": "rs_missing_previous",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "fresh"}]}],
        "stream": True,
    }
    resp = await async_client.post("/v1/responses", json=payload)
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "not_found"
    assert "rs_missing_previous" in body["error"]["message"]

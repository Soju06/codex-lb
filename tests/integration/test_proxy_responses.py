from __future__ import annotations

import base64
import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import app.modules.proxy.service as proxy_module
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
async def test_v1_responses_rewrites(async_client):
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
async def test_v1_responses_rewrites_under_root_path(app_instance):
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

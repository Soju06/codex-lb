from __future__ import annotations

import base64
import json

import httpx
import openai
import pytest
from httpx import ASGITransport

import app.modules.proxy.service as proxy_module

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
async def test_openai_client_responses_create(app_instance, async_client, monkeypatch):
    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_1","object":"response",'
            '"status":"completed","output":[],"usage":{"input_tokens":1,"output_tokens":2,"total_tokens":3}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    auth_json = _make_auth_json("acc_openai_resp", "openai-resp@example.com")
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    transport = ASGITransport(app=app_instance)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver/v1") as http_client:
        client = openai.AsyncOpenAI(api_key="test", base_url="http://testserver/v1", http_client=http_client)
        result = await client.responses.create(model="gpt-5.1", input="hi")

    assert result.id == "resp_1"
    assert result.object == "response"


@pytest.mark.asyncio
async def test_openai_client_chat_completions_create(app_instance, async_client, monkeypatch):
    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        yield 'data: {"type":"response.output_text.delta","delta":"hi"}\n\n'
        yield 'data: {"type":"response.completed","response":{"id":"resp_2"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    auth_json = _make_auth_json("acc_openai_chat", "openai-chat@example.com")
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    transport = ASGITransport(app=app_instance)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver/v1") as http_client:
        client = openai.AsyncOpenAI(api_key="test", base_url="http://testserver/v1", http_client=http_client)
        result = await client.chat.completions.create(
            model="gpt-5.2",
            messages=[{"role": "user", "content": "hi"}],
        )

    assert result.id == "resp_2"
    assert result.object == "chat.completion"

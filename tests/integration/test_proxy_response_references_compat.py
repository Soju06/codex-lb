from __future__ import annotations

import base64
import json

import pytest

import app.modules.proxy.service as proxy_module
from app.modules.proxy.response_context_cache import get_response_context_cache

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


async def _import_account(async_client, account_id: str, email: str) -> None:
    auth_json = _make_auth_json(account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_v1_responses_expands_item_reference_from_local_context(async_client, monkeypatch):
    cache = get_response_context_cache()
    await cache.reset()
    await _import_account(async_client, "acc_ref", "ref@example.com")
    monkeypatch.setattr(proxy_module, "_response_context_scope", lambda actor_log, headers=None: "scope:test")

    seen_inputs: list[object] = []
    turn = {"count": 0}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        turn["count"] += 1
        seen_inputs.append(json.loads(json.dumps(payload.input)))
        if turn["count"] == 1:
            yield (
                'data: {"type":"response.completed","response":{"id":"resp_local_1","status":"completed",'
                '"output":[{"id":"rs_local_1","type":"message","role":"assistant",'
                '"content":[{"type":"output_text","text":"first answer"}]}]}}\n\n'
            )
            return
        yield 'data: {"type":"response.completed","response":{"id":"rs_local_2","status":"completed"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    first = await async_client.post(
        "/v1/responses",
        json={"model": "gpt-5.1", "input": "first question"},
        headers={"x-openai-client-id": "openclaw-test", "Authorization": "Bearer compat-token-1"},
    )
    assert first.status_code == 200

    second = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "item_reference", "id": "rs_local_1"},
                        {"type": "input_text", "text": "follow up"},
                    ],
                },
            ],
        },
        headers={"x-openai-client-id": "openclaw-test", "Authorization": "Bearer compat-token-1"},
    )
    assert second.status_code == 200

    assert len(seen_inputs) == 2
    second_input = seen_inputs[1]
    assert isinstance(second_input, list)
    serialized_second_input = json.dumps(second_input)
    assert '"item_reference"' not in serialized_second_input
    assert "first answer" in serialized_second_input

    await cache.reset()

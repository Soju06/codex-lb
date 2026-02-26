from __future__ import annotations

import base64
import hashlib
import json

import pytest

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
async def test_model_override_for_app_forces_model_and_logs_actor(async_client, monkeypatch):
    auth_json = _make_auth_json("acc_override_app", "override-app@example.com")
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    create_rule = await async_client.post(
        "/api/model-overrides",
        json={
            "matchType": "app",
            "matchValue": "openclaw",
            "forcedModel": "gpt-5.3-codex",
            "forcedReasoningEffort": "high",
            "enabled": True,
            "note": "force migration from 5.1",
        },
    )
    assert create_rule.status_code == 200
    override_id = create_rule.json()["id"]

    observed: dict[str, str | None] = {"model": None, "effort": None}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        observed["model"] = payload.model
        observed["effort"] = payload.reasoning.effort if payload.reasoning else None
        yield 'data: {"type":"response.output_text.delta","delta":"ok"}\n\n'
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_1","usage":'
            '{"input_tokens":3,"output_tokens":4,"output_tokens_details":{"reasoning_tokens":1}}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    raw_token = "token-openclaw-123"
    resp = await async_client.post(
        "/v1/chat/completions",
        headers={
            "X-App-Id": "openclaw",
            "Authorization": f"Bearer {raw_token}",
        },
        json={
            "model": "gpt-5.1-codex",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert resp.status_code == 200
    assert observed["model"] == "gpt-5.3-codex"
    assert observed["effort"] == "high"

    logs_resp = await async_client.get("/api/request-logs", params={"limit": 1})
    assert logs_resp.status_code == 200
    log = logs_resp.json()["requests"][0]
    assert log["requestedModel"] == "gpt-5.1-codex"
    assert log["model"] == "gpt-5.3-codex"
    assert log["overrideId"] == override_id
    assert log["clientApp"] == "openclaw"
    expected_key = f"hash:{hashlib.sha256(raw_token.encode('utf-8')).hexdigest()[:16]}"
    assert log["apiKey"] == expected_key



@pytest.mark.asyncio
async def test_global_model_force_routes_all_requests(async_client, monkeypatch):
    auth_json = _make_auth_json("acc_force_all", "force-all@example.com")
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    settings_resp = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "routingStrategy": "usage_weighted",
            "globalModelForceEnabled": True,
            "globalModelForceModel": "gpt-5.3-codex",
            "globalModelForceReasoningEffort": "normal",
            "importWithoutOverwrite": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": False,
        },
    )
    assert settings_resp.status_code == 200

    observed: dict[str, str | None] = {"model": None, "effort": None}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        observed["model"] = payload.model
        observed["effort"] = payload.reasoning.effort if payload.reasoning else None
        yield 'data: {"type":"response.output_text.delta","delta":"ok"}\n\n'
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_1","usage":'
            '{"input_tokens":3,"output_tokens":4,"output_tokens_details":{"reasoning_tokens":1}}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    resp = await async_client.post(
        "/v1/chat/completions",
        headers={
            "X-App-Id": "openclaw",
            "Authorization": "Bearer token-force-all-123",
        },
        json={
            "model": "gpt-5.1-codex",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )
    assert resp.status_code == 200
    assert observed["model"] == "gpt-5.3-codex"
    assert observed["effort"] == "medium"

    logs_resp = await async_client.get("/api/request-logs", params={"limit": 1})
    assert logs_resp.status_code == 200
    log = logs_resp.json()["requests"][0]
    assert log["requestedModel"] == "gpt-5.1-codex"
    assert log["model"] == "gpt-5.3-codex"
    assert log["overrideId"] is None

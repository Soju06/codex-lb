from __future__ import annotations

import base64
import json

import pytest

import app.modules.proxy.service as proxy_module
from app.core.clients.proxy import ProxyResponseError
from app.core.openai.models import OpenAIResponsePayload
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.usage.repository import UsageRepository

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
async def test_proxy_compact_no_accounts(async_client):
    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "no_accounts"


@pytest.mark.asyncio
async def test_proxy_compact_success(async_client, monkeypatch):
    auth_json = _make_auth_json("acc_compact", "compact@example.com")
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    seen = {}

    async def fake_compact(payload, headers, access_token, account_id):
        seen["access_token"] = access_token
        seen["account_id"] = account_id
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id="acc_compact",
            used_percent=25.0,
            window="primary",
            reset_at=1735689600,
            recorded_at=utcnow(),
            credits_has=True,
            credits_unlimited=False,
            credits_balance=12.5,
        )

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 200
    assert response.json()["output"] == []
    assert seen["access_token"] == "access-token"
    assert seen["account_id"] == "acc_compact"
    assert response.headers.get("x-codex-primary-used-percent") == "25.0"
    assert response.headers.get("x-codex-primary-window-minutes") == "300"
    assert response.headers.get("x-codex-primary-reset-at") == "1735689600"
    assert response.headers.get("x-codex-credits-has-credits") == "true"
    assert response.headers.get("x-codex-credits-unlimited") == "false"
    assert response.headers.get("x-codex-credits-balance") == "12.50"


@pytest.mark.asyncio
async def test_proxy_compact_usage_limit_marks_account(async_client, monkeypatch):
    auth_json = _make_auth_json("acc_limit", "limit@example.com")
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fake_compact(payload, headers, access_token, account_id):
        raise ProxyResponseError(
            429,
            {
                "error": {
                    "type": "usage_limit_reached",
                    "message": "limit reached",
                    "plan_type": "plus",
                    "resets_at": 1767612327,
                }
            },
        )

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)
    assert response.status_code == 429
    error = response.json()["error"]
    assert error["type"] == "usage_limit_reached"

    async with SessionLocal() as session:
        account = await session.get(Account, "acc_limit")
        assert account is not None
        assert account.status == AccountStatus.RATE_LIMITED

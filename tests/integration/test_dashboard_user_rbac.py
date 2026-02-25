from __future__ import annotations

import base64
import json

import pytest

import app.modules.proxy.service as proxy_module
from app.core.auth import generate_unique_account_id
from tests.support.auth import BOOTSTRAP_ADMIN_PASSWORD

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


async def _login(async_client, *, username: str, password: str) -> dict:
    response = await async_client.post(
        "/api/dashboard-auth/password/login",
        json={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()


async def _import_account(async_client, account_id: str, email: str) -> str:
    auth_json = _make_auth_json(account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    return generate_unique_account_id(account_id, email)


@pytest.mark.asyncio
async def test_default_admin_login_returns_principal(async_client):
    payload = await _login(async_client, username="admin", password=BOOTSTRAP_ADMIN_PASSWORD)
    assert payload["authenticated"] is True
    assert payload["user"]["username"] == "admin"
    assert payload["user"]["role"] == "admin"


@pytest.mark.asyncio
async def test_rbac_user_sees_only_owned_accounts(async_client):
    await _login(async_client, username="admin", password=BOOTSTRAP_ADMIN_PASSWORD)
    create_user = await async_client.post(
        "/api/users",
        json={"username": "alice", "password": "alice123Q!", "role": "user"},
    )
    assert create_user.status_code == 200
    user_id = create_user.json()["id"]

    admin_account_id = await _import_account(async_client, "acc_admin_owned", "admin-owned@example.com")

    logout = await async_client.post("/api/dashboard-auth/logout", json={})
    assert logout.status_code == 200
    await _login(async_client, username="alice", password="alice123Q!")
    user_account_id = await _import_account(async_client, "acc_alice_owned", "alice-owned@example.com")

    own_accounts = await async_client.get("/api/accounts")
    assert own_accounts.status_code == 200
    own_account_ids = {row["accountId"] for row in own_accounts.json()["accounts"]}
    assert own_account_ids == {user_account_id}

    cannot_manage_admin = await async_client.post(f"/api/accounts/{admin_account_id}/pause")
    assert cannot_manage_admin.status_code == 404

    logout_user = await async_client.post("/api/dashboard-auth/logout", json={})
    assert logout_user.status_code == 200
    await _login(async_client, username="admin", password=BOOTSTRAP_ADMIN_PASSWORD)
    filtered_accounts = await async_client.get(f"/api/accounts?ownerUserId={user_id}")
    assert filtered_accounts.status_code == 200
    filtered_ids = {row["accountId"] for row in filtered_accounts.json()["accounts"]}
    assert filtered_ids == {user_account_id}


@pytest.mark.asyncio
async def test_backend_api_codex_requires_api_key_even_when_disabled(async_client):
    await _login(async_client, username="admin", password=BOOTSTRAP_ADMIN_PASSWORD)
    update = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": False,
        },
    )
    assert update.status_code == 200

    missing_key = await async_client.post(
        "/backend-api/codex/responses",
        json={"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True},
    )
    assert missing_key.status_code == 401
    assert missing_key.json()["error"]["code"] == "invalid_api_key"

    v1_allowed = await async_client.get("/v1/models")
    assert v1_allowed.status_code == 200


@pytest.mark.asyncio
async def test_api_key_uses_only_owner_accounts(async_client, monkeypatch):
    await _login(async_client, username="admin", password=BOOTSTRAP_ADMIN_PASSWORD)
    create_user = await async_client.post(
        "/api/users",
        json={"username": "bob", "password": "bob123Q!", "role": "user"},
    )
    assert create_user.status_code == 200

    admin_account_raw_id = "acc_admin_scope"
    await _import_account(async_client, admin_account_raw_id, "admin-scope@example.com")

    logout_admin = await async_client.post("/api/dashboard-auth/logout", json={})
    assert logout_admin.status_code == 200
    await _login(async_client, username="bob", password="bob123Q!")

    user_account_raw_id = "acc_bob_scope"
    await _import_account(async_client, user_account_raw_id, "bob-scope@example.com")

    key_response = await async_client.post("/api/api-keys/", json={"name": "bob-key"})
    assert key_response.status_code == 200
    api_key = key_response.json()["key"]

    seen: dict[str, str] = {}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen["account_id"] = account_id
        event = {
            "type": "response.completed",
            "response": {"id": "resp_owner_scope", "usage": {"input_tokens": 1, "output_tokens": 1}},
        }
        yield f"data: {json.dumps(event)}\n\n"

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True},
    ) as response:
        assert response.status_code == 200
        _ = [line async for line in response.aiter_lines() if line]

    assert seen["account_id"] == user_account_raw_id

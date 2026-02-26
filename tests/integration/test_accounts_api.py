from __future__ import annotations

import base64
import json

import pytest

from app.core.auth import generate_unique_account_id
from app.core.config.settings import get_settings

pytestmark = pytest.mark.integration


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


@pytest.mark.asyncio
async def test_import_and_list_accounts(async_client):
    email = "tester@example.com"
    raw_account_id = "acc_explicit"
    payload = {
        "email": email,
        "chatgpt_account_id": "acc_payload",
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    auth_json = {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access",
            "refreshToken": "refresh",
            "accountId": raw_account_id,
        },
    }

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    data = response.json()
    assert data["accountId"] == expected_account_id
    assert data["email"] == email
    assert data["planType"] == "plus"

    list_response = await async_client.get("/api/accounts")
    assert list_response.status_code == 200
    accounts = list_response.json()["accounts"]
    assert any(account["accountId"] == expected_account_id for account in accounts)


@pytest.mark.asyncio
async def test_reactivate_missing_account_returns_404(async_client):
    response = await async_client.post("/api/accounts/missing/reactivate")
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["code"] == "account_not_found"


@pytest.mark.asyncio
async def test_import_anthropic_credentials_uses_email_display_name(async_client):
    settings = get_settings()
    credentials = {
        "claudeAiOauth": {
            "accessToken": "sk-ant-oat-123",
            "refreshToken": "refresh-123",
            "expiresAt": 1_893_456_789_000,
        }
    }

    files = {
        "credentials_json": ("claude-credentials.json", json.dumps(credentials), "application/json"),
        "email": (None, "tester@example.com"),
    }
    response = await async_client.post("/api/accounts/import-anthropic", files=files)
    assert response.status_code == 200
    data = response.json()
    assert data["accountId"] == settings.anthropic_default_account_id
    assert data["email"] == "tester@example.com"

    list_response = await async_client.get("/api/accounts")
    assert list_response.status_code == 200
    accounts = list_response.json()["accounts"]
    imported = next(
        (account for account in accounts if account["accountId"] == settings.anthropic_default_account_id),
        None,
    )
    assert imported is not None
    assert imported["email"] == "tester@example.com"
    assert imported["displayName"] == "tester@example.com"


@pytest.mark.asyncio
async def test_import_anthropic_credentials_requires_email(async_client):
    credentials = {
        "claudeAiOauth": {
            "accessToken": "sk-ant-oat-123",
            "refreshToken": "refresh-123",
            "expiresAt": 1_893_456_789_000,
        }
    }
    files = {"credentials_json": ("claude-credentials.json", json.dumps(credentials), "application/json")}
    response = await async_client.post("/api/accounts/import-anthropic", files=files)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_pause_missing_account_returns_404(async_client):
    response = await async_client.post("/api/accounts/missing/pause")
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["code"] == "account_not_found"


@pytest.mark.asyncio
async def test_pause_account(async_client):
    email = "pause@example.com"
    raw_account_id = "acc_pause"
    payload = {
        "email": email,
        "chatgpt_account_id": raw_account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    auth_json = {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access",
            "refreshToken": "refresh",
            "accountId": raw_account_id,
        },
    }

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    pause = await async_client.post(f"/api/accounts/{expected_account_id}/pause")
    assert pause.status_code == 200
    assert pause.json()["status"] == "paused"

    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    data = accounts.json()["accounts"]
    matched = next((account for account in data if account["accountId"] == expected_account_id), None)
    assert matched is not None
    assert matched["status"] == "paused"


@pytest.mark.asyncio
async def test_delete_missing_account_returns_404(async_client):
    response = await async_client.delete("/api/accounts/missing")
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["code"] == "account_not_found"

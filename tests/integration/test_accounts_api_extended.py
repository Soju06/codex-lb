from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import pytest

from app.core.auth import fallback_account_id, generate_unique_account_id
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _make_auth_json(account_id: str | None, email: str, plan_type: str = "plus") -> dict:
    payload = {
        "email": email,
        "https://api.openai.com/auth": {"chatgpt_plan_type": plan_type},
    }
    if account_id:
        payload["chatgpt_account_id"] = account_id
    tokens: dict[str, object] = {
        "idToken": _encode_jwt(payload),
        "accessToken": "access",
        "refreshToken": "refresh",
    }
    if account_id:
        tokens["accountId"] = account_id
    return {"tokens": tokens}


def _make_account(account_id: str, email: str, plan_type: str = "plus") -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email,
        plan_type=plan_type,
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


def _iso_utc(epoch_seconds: int) -> str:
    return (
        datetime.fromtimestamp(epoch_seconds, tz=timezone.utc)
        .isoformat()
        .replace(
            "+00:00",
            "Z",
        )
    )


@pytest.mark.asyncio
async def test_import_invalid_json_returns_400(async_client):
    files = {"auth_json": ("auth.json", "not-json", "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "invalid_auth_json"


@pytest.mark.asyncio
async def test_import_missing_tokens_returns_400(async_client):
    files = {"auth_json": ("auth.json", json.dumps({"foo": "bar"}), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "invalid_auth_json"


@pytest.mark.asyncio
async def test_import_falls_back_to_email_based_account_id(async_client):
    email = "fallback@example.com"
    auth_json = _make_auth_json(None, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    payload = response.json()
    assert payload["accountId"] == fallback_account_id(email)
    assert payload["email"] == email


@pytest.mark.asyncio
async def test_import_overwrites_by_default_for_same_account_identity(async_client):
    email = "same-default@example.com"
    raw_account_id = "acc_same_default"

    files_one = {
        "auth_json": (
            "auth.json",
            json.dumps(_make_auth_json(raw_account_id, email, "plus")),
            "application/json",
        )
    }
    first = await async_client.post("/api/accounts/import", files=files_one)
    assert first.status_code == 200

    files_two = {
        "auth_json": (
            "auth.json",
            json.dumps(_make_auth_json(raw_account_id, email, "team")),
            "application/json",
        )
    }
    second = await async_client.post("/api/accounts/import", files=files_two)
    assert second.status_code == 200

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    assert first.json()["accountId"] == expected_account_id
    assert second.json()["accountId"] == expected_account_id
    assert second.json()["planType"] == "team"

    accounts_response = await async_client.get("/api/accounts")
    assert accounts_response.status_code == 200
    accounts = [entry for entry in accounts_response.json()["accounts"] if entry["email"] == email]
    assert len(accounts) == 1
    assert accounts[0]["accountId"] == expected_account_id
    assert accounts[0]["planType"] == "team"


@pytest.mark.asyncio
async def test_import_without_overwrite_keeps_same_account_identity_separate(async_client):
    settings = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "importWithoutOverwrite": True,
            "totpRequiredOnLogin": False,
        },
    )
    assert settings.status_code == 200
    assert settings.json()["importWithoutOverwrite"] is True

    email = "same-separate@example.com"
    raw_account_id = "acc_same_separate"

    files_one = {
        "auth_json": (
            "auth.json",
            json.dumps(_make_auth_json(raw_account_id, email, "plus")),
            "application/json",
        )
    }
    first = await async_client.post("/api/accounts/import", files=files_one)
    assert first.status_code == 200

    files_two = {
        "auth_json": (
            "auth.json",
            json.dumps(_make_auth_json(raw_account_id, email, "team")),
            "application/json",
        )
    }
    second = await async_client.post("/api/accounts/import", files=files_two)
    assert second.status_code == 200

    base_account_id = generate_unique_account_id(raw_account_id, email)
    first_id = first.json()["accountId"]
    second_id = second.json()["accountId"]
    assert first_id == base_account_id
    assert second_id != first_id
    assert second_id.startswith(f"{base_account_id}__copy")

    accounts_response = await async_client.get("/api/accounts")
    assert accounts_response.status_code == 200
    accounts = [entry for entry in accounts_response.json()["accounts"] if entry["email"] == email]
    assert len(accounts) == 2
    ids = {entry["accountId"] for entry in accounts}
    assert ids == {first_id, second_id}


@pytest.mark.asyncio
async def test_delete_account_removes_from_list(async_client):
    email = "delete@example.com"
    raw_account_id = "acc_delete"
    auth_json = _make_auth_json(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    actual_account_id = generate_unique_account_id(raw_account_id, email)
    delete = await async_client.delete(f"/api/accounts/{actual_account_id}")
    assert delete.status_code == 200
    assert delete.json()["status"] == "deleted"

    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    data = accounts.json()["accounts"]
    assert all(account["accountId"] != actual_account_id for account in data)


@pytest.mark.asyncio
async def test_accounts_list_includes_per_account_reset_times(async_client, db_setup):
    primary_a = 1735689600
    primary_b = 1735693200
    secondary_a = 1736294400
    secondary_b = 1736380800

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_reset_a", "a@example.com"))
        await accounts_repo.upsert(_make_account("acc_reset_b", "b@example.com"))

        await usage_repo.add_entry(
            "acc_reset_a",
            10.0,
            window="primary",
            reset_at=primary_a,
        )
        await usage_repo.add_entry(
            "acc_reset_b",
            20.0,
            window="primary",
            reset_at=primary_b,
        )
        await usage_repo.add_entry(
            "acc_reset_a",
            30.0,
            window="secondary",
            reset_at=secondary_a,
        )
        await usage_repo.add_entry(
            "acc_reset_b",
            40.0,
            window="secondary",
            reset_at=secondary_b,
        )

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}

    assert accounts["acc_reset_a"]["resetAtPrimary"] == _iso_utc(primary_a)
    assert accounts["acc_reset_b"]["resetAtPrimary"] == _iso_utc(primary_b)
    assert accounts["acc_reset_a"]["resetAtSecondary"] == _iso_utc(secondary_a)
    assert accounts["acc_reset_b"]["resetAtSecondary"] == _iso_utc(secondary_b)

from __future__ import annotations

import base64
import json

import pytest

from app.core.auth import DEFAULT_EMAIL, generate_unique_account_id, parse_auth_json

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
async def test_update_account_limit_warmup_opt_in(async_client):
    email = "warmup@example.com"
    raw_account_id = "acc_warmup"
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

    update = await async_client.put(f"/api/accounts/{expected_account_id}/limit-warmup", json={"enabled": True})
    assert update.status_code == 200
    assert update.json() == {"status": "enabled", "enabled": True}

    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    data = accounts.json()["accounts"]
    matched = next((account for account in data if account["accountId"] == expected_account_id), None)
    assert matched is not None
    assert matched["limitWarmupEnabled"] is True
    assert matched["limitWarmup"] is None


@pytest.mark.asyncio
async def test_export_account_returns_latest_codex_auth_json_with_no_store_headers(async_client):
    email = "export@example.com"
    raw_account_id = "acc_export"
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

    export = await async_client.post(f"/api/accounts/{expected_account_id}/export")
    assert export.status_code == 200
    assert export.headers["cache-control"] == "no-store, no-cache, must-revalidate, private"
    assert export.headers["pragma"] == "no-cache"
    assert export.headers["expires"] == "0"

    payload = export.json()
    assert payload["accountId"] == expected_account_id
    assert payload["email"] == email
    assert payload["planType"] == "plus"
    assert payload["status"] == "active"

    parsed_auth = parse_auth_json(payload["authJson"].encode("utf-8"))
    assert parsed_auth.tokens.access_token == "access"
    assert parsed_auth.tokens.refresh_token == "refresh"
    assert parsed_auth.tokens.account_id == raw_account_id
    assert parsed_auth.last_refresh_at is not None


@pytest.mark.asyncio
async def test_export_missing_account_returns_404(async_client):
    response = await async_client.post("/api/accounts/missing/export")
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["code"] == "account_not_found"


@pytest.mark.asyncio
async def test_delete_missing_account_returns_404(async_client):
    response = await async_client.delete("/api/accounts/missing")
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["code"] == "account_not_found"


@pytest.mark.asyncio
async def test_set_alias_missing_account_returns_404(async_client):
    response = await async_client.put("/api/accounts/missing/alias", json={"alias": "Personal Plus"})
    assert response.status_code == 404
    payload = response.json()
    assert payload["error"]["code"] == "account_not_found"


@pytest.mark.asyncio
async def test_set_and_clear_account_alias(async_client):
    email = "alias@example.com"
    raw_account_id = "acc_alias"
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

    # Default summary uses the email since no alias is set yet.
    listing = await async_client.get("/api/accounts")
    matched = next(a for a in listing.json()["accounts"] if a["accountId"] == expected_account_id)
    assert matched["alias"] is None
    assert matched["displayName"] == email

    # Setting an alias updates both `alias` and `displayName`.
    set_response = await async_client.put(
        f"/api/accounts/{expected_account_id}/alias",
        json={"alias": "  Personal Plus  "},
    )
    assert set_response.status_code == 200
    body = set_response.json()
    assert body["alias"] == "Personal Plus"  # whitespace-trimmed
    listing = await async_client.get("/api/accounts")
    matched = next(a for a in listing.json()["accounts"] if a["accountId"] == expected_account_id)
    assert matched["alias"] == "Personal Plus"
    assert matched["displayName"] == "Personal Plus"

    # Empty alias clears the value and the display name falls back to email.
    clear_response = await async_client.put(
        f"/api/accounts/{expected_account_id}/alias",
        json={"alias": "   "},
    )
    assert clear_response.status_code == 200
    assert clear_response.json()["alias"] is None
    listing = await async_client.get("/api/accounts")
    matched = next(a for a in listing.json()["accounts"] if a["accountId"] == expected_account_id)
    assert matched["alias"] is None
    assert matched["displayName"] == email


@pytest.mark.asyncio
async def test_list_accounts_flags_email_duplicates(async_client):
    """Pin codex-lb #787 (B): after a token-invalidation cascade, the
    re-add OAuth flow creates a second account row with the same email
    but a fresh accountId for the same ChatGPT account identity and workspace
    slot. /api/accounts surfaces that pair via isEmailDuplicate=true on both
    rows so the dashboard can flag the operator's "stale + fresh" pair without
    forcing them to group by email, ChatGPT identity, and workspace themselves.
    """
    from app.core.crypto import TokenEncryptor
    from app.core.utils.time import utcnow
    from app.db.models import Account, AccountStatus
    from app.db.session import SessionLocal
    from app.modules.accounts.repository import AccountsRepository

    encryptor = TokenEncryptor()

    def _account(account_id: str, email: str, chatgpt_id: str, workspace_id: str | None = None) -> Account:
        return Account(
            id=account_id,
            chatgpt_account_id=chatgpt_id,
            workspace_id=workspace_id,
            email=email,
            plan_type="plus",
            access_token_encrypted=encryptor.encrypt("access"),
            refresh_token_encrypted=encryptor.encrypt("refresh"),
            id_token_encrypted=encryptor.encrypt("id"),
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
            deactivation_reason=None,
        )

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_account("dup-stale", "dup@example.com", "chatgpt_same"), merge_by_email=False)
        await repo.upsert(_account("dup-fresh", "dup@example.com", "chatgpt_same"), merge_by_email=False)
        await repo.upsert(_account("workspace-a", "multi@example.com", "chatgpt_multi", "ws_a"), merge_by_email=False)
        await repo.upsert(_account("workspace-b", "multi@example.com", "chatgpt_multi", "ws_b"), merge_by_email=False)
        await repo.upsert(_account("workspace-other", "dup@example.com", "chatgpt_other"), merge_by_email=False)
        await repo.upsert(_account("solo", "solo@example.com", "chatgpt_solo"), merge_by_email=False)
        await repo.upsert(_account("placeholder-a", DEFAULT_EMAIL, "chatgpt_placeholder_a"), merge_by_email=False)
        await repo.upsert(_account("placeholder-b", DEFAULT_EMAIL, "chatgpt_placeholder_b"), merge_by_email=False)
        await repo.upsert(_account("blank-a", "   ", "chatgpt_blank"), merge_by_email=False)
        await repo.upsert(_account("blank-b", "   ", "chatgpt_blank"), merge_by_email=False)

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    accounts_by_id = {a["accountId"]: a for a in response.json()["accounts"]}

    assert accounts_by_id["dup-stale"]["isEmailDuplicate"] is True
    assert accounts_by_id["dup-fresh"]["isEmailDuplicate"] is True
    assert accounts_by_id["workspace-a"]["isEmailDuplicate"] is False
    assert accounts_by_id["workspace-b"]["isEmailDuplicate"] is False
    assert accounts_by_id["workspace-other"]["isEmailDuplicate"] is False
    assert accounts_by_id["solo"]["isEmailDuplicate"] is False
    assert accounts_by_id["placeholder-a"]["isEmailDuplicate"] is False
    assert accounts_by_id["placeholder-b"]["isEmailDuplicate"] is False
    assert accounts_by_id["blank-a"]["isEmailDuplicate"] is False
    assert accounts_by_id["blank-b"]["isEmailDuplicate"] is False

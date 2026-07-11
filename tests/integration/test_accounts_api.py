from __future__ import annotations

import base64
import json
from copy import copy
from datetime import datetime

import pytest

from app.core.auth import DEFAULT_EMAIL, generate_unique_account_id, parse_auth_json
from app.core.clients.rate_limit_reset_credits import RateLimitResetCreditsSnapshot, ResetCreditItem
from app.core.clients.usage import ConsumeRateLimitResetCreditResponse, UsageFetchError
from app.core.crypto import TokenEncryptor
from app.core.usage.models import UsagePayload
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.rate_limit_reset_credits.store import get_rate_limit_reset_credits_store

pytestmark = pytest.mark.integration


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _reset_credit_snapshot(credit_id: str) -> RateLimitResetCreditsSnapshot:
    credit = ResetCreditItem(
        id=credit_id,
        status="available",
        expires_at=datetime.fromisoformat("2026-07-12T00:00:00+00:00"),
    )
    return RateLimitResetCreditsSnapshot(
        available_count=1,
        nearest_expires_at=credit.expires_at,
        credits=[credit],
    )


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
async def test_account_usage_reset_credits_fetches_selected_account(async_client, monkeypatch):
    email = "reset-credits@example.com"
    raw_account_id = "acc_reset_credits"
    payload = {
        "email": email,
        "chatgpt_account_id": raw_account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    auth_json = {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-reset-credits",
            "refreshToken": "refresh-reset-credits",
            "accountId": raw_account_id,
        },
    }

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    captured: dict[str, str | None] = {}

    async def stub_fetch_usage(*, access_token: str, account_id: str | None, **_: object) -> UsagePayload:
        captured["access_token"] = access_token
        captured["account_id"] = account_id
        return UsagePayload.model_validate(
            {
                "rate_limit_reset_credits": {"available_count": 3},
            },
        )

    monkeypatch.setattr("app.modules.accounts.service.fetch_usage", stub_fetch_usage)

    credits = await async_client.get(f"/api/accounts/{expected_account_id}/usage-reset-credits")

    assert credits.status_code == 200
    assert credits.json() == {
        "accountId": expected_account_id,
        "rateLimitResetCredits": {"availableCount": 3},
    }
    assert captured == {
        "access_token": "access-reset-credits",
        "account_id": raw_account_id,
    }


@pytest.mark.asyncio
async def test_account_usage_reset_credits_defaults_missing_upstream_summary_to_zero(async_client, monkeypatch):
    email = "reset-credits-zero@example.com"
    raw_account_id = "acc_reset_credits_zero"
    payload = {
        "email": email,
        "chatgpt_account_id": raw_account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    auth_json = {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-reset-credits-zero",
            "refreshToken": "refresh-reset-credits-zero",
            "accountId": raw_account_id,
        },
    }

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def stub_fetch_usage(**_: object) -> UsagePayload:
        return UsagePayload.model_validate({})

    monkeypatch.setattr("app.modules.accounts.service.fetch_usage", stub_fetch_usage)

    credits = await async_client.get(f"/api/accounts/{expected_account_id}/usage-reset-credits")

    assert credits.status_code == 200
    assert credits.json()["rateLimitResetCredits"] == {"availableCount": 0}


@pytest.mark.asyncio
async def test_account_usage_reset_credits_rejects_paused_account(async_client, monkeypatch):
    email = "reset-credits-paused@example.com"
    raw_account_id = "acc_reset_credits_paused"
    payload = {
        "email": email,
        "chatgpt_account_id": raw_account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    auth_json = {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-reset-credits-paused",
            "refreshToken": "refresh-reset-credits-paused",
            "accountId": raw_account_id,
        },
    }

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    async def fail_fetch_usage(**_: object) -> UsagePayload:
        raise AssertionError("paused account should not fetch upstream reset credits")

    monkeypatch.setattr("app.modules.accounts.service.fetch_usage", fail_fetch_usage)

    pause_response = await async_client.post(f"/api/accounts/{expected_account_id}/pause")
    assert pause_response.status_code == 200

    credits = await async_client.get(f"/api/accounts/{expected_account_id}/usage-reset-credits")

    assert credits.status_code == 409
    assert credits.json()["error"]["code"] == "account_usage_reset_credits_unavailable"


@pytest.mark.asyncio
async def test_account_usage_reset_consume_consumes_credit_and_refreshes(async_client, monkeypatch):
    email = "reset-consume-dashboard@example.com"
    raw_account_id = "acc_reset_consume_dashboard"
    payload = {
        "email": email,
        "chatgpt_account_id": raw_account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    auth_json = {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-reset-consume-dashboard",
            "refreshToken": "refresh-reset-consume-dashboard",
            "accountId": raw_account_id,
        },
    }

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    consume_calls: list[dict[str, object]] = []

    async def stub_consume_rate_limit_reset_credit(**kwargs: object) -> ConsumeRateLimitResetCreditResponse:
        consume_calls.append(kwargs)
        return ConsumeRateLimitResetCreditResponse.model_validate({"code": "reset", "windows_reset": 2})

    refreshed_account_ids: list[str] = []

    class StubUsageUpdater:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def force_refresh(self, account: Account, *, ignore_refresh_disabled: bool = False) -> bool:
            refreshed_account_ids.append(f"{account.id}:{ignore_refresh_disabled}")
            return True

    monkeypatch.setattr(
        "app.modules.accounts.service.consume_rate_limit_reset_credit",
        stub_consume_rate_limit_reset_credit,
    )
    monkeypatch.setattr("app.modules.accounts.service.UsageUpdater", StubUsageUpdater)
    await get_rate_limit_reset_credits_store().set(expected_account_id, _reset_credit_snapshot("credit-dashboard"))

    reset = await async_client.post(f"/api/accounts/{expected_account_id}/usage-reset-credits/consume")

    assert reset.status_code == 200, reset.text
    assert reset.json()["code"] == "reset"
    assert reset.json()["windowsReset"] == 2
    assert reset.json()["usageWritten"] is True
    assert len(consume_calls) == 1
    call = consume_calls[0]
    assert call["access_token"] == "access-reset-consume-dashboard"
    assert call["account_id"] == raw_account_id
    assert isinstance(call["redeem_request_id"], str)
    assert call["redeem_request_id"]
    assert call["route"] is None
    assert call["allow_direct_egress"] is True
    assert refreshed_account_ids == [f"{expected_account_id}:True"]
    assert get_rate_limit_reset_credits_store().get(expected_account_id) is None


@pytest.mark.asyncio
async def test_account_usage_reset_consume_refreshes_usage_with_post_401_account(async_client, monkeypatch):
    email = "reset-consume-post-401-dashboard@example.com"
    raw_account_id = "acc_reset_consume_post_401_dashboard"
    payload = {
        "email": email,
        "chatgpt_account_id": raw_account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    auth_json = {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-reset-consume-stale-dashboard",
            "refreshToken": "refresh-reset-consume-post-401-dashboard",
            "accountId": raw_account_id,
        },
    }

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    encryptor = TokenEncryptor()
    consume_access_tokens: list[str] = []
    refreshed_access_tokens: list[str] = []

    async def stub_consume_rate_limit_reset_credit(**kwargs: object) -> ConsumeRateLimitResetCreditResponse:
        access_token = str(kwargs["access_token"])
        consume_access_tokens.append(access_token)
        if len(consume_access_tokens) == 1:
            raise UsageFetchError(401, "expired access token")
        return ConsumeRateLimitResetCreditResponse.model_validate({"code": "reset", "windows_reset": 1})

    class StubAuthManager:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def ensure_fresh(self, account: Account, *, force: bool = False) -> Account:
            if not force:
                return account
            refreshed = copy(account)
            refreshed.access_token_encrypted = encryptor.encrypt("access-reset-consume-refreshed-dashboard")
            return refreshed

    class StubUsageUpdater:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def force_refresh(self, account: Account, *, ignore_refresh_disabled: bool = False) -> bool:
            assert ignore_refresh_disabled is True
            refreshed_access_tokens.append(encryptor.decrypt(account.access_token_encrypted))
            return True

    monkeypatch.setattr("app.dependencies.AuthManager", StubAuthManager)
    monkeypatch.setattr(
        "app.modules.accounts.service.consume_rate_limit_reset_credit",
        stub_consume_rate_limit_reset_credit,
    )
    monkeypatch.setattr("app.modules.accounts.service.UsageUpdater", StubUsageUpdater)

    reset = await async_client.post(f"/api/accounts/{expected_account_id}/usage-reset-credits/consume")

    assert reset.status_code == 200, reset.text
    assert reset.json()["code"] == "reset"
    assert consume_access_tokens == [
        "access-reset-consume-stale-dashboard",
        "access-reset-consume-refreshed-dashboard",
    ]
    assert refreshed_access_tokens == ["access-reset-consume-refreshed-dashboard"]


@pytest.mark.asyncio
async def test_account_usage_reset_consume_forwards_redeem_request_id(async_client, monkeypatch):
    email = "reset-consume-retry-dashboard@example.com"
    raw_account_id = "acc_reset_consume_retry_dashboard"
    payload = {
        "email": email,
        "chatgpt_account_id": raw_account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    auth_json = {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-reset-consume-retry-dashboard",
            "refreshToken": "refresh-reset-consume-retry-dashboard",
            "accountId": raw_account_id,
        },
    }

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200

    consume_calls: list[dict[str, object]] = []

    async def stub_consume_rate_limit_reset_credit(**kwargs: object) -> ConsumeRateLimitResetCreditResponse:
        consume_calls.append(kwargs)
        return ConsumeRateLimitResetCreditResponse.model_validate({"code": "already_redeemed", "windows_reset": 0})

    monkeypatch.setattr(
        "app.modules.accounts.service.consume_rate_limit_reset_credit",
        stub_consume_rate_limit_reset_credit,
    )

    reset = await async_client.post(
        f"/api/accounts/{expected_account_id}/usage-reset-credits/consume",
        json={"redeemRequestId": " dashboard-retry-id "},
    )

    assert reset.status_code == 200, reset.text
    assert reset.json()["code"] == "already_redeemed"
    assert len(consume_calls) == 1
    assert consume_calls[0]["redeem_request_id"] == "dashboard-retry-id"


@pytest.mark.asyncio
async def test_account_usage_reset_consume_rejects_paused_account(async_client, monkeypatch):
    email = "reset-consume-paused-dashboard@example.com"
    raw_account_id = "acc_reset_consume_paused_dashboard"
    payload = {
        "email": email,
        "chatgpt_account_id": raw_account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    auth_json = {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-reset-consume-paused-dashboard",
            "refreshToken": "refresh-reset-consume-paused-dashboard",
            "accountId": raw_account_id,
        },
    }

    async def should_not_consume(**_: object) -> ConsumeRateLimitResetCreditResponse:
        raise AssertionError("paused account should not consume upstream reset credits")

    expected_account_id = generate_unique_account_id(raw_account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    pause_response = await async_client.post(f"/api/accounts/{expected_account_id}/pause")
    assert pause_response.status_code == 200

    monkeypatch.setattr(
        "app.modules.accounts.service.consume_rate_limit_reset_credit",
        should_not_consume,
    )

    reset = await async_client.post(f"/api/accounts/{expected_account_id}/usage-reset-credits/consume")

    assert reset.status_code == 409
    assert reset.json()["error"]["code"] == "account_usage_reset_consume_unavailable"


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
async def test_pause_reauth_required_account_returns_conflict(async_client):
    email = "pause-reauth@example.com"
    raw_account_id = "acc_pause_reauth"
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

    async with SessionLocal() as session:
        account = await session.get(Account, expected_account_id)
        assert account is not None
        account.status = AccountStatus.REAUTH_REQUIRED
        account.deactivation_reason = "Authentication token invalidated - re-login required"
        await session.commit()

    pause = await async_client.post(f"/api/accounts/{expected_account_id}/pause")
    assert pause.status_code == 409
    assert pause.json()["error"]["code"] == "account_state_transition_invalid"

    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    matched = next(
        (account for account in accounts.json()["accounts"] if account["accountId"] == expected_account_id),
        None,
    )
    assert matched is not None
    assert matched["status"] == "reauth_required"


@pytest.mark.asyncio
async def test_reactivate_reauth_required_account_returns_conflict(async_client):
    email = "reactivate-reauth@example.com"
    raw_account_id = "acc_reactivate_reauth"
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

    async with SessionLocal() as session:
        account = await session.get(Account, expected_account_id)
        assert account is not None
        account.status = AccountStatus.REAUTH_REQUIRED
        account.deactivation_reason = "Authentication token invalidated - re-login required"
        await session.commit()

    reactivate = await async_client.post(f"/api/accounts/{expected_account_id}/reactivate")
    assert reactivate.status_code == 409
    assert reactivate.json()["error"]["code"] == "account_state_transition_invalid"

    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    matched = next(
        (account for account in accounts.json()["accounts"] if account["accountId"] == expected_account_id),
        None,
    )
    assert matched is not None
    assert matched["status"] == "reauth_required"


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

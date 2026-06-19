from __future__ import annotations

import base64
import json

import pytest

from app.core.auth import generate_unique_account_id
from app.core.auth.refresh import RefreshError
from app.core.clients.rate_limit_reset import ConsumeRateLimitResetCode, ConsumeRateLimitResetResponse
from app.db.session import SessionLocal
from app.modules.accounts.service import AccountsService
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


async def _import_test_account(async_client, *, email: str, account_id: str) -> str:
    payload = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "pro"},
    }
    auth_json = {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-token-not-a-real-secret",
            "refreshToken": "refresh",
            "accountId": account_id,
        },
    }
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200, response.text
    return generate_unique_account_id(account_id, email)


async def _seed_reset_credit(account_id: str, *, count: int | None) -> None:
    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id,
            100.0,
            window="primary",
            reset_at=1_735_689_600,
            window_minutes=300,
            rate_limit_reset_available_count=count,
        )


@pytest.mark.asyncio
async def test_usage_reset_missing_account_returns_404(async_client):
    response = await async_client.post("/api/accounts/missing/usage-reset/apply")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "account_not_found"


@pytest.mark.asyncio
async def test_usage_reset_paused_account_returns_409(async_client, monkeypatch):
    async def _fake_consume(self, **kwargs):  # noqa: ARG001
        raise AssertionError("paused account should not invoke upstream consume")

    monkeypatch.setattr(AccountsService, "_send_usage_reset_consume", _fake_consume)

    account_id = await _import_test_account(
        async_client,
        email="reset-paused@example.com",
        account_id="acc_reset_paused",
    )
    await _seed_reset_credit(account_id, count=1)
    pause_resp = await async_client.post(f"/api/accounts/{account_id}/pause")
    assert pause_resp.status_code == 200

    response = await async_client.post(f"/api/accounts/{account_id}/usage-reset/apply")
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "account_not_reset_applicable"


@pytest.mark.asyncio
async def test_usage_reset_no_credit_returns_409(async_client, monkeypatch):
    async def _should_not_consume(self, **kwargs):  # noqa: ARG001
        raise AssertionError("no-credit account should not invoke upstream consume")

    monkeypatch.setattr(AccountsService, "_send_usage_reset_consume", _should_not_consume)

    account_id = await _import_test_account(
        async_client,
        email="reset-no-credit@example.com",
        account_id="acc_reset_no_credit",
    )
    await _seed_reset_credit(account_id, count=0)

    response = await async_client.post(f"/api/accounts/{account_id}/usage-reset/apply")
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "account_usage_reset_no_credit"


@pytest.mark.asyncio
async def test_usage_reset_refresh_failure_returns_structured_409(async_client, monkeypatch):
    async def _fail_reset(self, account_id):  # noqa: ARG001
        raise RefreshError(
            code="invalid_grant",
            message="refresh token revoked",
            is_permanent=True,
        )

    monkeypatch.setattr(AccountsService, "apply_usage_reset", _fail_reset)

    response = await async_client.post("/api/accounts/acc_refresh_failed/usage-reset/apply")

    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "account_usage_reset_refresh_failed"
    assert "refresh token revoked" in body["error"]["message"]


@pytest.mark.asyncio
async def test_usage_reset_active_account_returns_snapshot(async_client, monkeypatch):
    captured: dict = {}

    async def _fake_consume(self, *, access_token, chatgpt_account_id, account):  # noqa: ARG001
        captured["chatgpt_account_id"] = chatgpt_account_id
        captured["had_token"] = bool(access_token)
        return ConsumeRateLimitResetResponse(code=ConsumeRateLimitResetCode.RESET, windows_reset=2)

    monkeypatch.setattr(AccountsService, "_send_usage_reset_consume", _fake_consume)

    account_id = await _import_test_account(
        async_client,
        email="reset-active@example.com",
        account_id="acc_reset_active",
    )
    await _seed_reset_credit(account_id, count=1)

    response = await async_client.post(f"/api/accounts/{account_id}/usage-reset/apply")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "applied"
    assert body["accountId"] == account_id
    assert body["consumeCode"] == "reset"
    assert body["windowsReset"] == 2
    assert body["rateLimitResetAvailableCountBefore"] == 1
    assert body["accountStatusBefore"] == "active"
    assert body["accountStatusAfter"] == "active"

    assert captured["chatgpt_account_id"] == "acc_reset_active"
    assert captured["had_token"] is True
from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock

import pytest

from app.core.auth import generate_unique_account_id
from app.core.clients import rate_limit_resets
from app.core.clients.rate_limit_resets import (
    ConsumeResetCreditPayload,
    ResetCreditDetails,
    ResetCreditsError,
    ResetCreditsPayload,
)
from app.modules.accounts import reset_credit_cache
from app.modules.accounts import service as service_module

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _redemption_inventory(monkeypatch):
    payload = ResetCreditsPayload(
        credits=[
            ResetCreditDetails(
                id="credit-1",
                reset_type="weekly",
                status="available",
                granted_at="2026-07-01T00:00:00Z",
            )
        ],
        available_count=1,
    )
    monkeypatch.setattr(rate_limit_resets, "fetch_reset_credits", AsyncMock(return_value=payload))
    monkeypatch.setattr(service_module, "refresh_standard_capacity", AsyncMock(return_value=True))


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


@pytest.mark.asyncio
async def test_list_reset_credits_missing_account_returns_404(async_client):
    response = await async_client.get("/api/accounts/missing/rate-limit-reset-credits")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "account_not_found"


@pytest.mark.asyncio
async def test_consume_reset_credit_missing_account_returns_404(async_client, monkeypatch):
    async def _fake_consume(**kwargs):  # noqa: ARG001 - must not be reached
        raise AssertionError("missing account must not reach upstream consume")

    monkeypatch.setattr(rate_limit_resets, "consume_reset_credit", _fake_consume)

    response = await async_client.post("/api/accounts/missing/rate-limit-reset-credits/consume")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "account_not_found"


@pytest.mark.asyncio
async def test_list_reset_credits_paused_account_returns_409(async_client, monkeypatch):
    async def _fake_fetch(**kwargs):  # noqa: ARG001 - must not be reached
        raise AssertionError("paused account must not reach upstream")

    monkeypatch.setattr(rate_limit_resets, "fetch_reset_credits", _fake_fetch)

    account_id = await _import_test_account(
        async_client,
        email="reset-paused@example.com",
        account_id="acc_reset_paused",
    )
    pause_resp = await async_client.post(f"/api/accounts/{account_id}/pause")
    assert pause_resp.status_code == 200

    response = await async_client.get(f"/api/accounts/{account_id}/rate-limit-reset-credits")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "account_reset_credits_unavailable"


@pytest.mark.asyncio
async def test_list_reset_credits_returns_upstream_payload(async_client, monkeypatch):
    async def _fake_fetch(**kwargs):  # noqa: ARG001
        return ResetCreditsPayload(
            credits=[
                ResetCreditDetails(
                    id="credit-1",
                    reset_type="weekly",
                    status="available",
                    granted_at="2026-07-01T00:00:00Z",
                    expires_at="2026-07-31T00:00:00Z",
                )
            ],
            available_count=1,
        )

    monkeypatch.setattr(rate_limit_resets, "fetch_reset_credits", _fake_fetch)

    account_id = await _import_test_account(
        async_client,
        email="reset-list@example.com",
        account_id="acc_reset_list",
    )
    response = await async_client.get(f"/api/accounts/{account_id}/rate-limit-reset-credits")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accountId"] == account_id
    assert body["availableCount"] == 1
    assert body["credits"][0]["id"] == "credit-1"
    assert reset_credit_cache.get_count(account_id) == 1
    reset_credit_cache.clear(account_id)


@pytest.mark.asyncio
async def test_consume_reset_credit_returns_result_and_code(async_client, monkeypatch):
    captured: dict = {}

    async def _fake_consume(**kwargs):
        captured.update(kwargs)
        return ConsumeResetCreditPayload(code="reset", windows_reset=2)

    monkeypatch.setattr(rate_limit_resets, "consume_reset_credit", _fake_consume)

    account_id = await _import_test_account(
        async_client,
        email="reset-consume@example.com",
        account_id="acc_reset_consume",
    )
    response = await async_client.post(
        f"/api/accounts/{account_id}/rate-limit-reset-credits/consume",
        json={"credit_id": "credit-1"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "redeemed"
    assert body["code"] == "reset"
    assert body["windowsReset"] == 2
    assert captured["credit_id"] == "credit-1"
    assert captured["redeem_request_id"]


@pytest.mark.asyncio
async def test_consume_reset_credit_upstream_error_returns_409(async_client, monkeypatch):
    async def _fake_consume(**kwargs):  # noqa: ARG001
        raise ResetCreditsError(403, "not eligible")

    monkeypatch.setattr(rate_limit_resets, "consume_reset_credit", _fake_consume)

    account_id = await _import_test_account(
        async_client,
        email="reset-upstream-error@example.com",
        account_id="acc_reset_upstream_error",
    )
    response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume")
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "account_reset_credits_upstream_error"
    assert "not eligible" in body["error"]["message"]

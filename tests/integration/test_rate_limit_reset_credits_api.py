from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any

import pytest

from app.core.auth import generate_unique_account_id
from app.core.clients.rate_limit_reset_credits import (
    ConsumeResetCreditResponse,
    RateLimitResetCreditsSnapshot,
    ResetCreditItem,
    ResetCreditsResponse,
)
from app.db.session import SessionLocal
from app.modules.rate_limit_reset_credits import api as reset_credits_api
from app.modules.rate_limit_reset_credits.store import get_rate_limit_reset_credits_store

pytestmark = pytest.mark.integration


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


async def _import_test_account(async_client, *, email: str, account_id: str) -> str:
    payload = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
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


def _credit(credit_id: str, *, expires_at: str = "2026-07-12T00:00:00Z") -> ResetCreditItem:
    return ResetCreditItem.model_validate({"id": credit_id, "status": "available", "expires_at": expires_at})


def _upstream_response(credits: list[ResetCreditItem], available_count: int | None = None) -> ResetCreditsResponse:
    count = available_count if available_count is not None else len(credits)
    return ResetCreditsResponse(credits=credits, available_count=count)


def _snapshot(credits: list[ResetCreditItem], available_count: int | None = None) -> RateLimitResetCreditsSnapshot:
    available = available_count if available_count is not None else len(credits)
    expiries = [
        credit.expires_at for credit in credits if credit.status == "available" and credit.expires_at is not None
    ]
    return RateLimitResetCreditsSnapshot(
        available_count=available,
        nearest_expires_at=min(expiries) if expiries else None,
        credits=credits,
    )


@pytest.mark.asyncio
async def test_consume_paused_account_returns_409(async_client, monkeypatch) -> None:
    async def _should_not_fetch(*args: Any, **kwargs: Any) -> ResetCreditsResponse:
        raise AssertionError("paused account should not invoke upstream fetch")

    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", _should_not_fetch)

    account_id = await _import_test_account(
        async_client,
        email="reset-paused@example.com",
        account_id="acc_reset_paused",
    )
    pause_resp = await async_client.post(f"/api/accounts/{account_id}/pause")
    assert pause_resp.status_code == 200

    response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume")
    assert response.status_code == 409
    body = response.json()
    assert body["error"]["code"] == "account_not_reset_credit_applicable"


@pytest.mark.asyncio
async def test_consume_active_account_returns_success_with_mocked_upstream(async_client, monkeypatch) -> None:
    captured: dict[str, Any] = {}

    async def _fake_fetch(access_token: str, account_id: str | None, **kwargs: Any) -> ResetCreditsResponse:
        captured["fetch_account_id"] = account_id
        captured["fetch_had_token"] = bool(access_token)
        return _upstream_response([_credit("credit-1")])

    async def _fake_consume(
        access_token: str,
        account_id: str | None,
        credit_id: str,
        redeem_request_id: str | None = None,
        **kwargs: Any,
    ) -> ConsumeResetCreditResponse:
        captured.update(
            {
                "consume_account_id": account_id,
                "consume_credit_id": credit_id,
                "redeem_request_id": redeem_request_id,
                "consume_had_token": bool(access_token),
            }
        )
        return ConsumeResetCreditResponse.model_validate(
            {
                "code": "reset",
                "credit": {
                    "id": credit_id,
                    "status": "redeemed",
                    "redeemed_at": "2026-06-13T13:12:31Z",
                },
                "windows_reset": 2,
            }
        )

    async def _noop_refresh(account) -> None:  # noqa: ANN001
        return None

    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", _fake_fetch)
    monkeypatch.setattr(reset_credits_api, "consume_reset_credit", _fake_consume)
    monkeypatch.setattr(reset_credits_api, "_build_refresh_usage_callback", lambda _context: _noop_refresh)

    account_id = await _import_test_account(
        async_client,
        email="reset-active@example.com",
        account_id="acc_reset_active",
    )

    await get_rate_limit_reset_credits_store().set(account_id, _snapshot([_credit("credit-1")]))

    response = await async_client.post(
        f"/api/accounts/{account_id}/rate-limit-reset-credits/consume",
        json={"redeemRequestId": " dashboard-retry-id "},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["code"] == "reset"
    assert body["windowsReset"] == 2
    assert body["redeemedAt"] is not None
    assert datetime.fromisoformat(body["redeemedAt"].replace("Z", "+00:00")).year == 2026

    assert captured["fetch_account_id"] == "acc_reset_active"
    assert captured["fetch_had_token"] is True
    assert captured["consume_account_id"] == "acc_reset_active"
    assert captured["consume_credit_id"] == "credit-1"
    assert captured["redeem_request_id"] == "dashboard-retry-id"
    assert captured["consume_had_token"] is True


@pytest.mark.asyncio
async def test_consume_without_cached_snapshot_returns_409_without_fetch(async_client, monkeypatch) -> None:
    async def _should_not_fetch(*args: Any, **kwargs: Any) -> ResetCreditsResponse:
        raise AssertionError("uncached consume should not invoke upstream fetch")

    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", _should_not_fetch)

    account_id = await _import_test_account(
        async_client,
        email="reset-no-cache@example.com",
        account_id="acc_reset_no_cache",
    )

    response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "no_available_reset_credit"


@pytest.mark.asyncio
async def test_consume_reauth_required_account_returns_409(async_client, monkeypatch) -> None:
    async def _should_not_fetch(*args: Any, **kwargs: Any) -> ResetCreditsResponse:
        raise AssertionError("reauth account should not invoke upstream fetch")

    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", _should_not_fetch)

    account_id = await _import_test_account(
        async_client,
        email="reset-reauth@example.com",
        account_id="acc_reset_reauth",
    )

    async with SessionLocal() as session:
        from sqlalchemy import update

        from app.db.models import Account, AccountStatus

        await session.execute(
            update(Account).where(Account.id == account_id).values(status=AccountStatus.REAUTH_REQUIRED)
        )
        await session.commit()

    response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "account_not_reset_credit_applicable"


@pytest.mark.asyncio
async def test_get_returns_null_on_cache_miss_without_upstream_fetch(async_client, monkeypatch) -> None:
    account_id = await _import_test_account(
        async_client,
        email="reset-get@example.com",
        account_id="acc_reset_get",
    )

    async def _should_not_fetch(*args: Any, **kwargs: Any) -> ResetCreditsResponse:
        raise AssertionError("cache-miss GET should not invoke upstream fetch")

    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", _should_not_fetch)

    response = await async_client.get(f"/api/accounts/{account_id}/rate-limit-reset-credits")
    assert response.status_code == 200, response.text
    assert response.json() is None

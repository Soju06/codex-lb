from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import update

from app.core.clients.rate_limit_reset_credits import (
    ConsumeResetCreditResponse,
    RateLimitResetCreditsSnapshot,
    ResetCreditItem,
    ResetCreditsResponse,
)
from app.core.clients.usage import ConsumeRateLimitResetCreditResponse
from app.core.usage.models import RateLimitPayload, UsagePayload, UsageWindow
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, UsageHistory
from app.db.session import SessionLocal
from app.modules.rate_limit_reset_credits import api as reset_credits_api
from app.modules.rate_limit_reset_credits.store import get_rate_limit_reset_credits_store

pytestmark = pytest.mark.integration


def _jwt(payload: dict[str, object]) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"header.{encoded}.sig"


async def _blocked_account(async_client, name: str) -> str:
    upstream_id = f"weekly-{name}"
    email = f"{name}@example.com"
    payload = {
        "email": email,
        "chatgpt_account_id": upstream_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "pro"},
    }
    auth = {
        "tokens": {
            "idToken": _jwt(payload),
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": upstream_id,
        }
    }
    imported = await async_client.post(
        "/api/accounts/import",
        files={"auth_json": ("auth.json", json.dumps(auth), "application/json")},
    )
    assert imported.status_code == 200, imported.text
    account_id = imported.json()["accountId"]
    now = int(utcnow().replace(tzinfo=timezone.utc).timestamp())
    old_deadline = now + 3 * 24 * 3600
    blocked_at = now - 3600
    async with SessionLocal() as session:
        await session.execute(
            update(Account)
            .where(Account.id == account_id)
            .values(status=AccountStatus.RATE_LIMITED, reset_at=old_deadline, blocked_at=blocked_at)
        )
        session.add(
            UsageHistory(
                account_id=account_id,
                recorded_at=datetime.fromtimestamp(blocked_at + 30, timezone.utc).replace(tzinfo=None),
                window="primary",
                used_percent=100.0,
                reset_at=old_deadline,
                window_minutes=10_080,
            )
        )
        await session.commit()
    return account_id


async def _fresh_weekly_usage(*, used_percent: float = 0.0) -> UsagePayload:
    return UsagePayload(
        plan_type="pro",
        rate_limit=RateLimitPayload(
            primary_window=UsageWindow(
                used_percent=used_percent,
                reset_at=int(utcnow().replace(tzinfo=timezone.utc).timestamp()) + 7 * 24 * 3600,
                limit_window_seconds=604_800,
            ),
            secondary_window=None,
        ),
    )


async def _assert_rate_limited(account_id: str) -> None:
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        assert account.status == AccountStatus.RATE_LIMITED
        assert account.reset_at is not None
        assert account.blocked_at is not None


async def _assert_active(account_id: str) -> None:
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        assert account.status == AccountStatus.ACTIVE
        assert account.reset_at is None
        assert account.blocked_at is None


async def _patch_usage_fetch(monkeypatch: pytest.MonkeyPatch, *, used_percent: float = 0.0) -> None:
    async def _fetch_usage(**_kwargs):
        return await _fresh_weekly_usage(used_percent=used_percent)

    monkeypatch.setattr("app.modules.usage.updater.fetch_usage", _fetch_usage)


async def _patch_dashboard_redeem(monkeypatch: pytest.MonkeyPatch, *, code: str, windows_reset: int) -> None:
    async def _fetch_credits(*_args, **_kwargs):
        return ResetCreditsResponse(
            credits=[ResetCreditItem(id="credit-weekly", status="available", expires_at="2030-01-01T00:00:00Z")],
            available_count=1,
        )

    async def _consume(_access_token, _account_id, _credit_id, **_kwargs):
        return ConsumeResetCreditResponse.model_validate(
            {"code": code, "windows_reset": windows_reset, "credit": {"id": "credit-weekly", "status": "redeemed"}}
        )

    monkeypatch.setattr(reset_credits_api, "fetch_reset_credits", _fetch_credits)
    monkeypatch.setattr(reset_credits_api, "consume_reset_credit", _consume)


async def _prime_dashboard_credit(account_id: str) -> None:
    await get_rate_limit_reset_credits_store().set(
        account_id,
        RateLimitResetCreditsSnapshot(
            available_count=1,
            nearest_expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
            credits=[ResetCreditItem(id="credit-weekly", status="available", expires_at="2030-01-01T00:00:00Z")],
        ),
    )


@pytest.mark.parametrize("path", ["service", "reset_credits_api"])
@pytest.mark.asyncio
async def test_confirmed_reset_credit_releases_weekly_primary_account(async_client, monkeypatch, path: str) -> None:
    account_id = await _blocked_account(async_client, f"release-{path}")
    await _patch_usage_fetch(monkeypatch)
    if path == "service":
        from app.modules.accounts import service as accounts_service_module

        async def _consume(*_args, **_kwargs):
            return ConsumeRateLimitResetCreditResponse(code="reset", windows_reset=1)

        monkeypatch.setattr(accounts_service_module, "consume_rate_limit_reset_credit", _consume)
        response = await async_client.post(f"/api/accounts/{account_id}/usage-reset-credits/consume")
    else:
        await _patch_dashboard_redeem(monkeypatch, code="reset", windows_reset=1)
        await _prime_dashboard_credit(account_id)
        response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume")
    assert response.status_code == 200, response.text
    await _assert_active(account_id)


@pytest.mark.parametrize("code,windows_reset", [("nothing_to_reset", 0), ("reset", 0)])
@pytest.mark.asyncio
async def test_unconfirmed_reset_credit_does_not_release_account(
    async_client, monkeypatch, code, windows_reset
) -> None:
    account_id = await _blocked_account(async_client, f"no-confirm-{code}-{windows_reset}")
    await _patch_usage_fetch(monkeypatch)
    await _patch_dashboard_redeem(monkeypatch, code=code, windows_reset=windows_reset)
    await _prime_dashboard_credit(account_id)
    response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume")
    assert response.status_code == 200, response.text
    await _assert_rate_limited(account_id)


@pytest.mark.asyncio
async def test_confirmed_reset_with_exhausted_usage_keeps_account_rate_limited(async_client, monkeypatch) -> None:
    account_id = await _blocked_account(async_client, "still-exhausted")
    await _patch_usage_fetch(monkeypatch, used_percent=100.0)
    await _patch_dashboard_redeem(monkeypatch, code="reset", windows_reset=1)
    await _prime_dashboard_credit(account_id)
    response = await async_client.post(f"/api/accounts/{account_id}/rate-limit-reset-credits/consume")
    assert response.status_code == 200, response.text
    await _assert_rate_limited(account_id)

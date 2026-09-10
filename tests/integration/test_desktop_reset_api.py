from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from app.db.models import Account, AccountStatus, DesktopResetCreditRedemption
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.rate_limit_reset_credits.store import RateLimitResetCreditsStore, get_rate_limit_reset_credits_store
from app.modules.settings.repository import SettingsRepository
from tests.integration.desktop_reset_support import HEADERS, ORIGINAL, seed

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
URL = "/api/codex/desktop/reset-credits"


@pytest.fixture(autouse=True)
async def clear_inventory():
    await get_rate_limit_reset_credits_store().invalidate()
    yield
    await get_rate_limit_reset_credits_store().invalidate()


@pytest.mark.parametrize("suffix", ["", "/"])
async def test_native_inventory_and_summary_combine_real_credits(async_client, db_setup, monkeypatch, suffix):
    await seed(monkeypatch)
    response = await async_client.get(URL + suffix, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()["available_count"] == 2
    assert [c["id"] for c in response.json()["credits"]] == ["second-credit", "primary-credit"]
    usage = await async_client.get("/api/codex/desktop/usage", headers=HEADERS)
    assert usage.status_code == 200, usage.text
    assert usage.json()["rate_limit_reset_credits"] == response.json()
    assert usage.json()["account_id"] == "original-account"


async def test_disabled_pool_preserves_original_inventory_and_redemption(async_client, db_setup, monkeypatch):
    fake = await seed(monkeypatch, pooled=False)
    credits = await async_client.get(URL, headers=HEADERS)
    assert credits.json()["available_count"] == 1
    assert [c["id"] for c in credits.json()["credits"]] == ["primary-credit"]
    usage = await async_client.get("/api/codex/desktop/usage", headers=HEADERS)
    assert usage.json()["rate_limit_reset_credits"] == ORIGINAL["rate_limit_reset_credits"]
    reset = await async_client.post(URL + "/consume", headers=HEADERS, json={"redeem_request_id": "disabled"})
    assert reset.status_code == 200, reset.text
    assert fake.calls[0][:2] == ("primary", "primary-credit")


@pytest.mark.parametrize("suffix", ["", "/"])
async def test_default_reset_uses_earliest_expiry_owner(async_client, db_setup, monkeypatch, suffix):
    fake = await seed(monkeypatch)
    response = await async_client.post(
        URL + "/consume" + suffix, headers=HEADERS, json={"redeem_request_id": "earliest"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["code"] == "reset"
    assert response.json()["credit"]["id"] == "second-credit"
    assert fake.calls[0][:2] == ("second", "second-credit")
    assert len(fake.spent) == 1


async def test_explicit_selection_is_honored(async_client, db_setup, monkeypatch):
    fake = await seed(monkeypatch)
    response = await async_client.post(
        URL + "/consume", headers=HEADERS, json={"redeem_request_id": "selected", "credit_id": "primary-credit"}
    )
    assert response.status_code == 200, response.text
    assert fake.calls[0][:2] == ("primary", "primary-credit")


async def test_retry_on_another_replica_keeps_owner_after_lost_response(async_client, db_setup, monkeypatch):
    fake = await seed(monkeypatch)
    fake.lose_response = True
    payload = {"redeem_request_id": "lost-response"}
    first = await async_client.post(URL + "/consume", headers=HEADERS, json=payload)
    assert first.status_code == 503, first.text
    monkeypatch.setattr(
        "app.modules.desktop_resets.inventory.get_rate_limit_reset_credits_store", RateLimitResetCreditsStore
    )
    second = await async_client.post(URL + "/consume", headers=HEADERS, json=payload)
    assert second.status_code == 200, second.text
    assert second.json()["code"] == "already_redeemed"
    assert fake.calls[0] == fake.calls[1]
    assert len(fake.spent) == 1


async def test_deleted_owner_does_not_redirect_retry(async_client, db_setup, monkeypatch):
    fake = await seed(monkeypatch)
    fake.lose_response = True
    payload = {"redeem_request_id": "deleted-owner"}
    first = await async_client.post(URL + "/consume", headers=HEADERS, json=payload)
    assert first.status_code == 503
    async with SessionLocal() as session:
        await session.execute(delete(Account).where(Account.id == "second"))
        await session.commit()
    second = await async_client.post(URL + "/consume", headers=HEADERS, json=payload)
    assert second.status_code == 503, second.text
    assert len(fake.calls) == 1
    async with SessionLocal() as session:
        assert await session.get(DesktopResetCreditRedemption, ("original-account", "deleted-owner")) is not None


async def test_disabling_policy_prevents_cross_account_retry(async_client, db_setup, monkeypatch):
    fake = await seed(monkeypatch)
    fake.lose_response = True
    payload = {"redeem_request_id": "policy-disabled"}
    assert (await async_client.post(URL + "/consume", headers=HEADERS, json=payload)).status_code == 503
    async with SessionLocal() as session:
        settings = await SettingsRepository(session).get_or_create()
        settings.desktop_reset_pool_enabled = False
        await session.commit()
    response = await async_client.post(URL + "/consume", headers=HEADERS, json=payload)
    assert response.status_code == 401, response.text
    assert len(fake.calls) == 1


async def test_concurrent_same_request_spends_only_one_credit(async_client, db_setup, monkeypatch):
    fake = await seed(monkeypatch)

    async def consume():
        return await async_client.post(URL + "/consume", headers=HEADERS, json={"redeem_request_id": "concurrent"})

    responses = await asyncio.gather(consume(), consume())
    assert all(response.status_code == 200 for response in responses), [r.text for r in responses]
    assert sorted(r.json()["code"] for r in responses) == ["already_redeemed", "reset"]
    assert len(fake.spent) == 1
    assert fake.calls[0] == fake.calls[1]


@pytest.mark.parametrize("outcome", ["nothing_to_reset", "no_credit"])
async def test_rejected_reset_does_not_try_another_credit(async_client, db_setup, monkeypatch, outcome):
    fake = await seed(monkeypatch)
    fake.rejection = outcome
    response = await async_client.post(URL + "/consume", headers=HEADERS, json={"redeem_request_id": "rejected"})
    assert response.status_code == 200, response.text
    assert response.json()["code"] == outcome
    assert len(fake.calls) == 1 and not fake.spent


@pytest.mark.parametrize("status", [AccountStatus.PAUSED, AccountStatus.DEACTIVATED, AccountStatus.REAUTH_REQUIRED])
async def test_ineligible_owner_is_excluded(async_client, db_setup, monkeypatch, status):
    await seed(monkeypatch)
    async with SessionLocal() as session:
        account = await AccountsRepository(session).get_by_id("second")
        assert account is not None
        account.status = status
        await session.commit()
    response = await async_client.get(URL, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert [c["id"] for c in response.json()["credits"]] == ["primary-credit"]


async def test_expired_credit_is_not_counted_or_selected(async_client, db_setup, monkeypatch):
    fake = await seed(monkeypatch)
    fake.credits["second"][0].expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    response = await async_client.get(URL, headers=HEADERS)
    assert response.json()["available_count"] == 1
    reset = await async_client.post(URL + "/consume", headers=HEADERS, json={"redeem_request_id": "expired"})
    assert reset.status_code == 200, reset.text
    assert fake.calls[0][:2] == ("primary", "primary-credit")


async def test_incomplete_inventory_does_not_invent_credits_or_break_quota(async_client, db_setup, monkeypatch):
    fake = await seed(monkeypatch)
    fake.fail_owner = "second"
    response = await async_client.get(URL, headers=HEADERS)
    assert response.status_code == 503, response.text
    usage = await async_client.get("/api/codex/desktop/usage", headers=HEADERS)
    assert usage.status_code == 200, usage.text
    assert "rate_limit_reset_credits" not in usage.json()


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer sk-clb-only"}, {**HEADERS, "Authorization": "Bearer forged"}]
)
async def test_native_reset_requires_original_chatgpt_identity(async_client, db_setup, monkeypatch, headers):
    fake = await seed(monkeypatch)
    response = await async_client.post(URL + "/consume", headers=headers, json={"redeem_request_id": "unauthorized"})
    assert response.status_code == 401, response.text
    assert not fake.calls


async def test_original_account_preserves_native_idempotency_key(async_client, db_setup, monkeypatch):
    fake = await seed(monkeypatch, pooled=False)
    response = await async_client.post(
        URL + "/consume", headers=HEADERS, json={"redeem_request_id": "native-existing-attempt"}
    )
    assert response.status_code == 200, response.text
    assert fake.calls[0][2] == "native-existing-attempt"

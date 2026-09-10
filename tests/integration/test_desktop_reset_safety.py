from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, update

from app.db.models import AccountStatus, DesktopResetCreditRedemption, ResetCreditRedeemRequest
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.desktop_resets.repository import RedemptionPin, RedemptionRepository
from app.modules.rate_limit_reset_credits.store import get_rate_limit_reset_credits_store
from app.modules.settings.repository import SettingsRepository
from tests.integration.desktop_reset_support import HEADERS, seed

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
URL = "/api/codex/desktop/reset-credits"


@pytest.fixture(autouse=True)
async def clear_inventory():
    await get_rate_limit_reset_credits_store().invalidate()
    yield
    await get_rate_limit_reset_credits_store().invalidate()


@pytest.mark.parametrize("method", ["GET", "POST"])
async def test_inventory_timeout_cancels_active_and_queued_refreshes(async_client, db_setup, monkeypatch, method):
    fake = await seed(monkeypatch)
    active = set()
    started = []
    cancelled = []

    async def blocked(token, account_id, **kwargs):
        started.append(account_id)
        active.add(account_id)
        try:
            await asyncio.Event().wait()
        finally:
            active.remove(account_id)
            cancelled.append(account_id)

    monkeypatch.setattr("app.modules.desktop_resets.inventory._REFRESH_CONCURRENCY", 1)
    monkeypatch.setattr("app.modules.desktop_resets.inventory._REFRESH_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr("app.modules.desktop_resets.inventory.fetch_reset_credits", blocked)
    response = await async_client.request(
        method,
        URL if method == "GET" else URL + "/consume",
        headers=HEADERS,
        json={"redeem_request_id": "timeout"},
    )
    assert response.status_code == 503, response.text
    assert len(started) == 1
    assert cancelled == started
    assert not active
    assert not fake.calls


async def test_inventory_failure_awaits_cancelled_sibling(async_client, db_setup, monkeypatch):
    await seed(monkeypatch)
    sibling_started = asyncio.Event()
    sibling_stopped = asyncio.Event()

    async def failing(token, account_id, **kwargs):
        if account_id == "original-account":
            await sibling_started.wait()
            raise RuntimeError("Synthetic inventory failure")
        sibling_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            sibling_stopped.set()

    monkeypatch.setattr("app.modules.desktop_resets.inventory.fetch_reset_credits", failing)
    response = await async_client.get(URL, headers=HEADERS)
    assert response.status_code == 503, response.text
    assert sibling_stopped.is_set()


@pytest.mark.parametrize(
    ("change", "expected_status"), [("policy", 401), ("owner", 503), ("caller", 401), ("identity", 503)]
)
async def test_admission_is_rechecked_immediately_before_consumption(
    async_client, db_setup, monkeypatch, change, expected_status
):
    fake = await seed(monkeypatch)

    async def change_before_consume(account):
        async with SessionLocal() as session:
            if change == "policy":
                settings = await SettingsRepository(session).get_or_create()
                settings.desktop_reset_pool_enabled = False
            else:
                row = await AccountsRepository(session).get_by_id("primary" if change == "caller" else "second")
                assert row is not None
                if change == "identity":
                    row.chatgpt_account_id = "replacement-account"
                else:
                    row.status = AccountStatus.PAUSED
            await session.commit()
        return None

    monkeypatch.setattr("app.modules.desktop_resets.service._consume_route", change_before_consume)
    response = await async_client.post(URL + "/consume", headers=HEADERS, json={"redeem_request_id": "changed"})
    assert response.status_code == expected_status, response.text
    assert not fake.calls
    assert not fake.spent


async def test_conflicting_explicit_credit_cannot_rebind_request(async_client, db_setup, monkeypatch):
    fake = await seed(monkeypatch)
    first = await async_client.post(
        URL + "/consume", headers=HEADERS, json={"redeem_request_id": "bound", "credit_id": "second-credit"}
    )
    assert first.status_code == 200, first.text
    conflict = await async_client.post(
        URL + "/consume", headers=HEADERS, json={"redeem_request_id": "bound", "credit_id": "primary-credit"}
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "reset_credit_request_conflict"
    assert len(fake.calls) == 1
    assert fake.credits["primary"][0].status == "available"


async def test_unknown_explicit_credit_does_not_fall_back_or_pin(async_client, db_setup, monkeypatch):
    fake = await seed(monkeypatch)
    response = await async_client.post(
        URL + "/consume", headers=HEADERS, json={"redeem_request_id": "missing", "credit_id": "missing-credit"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["code"] == "no_credit"
    assert not fake.calls
    async with SessionLocal() as session:
        assert await RedemptionRepository(session).get("original-account", "missing") is None


async def test_inventory_and_usage_count_drop_after_reset(async_client, db_setup, monkeypatch):
    await seed(monkeypatch)
    response = await async_client.post(URL + "/consume", headers=HEADERS, json={"redeem_request_id": "count"})
    assert response.status_code == 200, response.text
    inventory = await async_client.get(URL, headers=HEADERS)
    assert inventory.status_code == 200, inventory.text
    assert inventory.json()["available_count"] == 1
    assert [credit["id"] for credit in inventory.json()["credits"]] == ["primary-credit"]
    usage = await async_client.get("/api/codex/desktop/usage", headers=HEADERS)
    assert usage.status_code == 200, usage.text
    assert usage.json()["rate_limit_reset_credits"] == inventory.json()


async def test_redemption_ledger_first_writer_wins_across_sessions(db_setup):
    ready = asyncio.Barrier(2)
    choices = [RedemptionPin("owner-a", "credit-a", "upstream-a"), RedemptionPin("owner-b", "credit-b", "upstream-b")]

    async def pin(choice):
        async with SessionLocal() as session:
            await ready.wait()
            return await RedemptionRepository(session).pin("caller", "same-request", choice)

    results = await asyncio.gather(*(pin(choice) for choice in choices))
    assert results[0] == results[1]
    assert results[0] in choices
    async with SessionLocal() as session:
        repository = RedemptionRepository(session)
        assert await repository.get("caller", "same-request") == results[0]
        assert await session.scalar(select(func.count()).select_from(DesktopResetCreditRedemption)) == 1
        assert await repository.pin("another-caller", "same-request", choices[1]) == choices[1]


async def test_old_retry_still_reaches_original_upstream_result(async_client, db_setup, monkeypatch):
    fake = await seed(monkeypatch)
    fake.lose_response = True
    payload = {"redeem_request_id": "late-retry"}
    response = await async_client.post(URL + "/consume", headers=HEADERS, json=payload)
    assert response.status_code == 503
    async with SessionLocal() as session:
        await session.execute(
            update(ResetCreditRedeemRequest).values(created_at=datetime.now(timezone.utc) - timedelta(days=2))
        )
        await session.commit()
    await get_rate_limit_reset_credits_store().invalidate()
    response = await async_client.post(URL + "/consume", headers=HEADERS, json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["code"] == "already_redeemed", (response.json(), fake.calls)


async def test_durable_pin_is_enough_to_resume_after_crash(async_client, db_setup, monkeypatch):
    fake = await seed(monkeypatch)
    async with SessionLocal() as session:
        await RedemptionRepository(session).pin(
            "original-account", "crashed-after-pin", RedemptionPin("second", "second-credit", "second-account")
        )
    await get_rate_limit_reset_credits_store().invalidate()
    response = await async_client.post(
        URL + "/consume", headers=HEADERS, json={"redeem_request_id": "crashed-after-pin"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["code"] == "reset", (response.json(), fake.calls)


async def test_reauthorized_owner_cannot_redirect_a_pinned_retry(async_client, db_setup, monkeypatch):
    fake = await seed(monkeypatch)
    fake.lose_response = True
    payload = {"redeem_request_id": "reauthorized-owner"}
    assert (await async_client.post(URL + "/consume", headers=HEADERS, json=payload)).status_code == 503
    async with SessionLocal() as session:
        owner = await AccountsRepository(session).get_by_id("second")
        assert owner is not None
        owner.chatgpt_account_id = "replacement-upstream-account"
        await session.commit()
    response = await async_client.post(URL + "/consume", headers=HEADERS, json=payload)
    assert response.status_code == 503, response.text
    assert len(fake.calls) == 1


async def test_disagreeing_helper_binding_returns_permanent_conflict(async_client, db_setup, monkeypatch):
    fake = await seed(monkeypatch, pooled=False)
    async with SessionLocal() as session:
        session.add(
            ResetCreditRedeemRequest(
                account_id="primary",
                redeem_request_id="disagreement",
                credit_id="different-credit",
                created_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
    response = await async_client.post(URL + "/consume", headers=HEADERS, json={"redeem_request_id": "disagreement"})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "reset_credit_request_conflict"
    assert not fake.calls


async def test_inventory_releases_database_connections_before_upstream_io(async_client, db_setup, monkeypatch):
    from sqlalchemy import event
    from sqlalchemy.engine import Engine

    fake = await seed(monkeypatch)
    checked_out = {}

    def checkout(connection, record, proxy):
        checked_out[record] = asyncio.current_task()

    def checkin(connection, record):
        checked_out.pop(record, None)

    async def fetch_without_connection(token, account_id, **kwargs):
        assert asyncio.current_task() not in checked_out.values()
        return await fake.fetch(token, account_id, **kwargs)

    event.listen(Engine, "checkout", checkout)
    event.listen(Engine, "checkin", checkin)
    monkeypatch.setattr("app.modules.desktop_resets.inventory.fetch_reset_credits", fetch_without_connection)
    try:
        response = await async_client.get(URL, headers=HEADERS)
        assert response.status_code == 200, response.text
        assert response.json()["available_count"] == 2
    finally:
        event.remove(Engine, "checkout", checkout)
        event.remove(Engine, "checkin", checkin)

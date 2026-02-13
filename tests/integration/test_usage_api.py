from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration


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


@pytest.mark.asyncio
async def test_usage_summary_empty_returns_zeroes(async_client):
    response = await async_client.get("/api/usage/summary")
    assert response.status_code == 200
    payload = response.json()

    primary = payload["primaryWindow"]
    assert primary["remainingPercent"] == 0.0
    assert primary["capacityCredits"] == 0.0
    assert primary["remainingCredits"] == 0.0
    assert primary["windowMinutes"] == 300

    secondary = payload["secondaryWindow"]
    assert secondary["remainingPercent"] == 0.0
    assert secondary["capacityCredits"] == 0.0
    assert secondary["remainingCredits"] == 0.0
    assert secondary["windowMinutes"] == 10080

    cost = payload["cost"]
    assert cost["currency"] == "USD"
    assert cost["totalUsd7d"] == 0.0

    metrics = payload["metrics"]
    assert metrics["requests7d"] == 0
    assert metrics["tokensSecondaryWindow"] == 0
    assert metrics["cachedTokensSecondaryWindow"] == 0
    assert metrics["errorRate7d"] is None
    assert metrics["topError"] is None


@pytest.mark.asyncio
async def test_usage_history_aggregates_per_account(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_a", "a@example.com"))
        await accounts_repo.upsert(_make_account("acc_b", "b@example.com"))

        await usage_repo.add_entry("acc_a", 10.0, recorded_at=now - timedelta(hours=3))
        await usage_repo.add_entry("acc_a", 30.0, recorded_at=now - timedelta(hours=2))

    response = await async_client.get("/api/usage/history?hours=24")
    assert response.status_code == 200
    payload = response.json()
    assert payload["windowHours"] == 24

    accounts = {item["accountId"]: item for item in payload["accounts"]}
    acc_a = accounts["acc_a"]
    acc_b = accounts["acc_b"]

    assert acc_a["remainingPercentAvg"] == pytest.approx(80.0)
    assert acc_a["capacityCredits"] == pytest.approx(225.0)
    assert acc_a["remainingCredits"] == pytest.approx(180.0)

    assert acc_b["remainingPercentAvg"] == pytest.approx(100.0)
    assert acc_b["capacityCredits"] == pytest.approx(225.0)
    assert acc_b["remainingCredits"] == pytest.approx(225.0)


@pytest.mark.asyncio
async def test_usage_window_secondary_uses_latest_window_minutes(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_sec", "sec@example.com"))
        await usage_repo.add_entry(
            "acc_sec",
            40.0,
            window="secondary",
            reset_at=1735689600,
            window_minutes=1440,
            recorded_at=now - timedelta(minutes=5),
        )
    response = await async_client.get("/api/usage/window?window=secondary")
    assert response.status_code == 200
    payload = response.json()
    assert payload["windowKey"] == "secondary"
    assert payload["windowMinutes"] == 1440

    accounts = {item["accountId"]: item for item in payload["accounts"]}
    entry = accounts["acc_sec"]
    assert entry["remainingPercentAvg"] == pytest.approx(60.0)
    assert entry["capacityCredits"] == pytest.approx(7560.0)
    assert entry["remainingCredits"] == pytest.approx(4536.0)


@pytest.mark.asyncio
async def test_usage_history_team_plan_has_capacity(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_team", "team@example.com", plan_type="team"))
        await usage_repo.add_entry(
            "acc_team",
            20.0,
            window="primary",
            recorded_at=now - timedelta(hours=1),
        )

    response = await async_client.get("/api/usage/history?hours=24")
    assert response.status_code == 200
    payload = response.json()
    accounts = {item["accountId"]: item for item in payload["accounts"]}
    entry = accounts["acc_team"]

    assert entry["remainingPercentAvg"] == pytest.approx(80.0)
    assert entry["capacityCredits"] == pytest.approx(225.0)
    assert entry["remainingCredits"] == pytest.approx(180.0)


@pytest.mark.asyncio
async def test_usage_history_invalid_hours_returns_validation_error(async_client):
    response = await async_client.get("/api/usage/history?hours=0")
    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_usage_window_invalid_query_returns_validation_error(async_client):
    response = await async_client.get("/api/usage/window?window=invalid")
    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "validation_error"

from __future__ import annotations

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
async def test_codex_usage_aggregates_windows(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_a", "a@example.com"))
        await accounts_repo.upsert(_make_account("acc_b", "b@example.com"))

        await usage_repo.add_entry(
            "acc_a",
            10.0,
            window="primary",
            reset_at=0,
            window_minutes=300,
            credits_has=True,
            credits_unlimited=False,
            credits_balance=12.5,
        )
        await usage_repo.add_entry(
            "acc_b",
            30.0,
            window="primary",
            reset_at=0,
            window_minutes=300,
            credits_has=False,
            credits_unlimited=False,
            credits_balance=2.5,
        )
        await usage_repo.add_entry(
            "acc_a",
            40.0,
            window="secondary",
            reset_at=0,
            window_minutes=10080,
        )
        await usage_repo.add_entry(
            "acc_b",
            60.0,
            window="secondary",
            reset_at=0,
            window_minutes=10080,
        )
        await usage_repo.add_entry(
            "acc_a",
            20.0,
            window="spark_primary",
            window_label="gpt-5-codex-spark_window",
            reset_at=0,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            "acc_b",
            40.0,
            window="spark_primary",
            window_label="gpt-5-codex-spark_window",
            reset_at=0,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            "acc_a",
            25.0,
            window="spark_secondary",
            window_label="gpt-5-codex-spark_window",
            reset_at=0,
            window_minutes=10080,
        )
        await usage_repo.add_entry(
            "acc_b",
            35.0,
            window="spark_secondary",
            window_label="gpt-5-codex-spark_window",
            reset_at=0,
            window_minutes=10080,
        )

    response = await async_client.get("/api/codex/usage")
    assert response.status_code == 200
    payload = response.json()

    assert payload["plan_type"] == "plus"
    rate_limit = payload["rate_limit"]
    assert rate_limit["allowed"] is True
    assert rate_limit["limit_reached"] is False

    primary = rate_limit["primary_window"]
    assert primary["used_percent"] == 20
    assert primary["limit_window_seconds"] == 18000
    assert primary["reset_after_seconds"] == 0
    assert primary["reset_at"] == 0

    secondary = rate_limit["secondary_window"]
    assert secondary["used_percent"] == 50
    assert secondary["limit_window_seconds"] == 604800
    assert secondary["reset_after_seconds"] == 0
    assert secondary["reset_at"] == 0

    spark_primary = rate_limit["spark_primary_window"]
    assert spark_primary["used_percent"] == 30
    assert spark_primary["limit_window_seconds"] == 18000
    assert spark_primary["reset_after_seconds"] == 0
    assert spark_primary["reset_at"] == 0

    spark_secondary = rate_limit["spark_secondary_window"]
    assert spark_secondary["used_percent"] == 30
    assert spark_secondary["limit_window_seconds"] == 604800
    assert spark_secondary["reset_after_seconds"] == 0
    assert spark_secondary["reset_at"] == 0
    assert rate_limit["spark_window_label"] == "Gpt 5 Codex Spark"

    credits = payload["credits"]
    assert credits["has_credits"] is True
    assert credits["unlimited"] is False
    assert credits["balance"] == "15.0"


@pytest.mark.asyncio
async def test_codex_usage_header_ignored(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_a", "a@example.com"))
        await accounts_repo.upsert(_make_account("acc_b", "b@example.com"))

        await usage_repo.add_entry(
            "acc_a",
            10.0,
            window="primary",
            reset_at=0,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            "acc_b",
            90.0,
            window="primary",
            reset_at=0,
            window_minutes=300,
        )

    response = await async_client.get(
        "/api/codex/usage",
        headers={"chatgpt-account-id": "acc_b"},
    )
    assert response.status_code == 200
    payload = response.json()
    primary = payload["rate_limit"]["primary_window"]
    assert primary["used_percent"] == 50

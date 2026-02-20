from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.crypto import TokenEncryptor
from app.core.usage.models import UsagePayload
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration


def _make_account(
    account_id: str,
    email: str,
    *,
    chatgpt_account_id: str | None = None,
    plan_type: str = "plus",
) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        chatgpt_account_id=chatgpt_account_id,
        email=email,
        plan_type=plan_type,
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


@pytest.fixture(autouse=True)
def stub_codex_usage_caller_validation(monkeypatch):
    async def stub_fetch_usage(*, access_token: str, account_id: str | None, **_: object) -> UsagePayload:
        assert access_token == "chatgpt-token"
        assert account_id is not None
        return UsagePayload.model_validate({"plan_type": "plus"})

    monkeypatch.setattr("app.core.auth.dependencies.fetch_usage", stub_fetch_usage)


@pytest.mark.asyncio
async def test_codex_usage_aggregates_windows(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_a", "a@example.com", chatgpt_account_id="workspace_acc_a"))
        await accounts_repo.upsert(_make_account("acc_b", "b@example.com", chatgpt_account_id="workspace_acc_b"))

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

    response = await async_client.get(
        "/api/codex/usage",
        headers={
            "Authorization": "Bearer chatgpt-token",
            "chatgpt-account-id": "workspace_acc_a",
        },
    )
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

    credits = payload["credits"]
    assert credits["has_credits"] is True
    assert credits["unlimited"] is False
    assert credits["balance"] == "15.0"


@pytest.mark.asyncio
async def test_codex_usage_header_ignored(async_client, db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_a", "a@example.com", chatgpt_account_id="workspace_acc_a"))
        await accounts_repo.upsert(_make_account("acc_b", "b@example.com", chatgpt_account_id="workspace_acc_b"))

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
        headers={
            "Authorization": "Bearer chatgpt-token",
            "chatgpt-account-id": "workspace_acc_b",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    primary = payload["rate_limit"]["primary_window"]
    assert primary["used_percent"] == 50


@pytest.mark.asyncio
async def test_codex_usage_prefers_newer_weekly_primary_over_stale_secondary(async_client, db_setup):
    now = utcnow()
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(
            _make_account("acc_weekly", "weekly@example.com", chatgpt_account_id="workspace_weekly")
        )

        await usage_repo.add_entry(
            "acc_weekly",
            15.0,
            window="secondary",
            reset_at=1735689600,
            window_minutes=10080,
            recorded_at=now - timedelta(days=2),
        )
        await usage_repo.add_entry(
            "acc_weekly",
            80.0,
            window="primary",
            reset_at=1735862400,
            window_minutes=10080,
            recorded_at=now,
        )

    response = await async_client.get(
        "/api/codex/usage",
        headers={
            "Authorization": "Bearer chatgpt-token",
            "chatgpt-account-id": "workspace_weekly",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    rate_limit = payload["rate_limit"]
    assert rate_limit["primary_window"] is None
    assert rate_limit["secondary_window"]["used_percent"] == 80
    assert rate_limit["secondary_window"]["reset_at"] == 1735862400

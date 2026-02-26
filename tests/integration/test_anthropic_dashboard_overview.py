from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration


def _make_account(account_id: str, email: str, plan_type: str = "pro") -> Account:
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
async def test_dashboard_overview_includes_anthropic_logs_and_cost(async_client, db_setup):
    now = utcnow().replace(microsecond=0)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        logs_repo = RequestLogsRepository(session)

        await accounts_repo.upsert(_make_account("anthropic_default", "anthropic@local", plan_type="pro"))
        await usage_repo.add_entry(
            "anthropic_default",
            15.0,
            window="primary",
            window_minutes=300,
            recorded_at=now - timedelta(minutes=3),
        )
        await usage_repo.add_entry(
            "anthropic_default",
            40.0,
            window="secondary",
            window_minutes=10080,
            recorded_at=now - timedelta(minutes=1),
        )
        await logs_repo.add_log(
            account_id="anthropic_default",
            request_id="req_anthropic_dashboard_1",
            model="claude-sonnet-4-20250514",
            input_tokens=1000,
            output_tokens=500,
            latency_ms=120,
            status="success",
            error_code=None,
            requested_at=now,
        )

    response = await async_client.get("/api/dashboard/overview")
    assert response.status_code == 200
    payload = response.json()

    assert payload["summary"]["primaryWindow"]["windowMinutes"] == 300
    assert payload["summary"]["secondaryWindow"]["windowMinutes"] == 10080
    assert payload["summary"]["cost"]["totalUsd7d"] > 0
    assert payload["summary"]["metrics"]["requests7d"] == 1

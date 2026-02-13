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
async def test_dashboard_overview_combines_data(async_client, db_setup):
    now = utcnow().replace(microsecond=0)
    primary_time = now - timedelta(minutes=5)
    secondary_time = now - timedelta(minutes=2)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        logs_repo = RequestLogsRepository(session)

        await accounts_repo.upsert(_make_account("acc_dash", "dash@example.com"))
        await usage_repo.add_entry(
            "acc_dash",
            20.0,
            window="primary",
            recorded_at=primary_time,
        )
        await usage_repo.add_entry(
            "acc_dash",
            40.0,
            window="secondary",
            recorded_at=secondary_time,
        )
        await logs_repo.add_log(
            account_id="acc_dash",
            request_id="req_dash_1",
            model="gpt-5.1",
            input_tokens=100,
            output_tokens=50,
            latency_ms=50,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/dashboard/overview")
    assert response.status_code == 200
    payload = response.json()

    assert payload["accounts"][0]["accountId"] == "acc_dash"
    assert payload["summary"]["primaryWindow"]["capacityCredits"] == pytest.approx(225.0)
    assert payload["windows"]["primary"]["windowKey"] == "primary"
    assert payload["windows"]["secondary"]["windowKey"] == "secondary"
    assert "requestLogs" not in payload
    assert payload["lastSyncAt"] == secondary_time.isoformat() + "Z"

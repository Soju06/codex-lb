from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, RequestLog
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


def _make_account(account_id: str, email: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email,
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=datetime.now(timezone.utc),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


async def test_reports_api_returns_null_account_bucket(async_client, db_setup):
    start_at = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
    async with SessionLocal() as session:
        session.add(_make_account("acc_reports", "reports@example.com"))
        session.add_all(
            [
                RequestLog(
                    account_id="acc_reports",
                    request_id="report-request-1",
                    requested_at=start_at,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=12,
                    output_tokens=4,
                    cached_input_tokens=2,
                    cost_usd=0.35,
                ),
                RequestLog(
                    account_id=None,
                    request_id="report-request-2",
                    requested_at=start_at,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=3,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=0.20,
                ),
            ]
        )
        await session.commit()

    response = await async_client.get(
        "/api/reports",
        params={
            "start_date": start_at.date().isoformat(),
            "end_date": start_at.date().isoformat(),
        },
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["daily"] == [
        {
            "activeAccounts": 1,
            "costUsd": 0.55,
            "cachedInputTokens": 2,
            "date": start_at.date().isoformat(),
            "errorCount": 0,
            "requests": 2,
            "inputTokens": 15,
            "outputTokens": 5,
        }
    ]
    assert payload["byAccount"] == [
        {
            "accountId": "acc_reports",
            "alias": None,
            "costUsd": 0.35,
            "requests": 1,
        },
        {
            "accountId": None,
            "alias": None,
            "costUsd": 0.2,
            "requests": 1,
        },
    ]

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, RequestLog
from app.db.session import SessionLocal
from app.modules.reports.thread_identity import MAX_THREAD_IDENTITY_DAYS

pytestmark = pytest.mark.integration

BASE = datetime(2026, 6, 1, 12, 0)
DAY = BASE.date().isoformat()


def _make_account(account_id: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=datetime.now(timezone.utc).replace(tzinfo=None),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


def _log(
    request_id: str,
    *,
    account_id: str,
    minutes: int,
    conversation_id: str | None = None,
    input_tokens: int,
    cached_input_tokens: int,
) -> RequestLog:
    return RequestLog(
        account_id=account_id,
        api_key_id="key-1",
        request_id=request_id,
        conversation_id=conversation_id,
        requested_at=BASE + timedelta(minutes=minutes),
        model="gpt-5.1",
        status="success",
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
    )


async def test_thread_identity_api_splits_keyed_and_unkeyed(async_client, db_setup):
    async with SessionLocal() as session:
        session.add_all([_make_account("acc_ti_a"), _make_account("acc_ti_b")])
        session.add_all(
            [
                _log(
                    "ti-k1",
                    account_id="acc_ti_a",
                    minutes=0,
                    conversation_id="conv-1",
                    input_tokens=10_000,
                    cached_input_tokens=9_000,
                ),
                _log(
                    "ti-k2",
                    account_id="acc_ti_a",
                    minutes=1,
                    conversation_id="conv-1",
                    input_tokens=11_000,
                    cached_input_tokens=9_000,
                ),
                _log(
                    "ti-k3",
                    account_id="acc_ti_b",
                    minutes=2,
                    conversation_id="conv-1",
                    input_tokens=12_000,
                    cached_input_tokens=0,
                ),
                _log("ti-u1", account_id="acc_ti_a", minutes=0, input_tokens=10_000, cached_input_tokens=0),
            ]
        )
        await session.commit()

    response = await async_client.get(
        "/api/reports/thread-identity",
        params={"start_date": DAY, "end_date": DAY, "timezone": "UTC"},
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["available"] is True
    assert payload["windowDays"] == 1
    assert payload["maxDays"] == MAX_THREAD_IDENTITY_DAYS
    assert payload["totalRequests"] == 4
    assert payload["unkeyedRequestShare"] == 0.25
    assert payload["keyed"] == {
        "requests": 3,
        "requestShare": 0.75,
        "unattributedRequestShare": 0.0,
        "conversations": 1,
        "meanAccountsPerConversation": 2.0,
        "singleAccountConversationShare": 0.0,
        "turns": 2,
        "accountSwitchRate": 0.5,
        "cacheHitRatio": round(18_000 / 33_000, 4),
        "cacheSampleInputTokens": 33_000,
        "threadGroupingApproximate": False,
    }
    assert payload["unkeyed"]["threadGroupingApproximate"] is True
    assert payload["unkeyed"]["requests"] == 1


async def test_thread_identity_api_reports_unavailable_for_wide_ranges(async_client, db_setup):
    end = (BASE.date() + timedelta(days=MAX_THREAD_IDENTITY_DAYS)).isoformat()

    response = await async_client.get(
        "/api/reports/thread-identity",
        params={"start_date": DAY, "end_date": end, "timezone": "UTC"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert payload["windowDays"] == MAX_THREAD_IDENTITY_DAYS + 1
    assert payload["totalRequests"] == 0


async def test_thread_identity_api_rejects_an_inverted_range(async_client, db_setup):
    response = await async_client.get(
        "/api/reports/thread-identity",
        params={"start_date": DAY, "end_date": "2026-05-01"},
    )

    assert response.status_code == 400

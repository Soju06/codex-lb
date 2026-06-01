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


async def test_reports_api_includes_end_date_until_next_midnight(async_client, db_setup):
    end_day_last_second = datetime(2026, 6, 1, 23, 59, 59, tzinfo=timezone.utc)
    next_day_midnight = datetime(2026, 6, 2, 0, 0, 0, tzinfo=timezone.utc)
    async with SessionLocal() as session:
        session.add(_make_account("acc_reports_end", "reports-end@example.com"))
        session.add_all(
            [
                RequestLog(
                    account_id="acc_reports_end",
                    request_id="report-end-included",
                    requested_at=end_day_last_second,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=10,
                    output_tokens=5,
                    cached_input_tokens=0,
                    cost_usd=0.5,
                ),
                RequestLog(
                    account_id="acc_reports_end",
                    request_id="report-end-excluded",
                    requested_at=next_day_midnight,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=99,
                    output_tokens=99,
                    cached_input_tokens=0,
                    cost_usd=9.9,
                ),
            ]
        )
        await session.commit()

    response = await async_client.get(
        "/api/reports",
        params={"start_date": "2026-06-01", "end_date": "2026-06-01"},
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["summary"]["totalRequests"] == 1
    assert payload["summary"]["totalCostUsd"] == 0.5
    assert payload["daily"][0]["date"] == "2026-06-01"


async def test_reports_api_excludes_limit_warmup_logs(async_client, db_setup):
    start_at = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
    async with SessionLocal() as session:
        session.add(_make_account("acc_reports_warmup", "reports-warmup@example.com"))
        session.add_all(
            [
                RequestLog(
                    account_id="acc_reports_warmup",
                    request_id="report-normal-traffic",
                    requested_at=start_at,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=6,
                    output_tokens=4,
                    cached_input_tokens=0,
                    cost_usd=0.4,
                    source=None,
                ),
                RequestLog(
                    account_id="acc_reports_warmup",
                    request_id="report-warmup-traffic",
                    requested_at=start_at,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=60,
                    output_tokens=40,
                    cached_input_tokens=0,
                    cost_usd=4.0,
                    source="limit_warmup",
                ),
            ]
        )
        await session.commit()

    response = await async_client.get(
        "/api/reports",
        params={"start_date": "2026-06-01", "end_date": "2026-06-01"},
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["summary"]["totalRequests"] == 1
    assert payload["summary"]["totalInputTokens"] == 6
    assert payload["summary"]["totalCostUsd"] == 0.4


async def test_reports_api_applies_account_and_model_filters(async_client, db_setup):
    start_at = datetime(2026, 6, 1, 13, 0, 0, tzinfo=timezone.utc)
    async with SessionLocal() as session:
        session.add_all(
            [
                _make_account("acc_reports_filter_a", "reports-filter-a@example.com"),
                _make_account("acc_reports_filter_b", "reports-filter-b@example.com"),
            ]
        )
        session.add_all(
            [
                RequestLog(
                    account_id="acc_reports_filter_a",
                    request_id="report-filter-match",
                    requested_at=start_at,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=8,
                    output_tokens=2,
                    cached_input_tokens=0,
                    cost_usd=0.8,
                ),
                RequestLog(
                    account_id="acc_reports_filter_a",
                    request_id="report-filter-wrong-model",
                    requested_at=start_at,
                    model="gpt-5.2",
                    status="success",
                    input_tokens=9,
                    output_tokens=2,
                    cached_input_tokens=0,
                    cost_usd=0.9,
                ),
                RequestLog(
                    account_id="acc_reports_filter_b",
                    request_id="report-filter-wrong-account",
                    requested_at=start_at,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=10,
                    output_tokens=2,
                    cached_input_tokens=0,
                    cost_usd=1.0,
                ),
            ]
        )
        await session.commit()

    response = await async_client.get(
        "/api/reports",
        params={
            "start_date": "2026-06-01",
            "end_date": "2026-06-01",
            "account_id": "acc_reports_filter_a",
            "model": "gpt-5.1",
        },
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["summary"]["totalRequests"] == 1
    assert payload["summary"]["totalCostUsd"] == 0.8
    assert payload["byAccount"] == [
        {
            "accountId": "acc_reports_filter_a",
            "alias": None,
            "costUsd": 0.8,
            "requests": 1,
        }
    ]
    assert payload["byModel"] == [{"model": "gpt-5.1", "costUsd": 0.8, "percentage": 100.0}]


async def test_reports_api_summary_counts_range_accounts_and_calendar_days(async_client, db_setup):
    async with SessionLocal() as session:
        session.add_all(
            [
                _make_account("acc_reports_sparse_a", "reports-sparse-a@example.com"),
                _make_account("acc_reports_sparse_b", "reports-sparse-b@example.com"),
            ]
        )
        session.add_all(
            [
                RequestLog(
                    account_id="acc_reports_sparse_a",
                    request_id="report-sparse-a",
                    requested_at=datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=5,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=0.5,
                ),
                RequestLog(
                    account_id="acc_reports_sparse_b",
                    request_id="report-sparse-b",
                    requested_at=datetime(2026, 6, 3, 10, 0, 0, tzinfo=timezone.utc),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=5,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=1.0,
                ),
            ]
        )
        await session.commit()

    response = await async_client.get(
        "/api/reports",
        params={"start_date": "2026-06-01", "end_date": "2026-06-03"},
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["summary"]["activeAccounts"] == 2
    assert payload["summary"]["avgCostPerDay"] == 0.5
    assert payload["summary"]["avgRequestsPerDay"] == 0.67

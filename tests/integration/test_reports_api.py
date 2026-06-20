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
        last_refresh=datetime.now(timezone.utc).replace(tzinfo=None),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


def _naive_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=None)


async def test_reports_api_returns_null_account_bucket(async_client, db_setup):
    start_at = _naive_utc(datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc))
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


async def test_reports_api_rejects_oversized_date_ranges(async_client, db_setup):
    response = await async_client.get(
        "/api/reports",
        params={
            "start_date": "2024-01-01",
            "end_date": "2026-01-01",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "report date range must be 730 days or less"


async def test_reports_api_rejects_oversized_date_ranges_with_default_end_date(async_client, db_setup):
    response = await async_client.get(
        "/api/reports",
        params={
            "start_date": "2020-01-01",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "report date range must be 730 days or less"


async def test_reports_api_includes_preserved_deleted_account_history(async_client, db_setup):
    start_at = _naive_utc(datetime(2026, 6, 1, 11, 0, 0, tzinfo=timezone.utc))
    deleted_at = _naive_utc(datetime(2026, 6, 2, 9, 0, 0, tzinfo=timezone.utc))
    async with SessionLocal() as session:
        session.add(
            RequestLog(
                account_id=None,
                request_id="report-deleted-account-history",
                requested_at=start_at,
                model="gpt-5.1",
                status="success",
                input_tokens=13,
                output_tokens=7,
                cached_input_tokens=3,
                cost_usd=0.42,
                deleted_at=deleted_at,
            )
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
    assert payload["summary"]["totalRequests"] == 1
    assert payload["summary"]["totalInputTokens"] == 13
    assert payload["summary"]["totalOutputTokens"] == 7
    assert payload["summary"]["totalCostUsd"] == 0.42
    assert payload["daily"] == [
        {
            "activeAccounts": 0,
            "costUsd": 0.42,
            "cachedInputTokens": 3,
            "date": start_at.date().isoformat(),
            "errorCount": 0,
            "requests": 1,
            "inputTokens": 13,
            "outputTokens": 7,
        }
    ]
    assert payload["byModel"] == [{"model": "gpt-5.1", "costUsd": 0.42, "requests": 1, "percentage": 100.0}]
    assert payload["byAccount"] == [
        {
            "accountId": None,
            "alias": None,
            "costUsd": 0.42,
            "requests": 1,
        }
    ]


async def test_reports_api_includes_end_date_until_next_midnight(async_client, db_setup):
    end_day_last_second = _naive_utc(datetime(2026, 6, 1, 23, 59, 59, tzinfo=timezone.utc))
    next_day_midnight = _naive_utc(datetime(2026, 6, 2, 0, 0, 0, tzinfo=timezone.utc))
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


async def test_reports_api_interprets_dates_in_requested_timezone(async_client, db_setup):
    before_local_day = _naive_utc(datetime(2026, 6, 1, 6, 59, 59, tzinfo=timezone.utc))
    local_day_start = _naive_utc(datetime(2026, 6, 1, 7, 0, 0, tzinfo=timezone.utc))
    local_day_end = _naive_utc(datetime(2026, 6, 2, 6, 59, 59, tzinfo=timezone.utc))
    after_local_day = _naive_utc(datetime(2026, 6, 2, 7, 0, 0, tzinfo=timezone.utc))
    async with SessionLocal() as session:
        session.add(_make_account("acc_reports_tz", "reports-tz@example.com"))
        session.add_all(
            [
                RequestLog(
                    account_id="acc_reports_tz",
                    request_id="report-tz-before",
                    requested_at=before_local_day,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=1,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=0.1,
                ),
                RequestLog(
                    account_id="acc_reports_tz",
                    request_id="report-tz-start",
                    requested_at=local_day_start,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=2,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=0.2,
                ),
                RequestLog(
                    account_id="acc_reports_tz",
                    request_id="report-tz-end",
                    requested_at=local_day_end,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=3,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=0.3,
                ),
                RequestLog(
                    account_id="acc_reports_tz",
                    request_id="report-tz-after",
                    requested_at=after_local_day,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=4,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=0.4,
                ),
            ]
        )
        await session.commit()

    response = await async_client.get(
        "/api/reports",
        params={
            "start_date": "2026-06-01",
            "end_date": "2026-06-01",
            "timezone": "America/Los_Angeles",
        },
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["summary"]["totalRequests"] == 2
    assert payload["summary"]["totalInputTokens"] == 5
    assert payload["summary"]["totalCostUsd"] == 0.5
    assert payload["daily"] == [
        {
            "activeAccounts": 1,
            "costUsd": 0.5,
            "cachedInputTokens": 0,
            "date": "2026-06-01",
            "errorCount": 0,
            "requests": 2,
            "inputTokens": 5,
            "outputTokens": 2,
        }
    ]


async def test_reports_api_falls_back_to_utc_for_invalid_timezone(async_client, db_setup):
    utc_day_end = _naive_utc(datetime(2026, 6, 1, 23, 59, 59, tzinfo=timezone.utc))
    next_utc_midnight = _naive_utc(datetime(2026, 6, 2, 0, 0, 0, tzinfo=timezone.utc))
    async with SessionLocal() as session:
        session.add(_make_account("acc_reports_bad_tz", "reports-bad-tz@example.com"))
        session.add_all(
            [
                RequestLog(
                    account_id="acc_reports_bad_tz",
                    request_id="report-bad-tz-included",
                    requested_at=utc_day_end,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=7,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=0.7,
                ),
                RequestLog(
                    account_id="acc_reports_bad_tz",
                    request_id="report-bad-tz-excluded",
                    requested_at=next_utc_midnight,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=8,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=0.8,
                ),
            ]
        )
        await session.commit()

    response = await async_client.get(
        "/api/reports",
        params={
            "start_date": "2026-06-01",
            "end_date": "2026-06-01",
            "timezone": "Mars/Olympus_Mons",
        },
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["summary"]["totalRequests"] == 1
    assert payload["summary"]["totalInputTokens"] == 7
    assert payload["summary"]["totalCostUsd"] == 0.7
    assert payload["daily"][0]["date"] == "2026-06-01"


async def test_reports_api_falls_back_to_utc_for_malformed_timezone(async_client, db_setup):
    utc_day_end = _naive_utc(datetime(2026, 6, 1, 23, 59, 59, tzinfo=timezone.utc))
    next_utc_midnight = _naive_utc(datetime(2026, 6, 2, 0, 0, 0, tzinfo=timezone.utc))
    async with SessionLocal() as session:
        session.add(_make_account("acc_reports_malformed_tz", "reports-malformed-tz@example.com"))
        session.add_all(
            [
                RequestLog(
                    account_id="acc_reports_malformed_tz",
                    request_id="report-malformed-tz-included",
                    requested_at=utc_day_end,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=7,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=0.7,
                ),
                RequestLog(
                    account_id="acc_reports_malformed_tz",
                    request_id="report-malformed-tz-excluded",
                    requested_at=next_utc_midnight,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=8,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=0.8,
                ),
            ]
        )
        await session.commit()

    response = await async_client.get(
        "/api/reports",
        params={
            "start_date": "2026-06-01",
            "end_date": "2026-06-01",
            "timezone": "../etc/passwd",
        },
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["summary"]["totalRequests"] == 1
    assert payload["summary"]["totalInputTokens"] == 7
    assert payload["summary"]["totalCostUsd"] == 0.7
    assert payload["daily"][0]["date"] == "2026-06-01"


async def test_reports_api_default_range_uses_last_seven_calendar_days(async_client, db_setup, monkeypatch):
    fixed_now = datetime(2026, 6, 8, 10, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.modules.reports.service.utcnow", lambda: fixed_now)
    async with SessionLocal() as session:
        session.add(_make_account("acc_reports_default", "reports-default@example.com"))
        session.add_all(
            [
                RequestLog(
                    account_id="acc_reports_default",
                    request_id="report-default-old",
                    requested_at=_naive_utc(datetime(2026, 6, 1, 23, 59, 59, tzinfo=timezone.utc)),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=99,
                    output_tokens=99,
                    cached_input_tokens=0,
                    cost_usd=9.9,
                ),
                RequestLog(
                    account_id="acc_reports_default",
                    request_id="report-default-start",
                    requested_at=_naive_utc(datetime(2026, 6, 2, 0, 0, 0, tzinfo=timezone.utc)),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=5,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=0.7,
                ),
                RequestLog(
                    account_id="acc_reports_default",
                    request_id="report-default-end",
                    requested_at=_naive_utc(datetime(2026, 6, 8, 23, 59, 59, tzinfo=timezone.utc)),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=5,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=1.4,
                ),
                RequestLog(
                    account_id="acc_reports_default",
                    request_id="report-default-future",
                    requested_at=_naive_utc(datetime(2026, 6, 9, 0, 0, 0, tzinfo=timezone.utc)),
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

    response = await async_client.get("/api/reports")
    assert response.status_code == 200

    payload = response.json()
    assert payload["summary"]["totalRequests"] == 2
    assert payload["summary"]["avgRequestsPerDay"] == 0.29
    assert payload["daily"][0]["date"] == "2026-06-02"
    assert payload["daily"][1]["date"] == "2026-06-08"


async def test_reports_api_default_range_uses_last_seven_calendar_days_in_requested_timezone(
    async_client, db_setup, monkeypatch
):
    fixed_now = datetime(2026, 6, 8, 1, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("app.modules.reports.service.utcnow", lambda: fixed_now)
    async with SessionLocal() as session:
        session.add(_make_account("acc_reports_default_tz", "reports-default-tz@example.com"))
        session.add_all(
            [
                RequestLog(
                    account_id="acc_reports_default_tz",
                    request_id="report-default-tz-old",
                    requested_at=_naive_utc(datetime(2026, 6, 1, 6, 59, 59, tzinfo=timezone.utc)),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=99,
                    output_tokens=99,
                    cached_input_tokens=0,
                    cost_usd=9.9,
                ),
                RequestLog(
                    account_id="acc_reports_default_tz",
                    request_id="report-default-tz-start",
                    requested_at=_naive_utc(datetime(2026, 6, 1, 7, 0, 0, tzinfo=timezone.utc)),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=5,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=0.7,
                ),
                RequestLog(
                    account_id="acc_reports_default_tz",
                    request_id="report-default-tz-end",
                    requested_at=_naive_utc(datetime(2026, 6, 8, 6, 59, 59, tzinfo=timezone.utc)),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=5,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=1.4,
                ),
                RequestLog(
                    account_id="acc_reports_default_tz",
                    request_id="report-default-tz-future",
                    requested_at=_naive_utc(datetime(2026, 6, 8, 7, 0, 0, tzinfo=timezone.utc)),
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
        params={"timezone": "America/Los_Angeles"},
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["summary"]["totalRequests"] == 2
    assert payload["summary"]["avgRequestsPerDay"] == 0.29
    assert payload["summary"]["totalCostUsd"] == 2.1
    assert payload["daily"] == [
        {
            "activeAccounts": 1,
            "costUsd": 0.7,
            "cachedInputTokens": 0,
            "date": "2026-06-01",
            "errorCount": 0,
            "requests": 1,
            "inputTokens": 5,
            "outputTokens": 1,
        },
        {
            "activeAccounts": 1,
            "costUsd": 1.4,
            "cachedInputTokens": 0,
            "date": "2026-06-07",
            "errorCount": 0,
            "requests": 1,
            "inputTokens": 5,
            "outputTokens": 1,
        },
    ]


async def test_reports_api_uses_dst_aware_boundaries_for_requested_timezone(async_client, db_setup):
    before_local_day = _naive_utc(datetime(2026, 3, 8, 4, 59, 59, tzinfo=timezone.utc))
    local_day_start = _naive_utc(datetime(2026, 3, 8, 5, 0, 0, tzinfo=timezone.utc))
    local_day_end = _naive_utc(datetime(2026, 3, 9, 3, 59, 59, tzinfo=timezone.utc))
    after_local_day = _naive_utc(datetime(2026, 3, 9, 4, 0, 0, tzinfo=timezone.utc))
    async with SessionLocal() as session:
        session.add(_make_account("acc_reports_dst", "reports-dst@example.com"))
        session.add_all(
            [
                RequestLog(
                    account_id="acc_reports_dst",
                    request_id="report-dst-before",
                    requested_at=before_local_day,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=1,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=0.1,
                ),
                RequestLog(
                    account_id="acc_reports_dst",
                    request_id="report-dst-start",
                    requested_at=local_day_start,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=2,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=0.2,
                ),
                RequestLog(
                    account_id="acc_reports_dst",
                    request_id="report-dst-end",
                    requested_at=local_day_end,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=3,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=0.3,
                ),
                RequestLog(
                    account_id="acc_reports_dst",
                    request_id="report-dst-after",
                    requested_at=after_local_day,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=4,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=0.4,
                ),
            ]
        )
        await session.commit()

    response = await async_client.get(
        "/api/reports",
        params={
            "start_date": "2026-03-08",
            "end_date": "2026-03-08",
            "timezone": "America/New_York",
        },
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["summary"]["totalRequests"] == 2
    assert payload["summary"]["totalInputTokens"] == 5
    assert payload["summary"]["totalCostUsd"] == 0.5
    assert payload["daily"] == [
        {
            "activeAccounts": 1,
            "costUsd": 0.5,
            "cachedInputTokens": 0,
            "date": "2026-03-08",
            "errorCount": 0,
            "requests": 2,
            "inputTokens": 5,
            "outputTokens": 2,
        }
    ]


async def test_reports_api_returns_previous_window_comparison_for_complete_history(async_client, db_setup):
    async with SessionLocal() as session:
        session.add(_make_account("acc_reports_compare", "reports-compare@example.com"))
        session.add_all(
            [
                RequestLog(
                    account_id="acc_reports_compare",
                    request_id="report-compare-prev-1",
                    requested_at=_naive_utc(datetime(2026, 5, 28, 0, 0, 0, tzinfo=timezone.utc)),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=10,
                    output_tokens=2,
                    cached_input_tokens=0,
                    cost_usd=0.4,
                ),
                RequestLog(
                    account_id="acc_reports_compare",
                    request_id="report-compare-current-1",
                    requested_at=_naive_utc(datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=20,
                    output_tokens=4,
                    cached_input_tokens=0,
                    cost_usd=0.8,
                ),
            ]
        )
        await session.commit()

    response = await async_client.get(
        "/api/reports",
        params={
            "start_date": "2026-06-05",
            "end_date": "2026-06-12",
            "timezone": "UTC",
        },
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["comparison"] == {
        "canCompare": True,
        "previous": {
            "totalCostUsd": 0.4,
            "totalTokens": 12,
            "totalRequests": 1,
        },
    }


async def test_reports_api_suppresses_comparison_when_previous_window_is_incomplete(async_client, db_setup):
    async with SessionLocal() as session:
        session.add(_make_account("acc_reports_incomplete", "reports-incomplete@example.com"))
        session.add(
            RequestLog(
                account_id="acc_reports_incomplete",
                request_id="report-incomplete-prev-1",
                requested_at=_naive_utc(datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)),
                model="gpt-5.1",
                status="success",
                input_tokens=7,
                output_tokens=2,
                cached_input_tokens=0,
                cost_usd=0.3,
            )
        )
        session.add(
            RequestLog(
                account_id="acc_reports_incomplete",
                request_id="report-incomplete-current-1",
                requested_at=_naive_utc(datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)),
                model="gpt-5.1",
                status="success",
                input_tokens=9,
                output_tokens=3,
                cached_input_tokens=0,
                cost_usd=0.6,
            )
        )
        await session.commit()

    response = await async_client.get(
        "/api/reports",
        params={
            "start_date": "2026-06-05",
            "end_date": "2026-06-12",
            "timezone": "UTC",
        },
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["comparison"] == {
        "canCompare": False,
        "previous": {
            "totalCostUsd": 0.3,
            "totalTokens": 9,
            "totalRequests": 1,
        },
    }


async def test_reports_api_comparison_uses_requested_timezone_boundaries(async_client, db_setup):
    async with SessionLocal() as session:
        session.add(_make_account("acc_reports_compare_tz", "reports-compare-tz@example.com"))
        session.add_all(
            [
                RequestLog(
                    account_id="acc_reports_compare_tz",
                    request_id="report-compare-tz-prev",
                    requested_at=_naive_utc(datetime(2026, 5, 27, 16, 0, 0, tzinfo=timezone.utc)),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=5,
                    output_tokens=5,
                    cached_input_tokens=0,
                    cost_usd=0.25,
                ),
                RequestLog(
                    account_id="acc_reports_compare_tz",
                    request_id="report-compare-tz-current",
                    requested_at=_naive_utc(datetime(2026, 6, 5, 16, 0, 0, tzinfo=timezone.utc)),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=8,
                    output_tokens=4,
                    cached_input_tokens=0,
                    cost_usd=0.5,
                ),
            ]
        )
        await session.commit()

    response = await async_client.get(
        "/api/reports",
        params={
            "start_date": "2026-06-05",
            "end_date": "2026-06-12",
            "timezone": "Asia/Hong_Kong",
        },
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["comparison"] == {
        "canCompare": True,
        "previous": {
            "totalCostUsd": 0.25,
            "totalTokens": 10,
            "totalRequests": 1,
        },
    }


async def test_reports_api_comparison_completeness_honors_active_filters(async_client, db_setup):
    async with SessionLocal() as session:
        session.add_all(
            [
                _make_account("acc_reports_compare_filters", "reports-compare-filters@example.com"),
                _make_account("acc_reports_compare_other", "reports-compare-other@example.com"),
            ]
        )
        session.add_all(
            [
                RequestLog(
                    account_id="acc_reports_compare_other",
                    request_id="report-compare-filter-other-account-prev",
                    requested_at=_naive_utc(datetime(2026, 5, 28, 0, 0, 0, tzinfo=timezone.utc)),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=10,
                    output_tokens=2,
                    cached_input_tokens=0,
                    cost_usd=0.4,
                ),
                RequestLog(
                    account_id="acc_reports_compare_filters",
                    request_id="report-compare-filter-wrong-model-prev",
                    requested_at=_naive_utc(datetime(2026, 5, 28, 0, 0, 0, tzinfo=timezone.utc)),
                    model="gpt-5.2",
                    status="success",
                    input_tokens=9,
                    output_tokens=3,
                    cached_input_tokens=0,
                    cost_usd=0.3,
                ),
                RequestLog(
                    account_id="acc_reports_compare_filters",
                    request_id="report-compare-filter-selected-prev",
                    requested_at=_naive_utc(datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=7,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=0.2,
                ),
                RequestLog(
                    account_id="acc_reports_compare_filters",
                    request_id="report-compare-filter-selected-current",
                    requested_at=_naive_utc(datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=11,
                    output_tokens=2,
                    cached_input_tokens=0,
                    cost_usd=0.6,
                ),
            ]
        )
        await session.commit()

    response = await async_client.get(
        "/api/reports",
        params={
            "start_date": "2026-06-05",
            "end_date": "2026-06-12",
            "timezone": "UTC",
            "account_id": "acc_reports_compare_filters",
            "model": "gpt-5.1",
        },
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["comparison"] == {
        "canCompare": False,
        "previous": {
            "totalCostUsd": 0.2,
            "totalTokens": 8,
            "totalRequests": 1,
        },
    }


async def test_reports_api_comparison_ignores_warmup_traffic_for_coverage(async_client, db_setup):
    async with SessionLocal() as session:
        session.add(_make_account("acc_reports_compare_warmup", "reports-compare-warmup@example.com"))
        session.add_all(
            [
                RequestLog(
                    account_id="acc_reports_compare_warmup",
                    request_id="report-compare-warmup-prev",
                    requested_at=_naive_utc(datetime(2026, 5, 28, 0, 0, 0, tzinfo=timezone.utc)),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=50,
                    output_tokens=10,
                    cached_input_tokens=0,
                    cost_usd=5.0,
                    source="limit_warmup",
                ),
                RequestLog(
                    account_id="acc_reports_compare_warmup",
                    request_id="report-compare-normal-prev",
                    requested_at=_naive_utc(datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=6,
                    output_tokens=2,
                    cached_input_tokens=0,
                    cost_usd=0.3,
                ),
                RequestLog(
                    account_id="acc_reports_compare_warmup",
                    request_id="report-compare-normal-current",
                    requested_at=_naive_utc(datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=12,
                    output_tokens=3,
                    cached_input_tokens=0,
                    cost_usd=0.7,
                ),
            ]
        )
        await session.commit()

    response = await async_client.get(
        "/api/reports",
        params={
            "start_date": "2026-06-05",
            "end_date": "2026-06-12",
            "timezone": "UTC",
            "account_id": "acc_reports_compare_warmup",
            "model": "gpt-5.1",
        },
    )
    assert response.status_code == 200

    payload = response.json()
    assert payload["comparison"] == {
        "canCompare": False,
        "previous": {
            "totalCostUsd": 0.3,
            "totalTokens": 8,
            "totalRequests": 1,
        },
    }


async def test_reports_api_excludes_warmup_logs(async_client, db_setup):
    start_at = _naive_utc(datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc))
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
                    request_id="report-warmup-source-traffic",
                    requested_at=start_at,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=60,
                    output_tokens=40,
                    cached_input_tokens=0,
                    cost_usd=4.0,
                    source="limit_warmup",
                ),
                RequestLog(
                    account_id="acc_reports_warmup",
                    request_id="report-warmup-kind-traffic",
                    requested_at=start_at,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=70,
                    output_tokens=50,
                    cached_input_tokens=0,
                    cost_usd=5.0,
                    source=None,
                    request_kind="warmup",
                ),
                RequestLog(
                    account_id="acc_reports_warmup",
                    request_id="report-limit-warmup-kind-traffic",
                    requested_at=start_at,
                    model="gpt-5.1",
                    status="success",
                    input_tokens=80,
                    output_tokens=60,
                    cached_input_tokens=0,
                    cost_usd=6.0,
                    source=None,
                    request_kind="limit_warmup",
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
    assert payload["byModel"] == [{"model": "gpt-5.1", "costUsd": 0.4, "requests": 1, "percentage": 100.0}]
    assert payload["byAccount"] == [
        {
            "accountId": "acc_reports_warmup",
            "alias": None,
            "costUsd": 0.4,
            "requests": 1,
        }
    ]


async def test_reports_api_applies_account_and_model_filters(async_client, db_setup):
    start_at = _naive_utc(datetime(2026, 6, 1, 13, 0, 0, tzinfo=timezone.utc))
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
    assert payload["byModel"] == [{"model": "gpt-5.1", "costUsd": 0.8, "requests": 1, "percentage": 100.0}]


async def test_reports_api_includes_unpriced_models_in_model_breakdown(async_client, db_setup):
    start_at = _naive_utc(datetime(2026, 6, 1, 14, 0, 0, tzinfo=timezone.utc))
    async with SessionLocal() as session:
        session.add(_make_account("acc_reports_unpriced", "reports-unpriced@example.com"))
        session.add_all(
            [
                RequestLog(
                    account_id="acc_reports_unpriced",
                    request_id="report-priced-model",
                    requested_at=start_at,
                    model="gpt-priced",
                    status="success",
                    input_tokens=8,
                    output_tokens=2,
                    cached_input_tokens=0,
                    cost_usd=0.8,
                ),
                RequestLog(
                    account_id="acc_reports_unpriced",
                    request_id="report-unpriced-model",
                    requested_at=start_at,
                    model="gpt-unpriced",
                    status="success",
                    input_tokens=9,
                    output_tokens=2,
                    cached_input_tokens=0,
                    cost_usd=None,
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
    assert payload["summary"]["totalRequests"] == 2
    assert payload["byModel"] == [
        {"model": "gpt-priced", "costUsd": 0.8, "requests": 1, "percentage": 100.0},
        {"model": "gpt-unpriced", "costUsd": 0.0, "requests": 1, "percentage": 0.0},
    ]
    assert payload["byAccount"] == [
        {
            "accountId": "acc_reports_unpriced",
            "alias": None,
            "costUsd": 0.8,
            "requests": 2,
        }
    ]


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
                    requested_at=_naive_utc(datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)),
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
                    requested_at=_naive_utc(datetime(2026, 6, 3, 10, 0, 0, tzinfo=timezone.utc)),
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


async def test_reports_api_supports_useragent_group_filter_and_breakdown(async_client, db_setup):
    start_at = _naive_utc(datetime(2026, 6, 1, 16, 0, 0, tzinfo=timezone.utc))
    async with SessionLocal() as session:
        session.add(_make_account("acc_reports_useragent", "reports-useragent@example.com"))
        session.add_all(
            [
                RequestLog(
                    account_id="acc_reports_useragent",
                    request_id="report-useragent-match-1",
                    requested_at=start_at,
                    model="gpt-5.1",
                    useragent_group="opencode",
                    status="success",
                    input_tokens=8,
                    output_tokens=2,
                    cached_input_tokens=0,
                    cost_usd=0.8,
                ),
                RequestLog(
                    account_id="acc_reports_useragent",
                    request_id="report-useragent-match-2",
                    requested_at=start_at,
                    model="gpt-5.2",
                    useragent_group="CodexCLI",
                    status="success",
                    input_tokens=7,
                    output_tokens=2,
                    cached_input_tokens=0,
                    cost_usd=0.7,
                ),
                RequestLog(
                    account_id="acc_reports_useragent",
                    request_id="report-useragent-real-unknown",
                    requested_at=start_at,
                    model="gpt-5.0",
                    useragent_group="Unknown",
                    status="success",
                    input_tokens=7,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=0.4,
                ),
                RequestLog(
                    account_id="acc_reports_useragent",
                    request_id="report-useragent-blank",
                    requested_at=start_at,
                    model="gpt-5.3",
                    useragent_group="",
                    status="success",
                    input_tokens=6,
                    output_tokens=2,
                    cached_input_tokens=0,
                    cost_usd=0.6,
                ),
                RequestLog(
                    account_id="acc_reports_useragent",
                    request_id="report-useragent-null",
                    requested_at=start_at,
                    model="gpt-5.4",
                    useragent_group=None,
                    status="success",
                    input_tokens=5,
                    output_tokens=2,
                    cached_input_tokens=0,
                    cost_usd=0.5,
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
    assert payload["byUseragent"] == [
        {"useragent": "opencode", "costUsd": 0.8, "requests": 1, "percentage": 33.3},
        {"useragent": "CodexCLI", "costUsd": 0.7, "requests": 1, "percentage": 29.2},
        {"useragent": "Missing User-Agent", "costUsd": 0.5, "requests": 1, "percentage": 20.8},
        {"useragent": "Unknown", "costUsd": 0.4, "requests": 1, "percentage": 16.7},
    ]

    filtered_response = await async_client.get(
        "/api/reports",
        params={
            "start_date": "2026-06-01",
            "end_date": "2026-06-01",
            "useragent_group": "opencode",
        },
    )
    assert filtered_response.status_code == 200

    filtered_payload = filtered_response.json()
    assert filtered_payload["summary"]["totalRequests"] == 1
    assert filtered_payload["summary"]["totalCostUsd"] == 0.8
    assert filtered_payload["byModel"] == [{"model": "gpt-5.1", "costUsd": 0.8, "requests": 1, "percentage": 100.0}]
    assert filtered_payload["byUseragent"] == [
        {"useragent": "opencode", "costUsd": 0.8, "requests": 1, "percentage": 100.0}
    ]

    unknown_filtered_response = await async_client.get(
        "/api/reports",
        params={
            "start_date": "2026-06-01",
            "end_date": "2026-06-01",
            "useragent_group": "Unknown",
        },
    )
    assert unknown_filtered_response.status_code == 200

    unknown_filtered_payload = unknown_filtered_response.json()
    assert unknown_filtered_payload["summary"]["totalRequests"] == 1
    assert unknown_filtered_payload["summary"]["totalCostUsd"] == 0.4
    assert unknown_filtered_payload["byModel"] == [
        {"model": "gpt-5.0", "costUsd": 0.4, "requests": 1, "percentage": 100.0}
    ]
    assert unknown_filtered_payload["byUseragent"] == [
        {"useragent": "Unknown", "costUsd": 0.4, "requests": 1, "percentage": 100.0}
    ]

    missing_useragent_filtered_response = await async_client.get(
        "/api/reports",
        params={
            "start_date": "2026-06-01",
            "end_date": "2026-06-01",
            "useragent_group": "Missing User-Agent",
        },
    )
    assert missing_useragent_filtered_response.status_code == 200

    missing_useragent_filtered_payload = missing_useragent_filtered_response.json()
    assert missing_useragent_filtered_payload["summary"]["totalRequests"] == 1
    assert missing_useragent_filtered_payload["summary"]["totalCostUsd"] == 0.5
    assert missing_useragent_filtered_payload["byModel"] == [
        {"model": "gpt-5.4", "costUsd": 0.5, "requests": 1, "percentage": 100.0}
    ]
    assert missing_useragent_filtered_payload["byUseragent"] == [
        {"useragent": "Missing User-Agent", "costUsd": 0.5, "requests": 1, "percentage": 100.0}
    ]


async def test_reports_api_summary_uses_sql_range_totals_not_rounded_daily_rows(async_client, db_setup):
    async with SessionLocal() as session:
        session.add(_make_account("acc_reports_summary_sql", "reports-summary-sql@example.com"))
        session.add_all(
            [
                RequestLog(
                    account_id="acc_reports_summary_sql",
                    request_id="report-summary-sql-1",
                    requested_at=_naive_utc(datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=1,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=0.00004,
                ),
                RequestLog(
                    account_id="acc_reports_summary_sql",
                    request_id="report-summary-sql-2",
                    requested_at=_naive_utc(datetime(2026, 6, 2, 10, 0, 0, tzinfo=timezone.utc)),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=1,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=0.00004,
                ),
                RequestLog(
                    account_id="acc_reports_summary_sql",
                    request_id="report-summary-sql-3",
                    requested_at=_naive_utc(datetime(2026, 6, 3, 10, 0, 0, tzinfo=timezone.utc)),
                    model="gpt-5.1",
                    status="success",
                    input_tokens=1,
                    output_tokens=1,
                    cached_input_tokens=0,
                    cost_usd=0.00004,
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
    assert payload["summary"]["totalCostUsd"] == 0.0001
    assert payload["summary"]["avgCostPerDay"] == 0.0
    assert payload["daily"] == [
        {
            "activeAccounts": 1,
            "costUsd": 0.0,
            "cachedInputTokens": 0,
            "date": "2026-06-01",
            "errorCount": 0,
            "requests": 1,
            "inputTokens": 1,
            "outputTokens": 1,
        },
        {
            "activeAccounts": 1,
            "costUsd": 0.0,
            "cachedInputTokens": 0,
            "date": "2026-06-02",
            "errorCount": 0,
            "requests": 1,
            "inputTokens": 1,
            "outputTokens": 1,
        },
        {
            "activeAccounts": 1,
            "costUsd": 0.0,
            "cachedInputTokens": 0,
            "date": "2026-06-03",
            "errorCount": 0,
            "requests": 1,
            "inputTokens": 1,
            "outputTokens": 1,
        },
    ]

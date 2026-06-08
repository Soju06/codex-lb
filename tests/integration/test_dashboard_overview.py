from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

import pytest

from app.core.crypto import TokenEncryptor
from app.core.utils.time import naive_utc_to_epoch, utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.schemas import AccountSummary
from app.modules.dashboard.weekly_pace import _weekly_timing
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration


def _make_account(
    account_id: str,
    email: str,
    plan_type: str = "plus",
    status: AccountStatus = AccountStatus.ACTIVE,
) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email,
        plan_type=plan_type,
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=status,
        deactivation_reason=None,
    )


def test_weekly_credit_pace_timing_treats_naive_reset_as_utc():
    if not hasattr(time, "tzset"):
        pytest.skip("tzset is required to simulate non-UTC local time")

    original_tz = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Seoul"
    time.tzset()
    try:
        fixed_now = datetime(2026, 5, 18, 12, 0, 0)
        reset_at = fixed_now + timedelta(days=4)
        now_ms = naive_utc_to_epoch(fixed_now) * 1000.0
        timing = _weekly_timing(
            AccountSummary(
                account_id="acc_tz",
                email="tz@example.com",
                display_name="tz@example.com",
                plan_type="pro",
                status="active",
                reset_at_secondary=reset_at,
                window_minutes_secondary=10080,
                capacity_credits_secondary=50_400.0,
                remaining_credits_secondary=40_320.0,
            ),
            now_ms,
        )
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time.tzset()

    assert timing is not None
    assert timing[2] == pytest.approx(naive_utc_to_epoch(reset_at) * 1000.0)


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
    assert payload["accounts"][0]["capacityCreditsSecondary"] == pytest.approx(7560.0)
    assert payload["accounts"][0]["remainingCreditsSecondary"] == pytest.approx(4536.0)
    assert payload["timeframe"] == {
        "key": "7d",
        "windowMinutes": 10080,
        "bucketSeconds": 21600,
        "bucketCount": 28,
    }
    assert payload["summary"]["primaryWindow"]["capacityCredits"] == pytest.approx(225.0)
    assert payload["summary"]["cost"]["totalUsd"] == pytest.approx(0.000625)
    assert payload["summary"]["metrics"]["requests"] == 1
    assert payload["summary"]["metrics"]["tokens"] == 150
    assert payload["summary"]["metrics"]["cachedInputTokens"] == 0
    assert payload["summary"]["metrics"]["errorRate"] == pytest.approx(0.0)
    assert payload["summary"]["metrics"]["errorCount"] == 0
    assert payload["windows"]["primary"]["windowKey"] == "primary"
    assert payload["windows"]["secondary"]["windowKey"] == "secondary"
    assert "requestLogs" not in payload
    assert payload["lastSyncAt"] == secondary_time.isoformat() + "Z"

    # Verify trends are present and have 28 data points each
    assert "trends" in payload
    trends = payload["trends"]
    assert len(trends["requests"]) == 28
    assert len(trends["tokens"]) == 28
    assert len(trends["cost"]) == 28
    assert len(trends["errorRate"]) == 28

    # At least one trend point should have non-zero request count
    request_values = [p["v"] for p in trends["requests"]]
    assert any(v > 0 for v in request_values)


@pytest.mark.asyncio
async def test_dashboard_overview_metrics_keep_soft_deleted_request_logs(async_client, db_setup):
    now = utcnow().replace(microsecond=0)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc_dash_deleted", "dash-deleted@example.com"))
        await logs_repo.add_log(
            account_id="acc_dash_deleted",
            request_id="req_dash_deleted_1",
            model="gpt-5.1",
            input_tokens=40,
            output_tokens=10,
            latency_ms=40,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=2),
        )

    delete_response = await async_client.delete("/api/accounts/acc_dash_deleted")
    assert delete_response.status_code == 200

    overview = await async_client.get("/api/dashboard/overview")
    assert overview.status_code == 200
    payload = overview.json()

    assert payload["accounts"] == []
    assert payload["summary"]["metrics"]["requests"] == 1
    assert payload["summary"]["metrics"]["tokens"] == 50
    request_values = [point["v"] for point in payload["trends"]["requests"]]
    assert any(value > 0 for value in request_values)


@pytest.mark.asyncio
async def test_dashboard_overview_maps_weekly_only_primary_to_secondary(async_client, db_setup):
    now = utcnow().replace(microsecond=0)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_plus", "plus@example.com", plan_type="plus"))
        await accounts_repo.upsert(_make_account("acc_free", "free@example.com", plan_type="free"))

        await usage_repo.add_entry(
            "acc_plus",
            20.0,
            window="primary",
            window_minutes=300,
            recorded_at=now - timedelta(minutes=2),
        )
        await usage_repo.add_entry(
            "acc_free",
            20.0,
            window="primary",
            window_minutes=10080,
            recorded_at=now - timedelta(minutes=1),
        )
        await usage_repo.add_entry(
            "acc_plus",
            40.0,
            window="secondary",
            window_minutes=10080,
            recorded_at=now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/dashboard/overview")
    assert response.status_code == 200
    payload = response.json()

    accounts = {item["accountId"]: item for item in payload["accounts"]}

    assert payload["summary"]["primaryWindow"]["windowMinutes"] == 300
    assert payload["windows"]["primary"]["windowMinutes"] == 300
    assert payload["summary"]["secondaryWindow"]["windowMinutes"] == 10080
    assert accounts["acc_free"]["windowMinutesPrimary"] is None
    assert accounts["acc_free"]["windowMinutesSecondary"] == 10080
    assert accounts["acc_free"]["usage"]["secondaryRemainingPercent"] == pytest.approx(80.0)


@pytest.mark.asyncio
async def test_dashboard_overview_exposes_monthly_only_free_account(async_client, db_setup):
    now = utcnow().replace(microsecond=0)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_free_monthly", "free-monthly@example.com", plan_type="free"))
        await usage_repo.add_entry(
            "acc_free_monthly",
            20.0,
            window="monthly",
            window_minutes=43200,
            recorded_at=now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/dashboard/overview")
    assert response.status_code == 200
    payload = response.json()

    accounts = {item["accountId"]: item for item in payload["accounts"]}
    account = accounts["acc_free_monthly"]
    assert payload["lastSyncAt"] == (now - timedelta(minutes=1)).isoformat() + "Z"
    assert account["usage"]["monthlyRemainingPercent"] == pytest.approx(80.0)
    assert account["windowMinutesPrimary"] is None
    assert account["windowMinutesSecondary"] is None
    assert account["windowMinutesMonthly"] == 43200


@pytest.mark.asyncio
async def test_dashboard_overview_derives_quota_status_from_current_weekly_usage(async_client, db_setup):
    now = utcnow().replace(microsecond=0)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_weekly_full", "weekly-full@example.com"))
        await usage_repo.add_entry(
            "acc_weekly_full",
            5.0,
            window="primary",
            window_minutes=300,
            recorded_at=now - timedelta(minutes=2),
        )
        await usage_repo.add_entry(
            "acc_weekly_full",
            100.0,
            window="secondary",
            window_minutes=10080,
            reset_at=int(naive_utc_to_epoch(now + timedelta(days=2))),
            recorded_at=now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/dashboard/overview")
    assert response.status_code == 200
    payload = response.json()

    accounts = {item["accountId"]: item for item in payload["accounts"]}
    account = accounts["acc_weekly_full"]
    assert account["status"] == "quota_exceeded"
    assert account["usage"]["primaryRemainingPercent"] == pytest.approx(95.0)
    assert account["usage"]["secondaryRemainingPercent"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_dashboard_overview_counts_prolite_capacity(async_client, db_setup):
    now = utcnow().replace(microsecond=0)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_prolite", "prolite@example.com", plan_type="prolite"))
        await usage_repo.add_entry(
            "acc_prolite",
            0.0,
            window="primary",
            window_minutes=300,
            recorded_at=now - timedelta(minutes=2),
        )
        await usage_repo.add_entry(
            "acc_prolite",
            0.0,
            window="secondary",
            window_minutes=10080,
            recorded_at=now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/dashboard/overview")
    assert response.status_code == 200
    payload = response.json()
    account = payload["accounts"][0]

    assert account["planType"] == "prolite"
    assert account["capacityCreditsPrimary"] == pytest.approx(1125.0)
    assert account["remainingCreditsPrimary"] == pytest.approx(1125.0)
    assert account["capacityCreditsSecondary"] == pytest.approx(37800.0)
    assert account["remainingCreditsSecondary"] == pytest.approx(37800.0)
    assert payload["summary"]["primaryWindow"]["capacityCredits"] == pytest.approx(1125.0)
    assert payload["summary"]["primaryWindow"]["remainingCredits"] == pytest.approx(1125.0)
    assert payload["summary"]["secondaryWindow"]["capacityCredits"] == pytest.approx(37800.0)
    assert payload["summary"]["secondaryWindow"]["remainingCredits"] == pytest.approx(37800.0)


@pytest.mark.asyncio
async def test_dashboard_projections_weekly_credit_pace_excludes_inactive_and_stale_accounts(
    async_client,
    db_setup,
    monkeypatch: pytest.MonkeyPatch,
):
    fixed_now = datetime(2026, 5, 18, 12, 0, 0)
    monkeypatch.setattr("app.modules.dashboard.service.utcnow", lambda: fixed_now)
    reset_at = int(naive_utc_to_epoch(fixed_now + timedelta(days=4)))

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_active_fresh", "fresh@example.com", plan_type="pro"))
        await accounts_repo.upsert(_make_account("acc_active_stale", "stale@example.com", plan_type="pro"))
        await accounts_repo.upsert(
            _make_account(
                "acc_inactive_fresh",
                "inactive@example.com",
                plan_type="pro",
                status=AccountStatus.DEACTIVATED,
            )
        )

        await usage_repo.add_entry(
            "acc_active_fresh",
            20.0,
            window="secondary",
            window_minutes=10080,
            reset_at=reset_at,
            recorded_at=fixed_now - timedelta(minutes=1),
        )
        await usage_repo.add_entry(
            "acc_active_stale",
            80.0,
            window="secondary",
            window_minutes=10080,
            reset_at=reset_at,
            recorded_at=fixed_now - timedelta(minutes=10),
        )
        await usage_repo.add_entry(
            "acc_inactive_fresh",
            90.0,
            window="secondary",
            window_minutes=10080,
            reset_at=reset_at,
            recorded_at=fixed_now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/dashboard/projections")
    assert response.status_code == 200
    payload = response.json()

    pace = payload["weeklyCreditPace"]
    assert pace["accountCount"] == 1
    assert pace["staleAccountCount"] == 1
    assert pace["inactiveAccountCount"] == 1
    assert pace["totalFullCredits"] == pytest.approx(50_400.0)
    assert pace["actualUsedPercent"] == pytest.approx(20.0)
    assert pace["scheduledUsedPercent"] == pytest.approx(42.857, abs=0.01)
    assert pace["scheduleGapCredits"] == 0
    assert pace["status"] == "behind"


@pytest.mark.asyncio
async def test_dashboard_projections_weekly_credit_pace_forecast_uses_recent_slope_not_full_window_average(
    async_client,
    db_setup,
    monkeypatch: pytest.MonkeyPatch,
):
    fixed_now = datetime(2026, 5, 18, 12, 0, 0)
    monkeypatch.setattr("app.modules.dashboard.service.utcnow", lambda: fixed_now)
    reset_at = int(naive_utc_to_epoch(fixed_now + timedelta(days=5, hours=18)))

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_recent_flat", "flat@example.com", plan_type="pro"))
        await usage_repo.add_entry(
            "acc_recent_flat",
            0.0,
            window="secondary",
            window_minutes=10080,
            reset_at=reset_at,
            recorded_at=fixed_now - timedelta(days=1),
        )
        await usage_repo.add_entry(
            "acc_recent_flat",
            24.0,
            window="secondary",
            window_minutes=10080,
            reset_at=reset_at,
            recorded_at=fixed_now - timedelta(hours=3),
        )
        await usage_repo.add_entry(
            "acc_recent_flat",
            24.0,
            window="secondary",
            window_minutes=10080,
            reset_at=reset_at,
            recorded_at=fixed_now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/dashboard/projections")
    assert response.status_code == 200
    payload = response.json()

    pace = payload["weeklyCreditPace"]
    assert pace["accountCount"] == 1
    assert pace["actualUsedPercent"] == pytest.approx(24.0)
    assert pace["scheduledUsedPercent"] == pytest.approx(17.857, abs=0.01)
    assert pace["scheduleGapCredits"] == pytest.approx(3_096.0, abs=1.0)
    assert pace["projectedShortfallCredits"] == 0
    assert pace["forecastBurnRateCreditsPerHour"] == pytest.approx(0.0)
    assert pace["paceMultiplier"] == pytest.approx(0.0)
    assert pace["pauseForBreakEvenHours"] is None
    assert pace["status"] == "ahead"


@pytest.mark.asyncio
async def test_dashboard_projections_weekly_credit_pace_uses_configured_working_days(
    async_client,
    db_setup,
    monkeypatch: pytest.MonkeyPatch,
):
    fixed_now = datetime(2026, 5, 24, 12, 0, 0)
    monkeypatch.setattr("app.modules.dashboard.service.utcnow", lambda: fixed_now)
    reset_at = int(naive_utc_to_epoch(datetime(2026, 5, 25, 0, 0, 0)))

    settings_response = await async_client.put("/api/settings", json={"weeklyPaceWorkingDays": "0,1,2,3,4"})
    assert settings_response.status_code == 200

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_weekdays", "weekdays@example.com", plan_type="pro"))
        await usage_repo.add_entry(
            "acc_weekdays",
            80.0,
            window="secondary",
            window_minutes=10080,
            reset_at=reset_at,
            recorded_at=fixed_now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/dashboard/projections")
    assert response.status_code == 200
    payload = response.json()

    pace = payload["weeklyCreditPace"]
    assert pace["accountCount"] == 1
    assert pace["actualUsedPercent"] == pytest.approx(80.0)
    assert pace["scheduledUsedPercent"] == pytest.approx(100.0)
    assert pace["scheduleGapCredits"] == 0
    assert pace["status"] == "behind"


@pytest.mark.asyncio
async def test_dashboard_projections_compute_depletion_from_recent_db_history(async_client, db_setup):
    now = utcnow().replace(microsecond=0)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_depletion", "depletion@example.com"))
        await usage_repo.add_entry(
            "acc_depletion",
            10.0,
            window="primary",
            window_minutes=60,
            reset_at=int(naive_utc_to_epoch(now + timedelta(minutes=45))),
            recorded_at=now - timedelta(minutes=20),
        )
        await usage_repo.add_entry(
            "acc_depletion",
            35.0,
            window="primary",
            window_minutes=60,
            reset_at=int(naive_utc_to_epoch(now + timedelta(minutes=45))),
            recorded_at=now - timedelta(minutes=5),
        )

    response = await async_client.get("/api/dashboard/projections")
    assert response.status_code == 200

    payload = response.json()
    assert payload["depletionPrimary"] is not None
    assert 0.0 <= payload["depletionPrimary"]["risk"] <= 1.0
    assert payload["depletionPrimary"]["riskLevel"] in {"safe", "warning", "danger", "critical"}


@pytest.mark.asyncio
async def test_dashboard_projections_weekly_only_depletion_uses_current_stream(async_client, db_setup):
    now = utcnow().replace(microsecond=0)
    reset_at = int(naive_utc_to_epoch(now + timedelta(minutes=30)))

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_weekly_depletion", "weekly@example.com", plan_type="free"))

        await usage_repo.add_entry(
            "acc_weekly_depletion",
            0.0,
            window="secondary",
            window_minutes=10080,
            reset_at=reset_at,
            recorded_at=now - timedelta(days=6, minutes=2),
        )
        await usage_repo.add_entry(
            "acc_weekly_depletion",
            5.0,
            window="secondary",
            window_minutes=10080,
            reset_at=reset_at,
            recorded_at=now - timedelta(days=6, minutes=1),
        )
        await usage_repo.add_entry(
            "acc_weekly_depletion",
            6.0,
            window="primary",
            window_minutes=10080,
            reset_at=reset_at,
            recorded_at=now - timedelta(minutes=2),
        )
        await usage_repo.add_entry(
            "acc_weekly_depletion",
            7.0,
            window="primary",
            window_minutes=10080,
            reset_at=reset_at,
            recorded_at=now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/dashboard/projections")
    assert response.status_code == 200

    payload = response.json()
    assert payload["depletionSecondary"] is not None
    assert payload["depletionSecondary"]["risk"] == pytest.approx(0.37, abs=0.02)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("timeframe", "expected_requests", "expected_bucket_count"),
    [
        ("1d", 1, 24),
        ("30d", 2, 30),
    ],
)
async def test_dashboard_overview_respects_selected_timeframe(
    async_client,
    db_setup,
    timeframe: str,
    expected_requests: int,
    expected_bucket_count: int,
):
    now = utcnow().replace(microsecond=0)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        logs_repo = RequestLogsRepository(session)

        await accounts_repo.upsert(_make_account("acc_timeframe", "timeframe@example.com"))
        await usage_repo.add_entry(
            "acc_timeframe",
            20.0,
            window="primary",
            recorded_at=now - timedelta(minutes=5),
        )
        await usage_repo.add_entry(
            "acc_timeframe",
            40.0,
            window="secondary",
            recorded_at=now - timedelta(minutes=2),
        )
        await logs_repo.add_log(
            account_id="acc_timeframe",
            request_id="req_recent",
            model="gpt-5.1",
            input_tokens=100,
            output_tokens=50,
            latency_ms=50,
            status="success",
            error_code=None,
            requested_at=now - timedelta(hours=3),
        )
        await logs_repo.add_log(
            account_id="acc_timeframe",
            request_id="req_old",
            model="gpt-5.1",
            input_tokens=200,
            output_tokens=100,
            latency_ms=50,
            status="error",
            error_code="rate_limit_exceeded",
            requested_at=now - timedelta(days=2),
        )

    response = await async_client.get(f"/api/dashboard/overview?timeframe={timeframe}")
    assert response.status_code == 200
    payload = response.json()

    assert payload["timeframe"]["key"] == timeframe
    assert payload["timeframe"]["bucketCount"] == expected_bucket_count
    assert len(payload["trends"]["requests"]) == expected_bucket_count
    assert payload["summary"]["metrics"]["requests"] == expected_requests
    if timeframe == "1d":
        assert payload["summary"]["metrics"]["errorCount"] == 0
        assert payload["summary"]["metrics"]["topError"] is None
    else:
        assert payload["summary"]["metrics"]["errorCount"] == 1
        assert payload["summary"]["metrics"]["topError"] == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_dashboard_overview_invalid_timeframe_returns_validation_error(async_client):
    response = await async_client.get("/api/dashboard/overview?timeframe=90d")
    assert response.status_code == 422

    payload = response.json()
    assert payload["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_dashboard_overview_summary_uses_exact_timeframe_even_when_trends_skip_partial_leading_bucket(
    async_client,
    db_setup,
    monkeypatch: pytest.MonkeyPatch,
):
    fixed_now = datetime(2026, 4, 3, 10, 37, 0)
    monkeypatch.setattr("app.modules.dashboard.service.utcnow", lambda: fixed_now)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        logs_repo = RequestLogsRepository(session)

        await accounts_repo.upsert(_make_account("acc_partial", "partial@example.com"))
        await usage_repo.add_entry(
            "acc_partial",
            20.0,
            window="primary",
            recorded_at=fixed_now - timedelta(minutes=5),
        )
        await usage_repo.add_entry(
            "acc_partial",
            40.0,
            window="secondary",
            recorded_at=fixed_now - timedelta(minutes=2),
        )
        await logs_repo.add_log(
            account_id="acc_partial",
            request_id="req_partial_error",
            model="gpt-5.1",
            input_tokens=100,
            output_tokens=50,
            latency_ms=50,
            status="error",
            error_code="rate_limit_exceeded",
            requested_at=fixed_now - timedelta(hours=23, minutes=52),
        )

    response = await async_client.get("/api/dashboard/overview?timeframe=1d")
    assert response.status_code == 200

    payload = response.json()
    assert payload["summary"]["metrics"]["requests"] == 1
    assert payload["summary"]["metrics"]["tokens"] == 150
    assert payload["summary"]["metrics"]["errorCount"] == 1
    assert payload["summary"]["metrics"]["topError"] == "rate_limit_exceeded"
    assert all(point["v"] == 0 for point in payload["trends"]["requests"])

from __future__ import annotations

import os
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.dashboard_users_cache import get_dashboard_users_cache
from app.core.config.settings_cache import get_settings_cache
from app.core.crypto import TokenEncryptor
from app.core.utils.time import naive_utc_to_epoch, utcnow
from app.db.models import Account, AccountStatus, ApiKey, ModelSourcePin, RequestLog
from app.db.session import SessionLocal, engine
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.schemas import AccountSummary
from app.modules.dashboard.weekly_pace import _weekly_timing
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.settings.repository import SettingsRepository
from app.modules.usage.repository import UsageRepository
from tests.integration.statement_capture import capture_task_statements

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
            conversation_id="conv-dash",
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
    assert len(trends["conversations"]) == 28

    # At least one trend point should have non-zero request count
    request_values = [p["v"] for p in trends["requests"]]
    assert any(v > 0 for v in request_values)
    conversation_values = [p["v"] for p in trends["conversations"]]
    assert any(v > 0 for v in conversation_values)


@pytest.mark.asyncio
async def test_dashboard_overview_carries_weekly_runway_fields_and_attribution(
    async_client,
    db_setup,
    monkeypatch: pytest.MonkeyPatch,
):
    fixed_now = datetime(2026, 8, 17, 12, 0, 0)
    monkeypatch.setattr("app.modules.dashboard.service.utcnow", lambda: fixed_now)
    reset_at = int(naive_utc_to_epoch(fixed_now + timedelta(hours=4)))

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        await accounts_repo.upsert(_make_account("acc-runway", "runway@example.com", plan_type="pro"))
        for minutes_ago, used_percent in (
            (170, 70.0),
            (130, 80.0),
            (110, 80.0),
            (70, 90.0),
            (50, 90.0),
            (1, 95.0),
        ):
            await usage_repo.add_entry(
                "acc-runway",
                used_percent,
                window="secondary",
                window_minutes=10_080,
                reset_at=reset_at,
                recorded_at=fixed_now - timedelta(minutes=minutes_ago),
            )
        session.add(ApiKey(id="key-runway", name="Runway key", key_hash="hash-runway", key_prefix="run"))
        session.add(
            RequestLog(
                account_id="acc-runway",
                api_key_id="key-runway",
                request_id="runway-request",
                requested_at=fixed_now - timedelta(minutes=10),
                model="gpt-5.1",
                status="success",
                input_tokens=100,
                output_tokens=25,
                reasoning_tokens=10,
                cached_input_tokens=20,
            )
        )
        await session.commit()

    response = await async_client.get("/api/dashboard/overview")

    assert response.status_code == 200
    pace = response.json()["weeklyCreditPace"]
    assert pace is not None
    assert pace["headroomPercent"] == pytest.approx(5.0)
    assert pace["headroomCredits"] == pytest.approx(2_520.0)
    assert pace["burnRateRecentCreditsPerHour"] == pytest.approx(4_473.3727810651)
    assert pace["depletionEtaHours"] == pytest.approx(0.5633333333)
    assert pace["nextReliefInHours"] == pytest.approx(4.0)
    assert pace["nextReliefCredits"] == pytest.approx(47_880.0)
    assert pace["runwayStatus"] == "runs_dry"
    assert pace["status"] == "danger"
    assert pace["saturatedAccountCount"] == 0
    assert pace["addProAccounts"] is None
    assert len(pace["resetEvents"]) == 1
    assert pace["topApiKeys"] == [
        {
            "apiKeyId": "key-runway",
            "name": "Runway key",
            "requests": 1,
            "billableTokens": 125,
            "cachedTokens": 20,
            "dominantModel": "gpt-5.1",
        }
    ]
    assert pace["scheduledUsedPercent"] == pytest.approx(97.619, abs=0.01)


@pytest.mark.asyncio
async def test_dashboard_overview_omits_weekly_pace_value_without_weekly_data(
    async_client,
    db_setup,
):
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(_make_account("acc-no-weekly", "no-weekly@example.com"))

    response = await async_client.get("/api/dashboard/overview")

    assert response.status_code == 200
    assert response.json()["weeklyCreditPace"] is None


@pytest.mark.asyncio
async def test_dashboard_overview_counts_distinct_nonblank_conversations_in_timeframe(
    async_client,
    db_setup,
    monkeypatch: pytest.MonkeyPatch,
):
    now = datetime(2026, 4, 3, 10, 37, 0)
    monkeypatch.setattr("app.modules.dashboard.service.utcnow", lambda: now)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        await accounts_repo.upsert(_make_account("acc_dash_conversations", "dash-conversations@example.com"))
        session.add_all(
            [
                RequestLog(
                    account_id="acc_dash_conversations",
                    request_id="dash-conversation-1",
                    requested_at=now - timedelta(minutes=5),
                    model="gpt-5.1",
                    status="success",
                    conversation_id="conv-a",
                ),
                RequestLog(
                    account_id="acc_dash_conversations",
                    request_id="dash-conversation-2",
                    requested_at=now - timedelta(minutes=4),
                    model="gpt-5.1",
                    status="success",
                    conversation_id="conv-a",
                ),
                RequestLog(
                    account_id="acc_dash_conversations",
                    request_id="dash-conversation-3",
                    requested_at=now - timedelta(minutes=3),
                    model="gpt-5.1",
                    status="success",
                    conversation_id="conv-b",
                ),
                RequestLog(
                    account_id="acc_dash_conversations",
                    request_id="dash-conversation-repeat-other-bucket",
                    requested_at=now - timedelta(hours=1, minutes=5),
                    model="gpt-5.1",
                    status="success",
                    conversation_id="conv-a",
                ),
                RequestLog(
                    account_id="acc_dash_conversations",
                    request_id="dash-conversation-null",
                    requested_at=now - timedelta(minutes=2),
                    model="gpt-5.1",
                    status="success",
                    conversation_id=None,
                ),
                RequestLog(
                    account_id="acc_dash_conversations",
                    request_id="dash-conversation-empty",
                    requested_at=now - timedelta(minutes=1),
                    model="gpt-5.1",
                    status="success",
                    conversation_id="",
                ),
                RequestLog(
                    account_id="acc_dash_conversations",
                    request_id="dash-conversation-whitespace",
                    requested_at=now,
                    model="gpt-5.1",
                    status="success",
                    conversation_id="   ",
                ),
                RequestLog(
                    account_id="acc_dash_conversations",
                    request_id="dash-conversation-tab",
                    requested_at=now,
                    model="gpt-5.1",
                    status="success",
                    conversation_id="\t",
                ),
                RequestLog(
                    account_id="acc_dash_conversations",
                    request_id="dash-conversation-newline",
                    requested_at=now,
                    model="gpt-5.1",
                    status="success",
                    conversation_id="\n",
                ),
                RequestLog(
                    account_id="acc_dash_conversations",
                    request_id="dash-conversation-warmup",
                    requested_at=now - timedelta(minutes=1),
                    model="gpt-5.1",
                    status="success",
                    request_kind="warmup",
                    conversation_id="conv-warmup",
                ),
                RequestLog(
                    account_id="acc_dash_conversations",
                    request_id="dash-conversation-old",
                    requested_at=now - timedelta(days=2),
                    model="gpt-5.1",
                    status="success",
                    conversation_id="conv-old",
                ),
            ]
        )
        await session.commit()

    response = await async_client.get("/api/dashboard/overview?timeframe=1d")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["metrics"]["conversations"] == 2
    assert payload["summary"]["metrics"]["conversationRequests"] == 4

    populated_conversation_values = [point["v"] for point in payload["trends"]["conversations"] if point["v"] > 0]
    assert populated_conversation_values == [1.0, 2.0]
    assert max(populated_conversation_values) == 2.0
    assert sum(point["v"] for point in payload["trends"]["conversations"]) == 3.0


@pytest.mark.asyncio
async def test_conversation_list_agrees_with_dashboard_activity_window(
    async_client,
    db_setup,
    monkeypatch: pytest.MonkeyPatch,
):
    now = utcnow().replace(microsecond=0)
    monkeypatch.setattr("app.modules.dashboard.service.utcnow", lambda: now)
    since = now - timedelta(days=1)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        await accounts_repo.upsert(_make_account("acc_conversation_window", "conversation-window@example.com"))
        session.add_all(
            [
                RequestLog(
                    account_id="acc_conversation_window",
                    request_id="conversation-window-old",
                    requested_at=now - timedelta(days=2),
                    model="gpt-5.1",
                    status="success",
                    conversation_id="conv-a",
                ),
                RequestLog(
                    account_id="acc_conversation_window",
                    request_id="conversation-window-active",
                    requested_at=now - timedelta(hours=1),
                    model="gpt-5.1",
                    status="success",
                    conversation_id="conv-a",
                ),
                RequestLog(
                    account_id="acc_conversation_window",
                    request_id="conversation-window-outside",
                    requested_at=now - timedelta(days=2),
                    model="gpt-5.1",
                    status="success",
                    conversation_id="conv-b",
                ),
                RequestLog(
                    account_id="acc_conversation_window",
                    request_id="conversation-window-deleted",
                    requested_at=now - timedelta(hours=2),
                    model="gpt-5.1",
                    status="success",
                    conversation_id="conv-deleted",
                    deleted_at=now - timedelta(hours=1),
                ),
            ]
        )
        await session.commit()

    conversations_response = await async_client.get("/api/conversations", params={"since": since.isoformat()})
    dashboard_response = await async_client.get("/api/dashboard/overview?timeframe=1d")

    assert conversations_response.status_code == 200
    assert dashboard_response.status_code == 200
    conversations = conversations_response.json()
    dashboard = dashboard_response.json()
    assert [row["conversationId"] for row in conversations["conversations"]] == ["conv-a"]
    assert conversations["total"] == 1
    assert dashboard["summary"]["metrics"]["conversations"] == 1
    assert dashboard["summary"]["metrics"]["conversationRequests"] == 1
    assert sum(point["v"] for point in dashboard["trends"]["conversations"]) == 1.0


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
async def test_dashboard_overview_weekly_primary_beats_no_data_secondary_placeholder(async_client, db_setup):
    # Regression: upstream reports the weekly window in the primary slot and an
    # empty no-data secondary placeholder (used_percent=0, no window duration, no
    # reset). Both rows are written milliseconds apart in the same fetch. Before
    # the data-aware tiebreak, the sub-second younger placeholder won and the
    # dashboard weekly remaining jumped to 100%. The real weekly used_percent
    # must drive the secondary remaining percent instead.
    now = utcnow().replace(microsecond=0)
    reset_at = int(naive_utc_to_epoch(now + timedelta(days=2)))

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_weekly_placeholder", "weekly-placeholder@example.com"))

        # Real weekly window reported in the primary slot.
        await usage_repo.add_entry(
            "acc_weekly_placeholder",
            74.0,
            window="primary",
            window_minutes=10080,
            reset_at=reset_at,
            recorded_at=now - timedelta(milliseconds=13),
        )
        # Empty secondary placeholder written ~13ms later in the same fetch.
        await usage_repo.add_entry(
            "acc_weekly_placeholder",
            0.0,
            window="secondary",
            window_minutes=0,
            reset_at=None,
            recorded_at=now,
        )

    response = await async_client.get("/api/dashboard/overview")
    assert response.status_code == 200
    payload = response.json()

    account = payload["accounts"][0]
    # The weekly primary row (74% used) must be remapped onto the secondary
    # slot, not the no-data placeholder (0% used -> 100% remaining).
    assert account["windowMinutesPrimary"] is None
    assert account["windowMinutesSecondary"] == 10080
    assert account["usage"]["secondaryRemainingPercent"] == pytest.approx(26.0)
    assert account["remainingCreditsSecondary"] == pytest.approx(7560.0 * 0.26)


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
        await accounts_repo.upsert(
            _make_account(
                "acc_quota_exceeded_fresh",
                "quota@example.com",
                plan_type="pro",
                status=AccountStatus.QUOTA_EXCEEDED,
            )
        )
        await accounts_repo.upsert(_make_account("acc_active_stale", "stale@example.com", plan_type="pro"))
        await accounts_repo.upsert(
            _make_account(
                "acc_inactive_fresh",
                "inactive@example.com",
                plan_type="pro",
                status=AccountStatus.DEACTIVATED,
            )
        )
        await accounts_repo.upsert(
            _make_account(
                "acc_reauth_fresh",
                "reauth@example.com",
                plan_type="pro",
                status=AccountStatus.REAUTH_REQUIRED,
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
            "acc_quota_exceeded_fresh",
            100.0,
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
        await usage_repo.add_entry(
            "acc_reauth_fresh",
            100.0,
            window="secondary",
            window_minutes=10080,
            reset_at=reset_at,
            recorded_at=fixed_now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/dashboard/projections")
    assert response.status_code == 200
    payload = response.json()

    pace = payload["weeklyCreditPace"]
    assert pace["accountCount"] == 3
    assert pace["staleAccountCount"] == 1
    assert pace["inactiveAccountCount"] == 1
    assert pace["totalFullCredits"] == pytest.approx(151_200.0)
    assert pace["actualUsedPercent"] == pytest.approx(73.333, abs=0.01)
    assert pace["scheduledUsedPercent"] == pytest.approx(42.857, abs=0.01)
    assert pace["scheduleGapCredits"] == pytest.approx(46_080.0, abs=1.0)
    # No account has two fresh samples, so burn is unmeasured and headroom is
    # ~26.7%: the runway verdict is safe, which maps to legacy on_track.
    assert pace["runwayStatus"] == "safe"
    assert pace["status"] == "on_track"


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
    assert pace["status"] == "on_track"


@pytest.mark.asyncio
async def test_dashboard_projections_weekly_credit_pace_smooths_displayed_gap(
    async_client,
    db_setup,
    monkeypatch: pytest.MonkeyPatch,
):
    fixed_now = datetime(2026, 5, 18, 12, 0, 0)
    monkeypatch.setattr("app.modules.dashboard.service.utcnow", lambda: fixed_now)
    reset_at = int(naive_utc_to_epoch(fixed_now + timedelta(days=5, hours=18)))

    settings_response = await async_client.put("/api/settings", json={"weeklyPaceSmoothingMinutes": 240})
    assert settings_response.status_code == 200

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_smoothed", "smoothed@example.com", plan_type="pro"))
        await usage_repo.add_entry(
            "acc_smoothed",
            0.0,
            window="secondary",
            window_minutes=10080,
            reset_at=reset_at,
            recorded_at=fixed_now - timedelta(hours=3),
        )
        await usage_repo.add_entry(
            "acc_smoothed",
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
    assert pace["paceGapSmoothingMinutes"] == 240
    assert pace["actualUsedPercent"] == pytest.approx(24.0)
    assert pace["deltaPercent"] == pytest.approx(6.142, abs=0.01)
    assert pace["scheduleGapCredits"] == pytest.approx(3_096.0, abs=1.0)
    assert pace["smoothedDeltaPercent"] == pytest.approx(-5.857, abs=0.01)
    assert pace["smoothedScheduleGapCredits"] == 0


@pytest.mark.asyncio
async def test_dashboard_projections_weekly_credit_pace_smoothing_resets_with_quota_window(
    async_client,
    db_setup,
    monkeypatch: pytest.MonkeyPatch,
):
    fixed_now = datetime(2026, 5, 18, 12, 0, 0)
    monkeypatch.setattr("app.modules.dashboard.service.utcnow", lambda: fixed_now)
    previous_reset_at = int(naive_utc_to_epoch(fixed_now - timedelta(minutes=5)))
    current_reset_at = int(naive_utc_to_epoch(fixed_now + timedelta(days=7)))

    settings_response = await async_client.put("/api/settings", json={"weeklyPaceSmoothingMinutes": 15})
    assert settings_response.status_code == 200

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)

        await accounts_repo.upsert(_make_account("acc_reset_smooth", "reset-smooth@example.com", plan_type="pro"))
        await usage_repo.add_entry(
            "acc_reset_smooth",
            99.0,
            window="secondary",
            window_minutes=10080,
            reset_at=previous_reset_at,
            recorded_at=fixed_now - timedelta(minutes=10),
        )
        await usage_repo.add_entry(
            "acc_reset_smooth",
            0.0,
            window="secondary",
            window_minutes=10080,
            reset_at=current_reset_at,
            recorded_at=fixed_now - timedelta(minutes=1),
        )

    response = await async_client.get("/api/dashboard/projections")
    assert response.status_code == 200
    payload = response.json()

    pace = payload["weeklyCreditPace"]
    assert pace["actualUsedPercent"] == pytest.approx(0.0)
    assert pace["smoothedDeltaPercent"] == pytest.approx(pace["deltaPercent"])
    assert pace["smoothedScheduleGapCredits"] == 0
    assert pace["status"] == "on_track"


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
    assert pace["status"] == "on_track"


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


def _assert_json_close(actual: object, expected: object, path: str = "$") -> None:
    if isinstance(expected, dict):
        assert isinstance(actual, dict), path
        assert actual.keys() == expected.keys(), path
        for key in expected:
            _assert_json_close(actual[key], expected[key], f"{path}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), path
        for index, (left, right) in enumerate(zip(actual, expected)):
            _assert_json_close(left, right, f"{path}[{index}]")
    elif isinstance(expected, float) and not isinstance(expected, bool):
        assert actual == pytest.approx(expected, rel=1e-12, abs=1e-12), path
    else:
        assert actual == expected, path


@pytest.mark.asyncio
async def test_dashboard_projections_ewma_tail_cap_matches_uncapped_history(async_client, db_setup, monkeypatch):
    """The projections fetch caps rows older than the equal-weight floor to
    the newest 64 per account. For a dense weekly-only account sourced from
    the primary stream (the production shape) the response must be
    equivalent to the uncapped fetch: exact for the floor-covered weekly
    pace values, within floating-point noise for the EWMA-derived fields."""
    from app.db.models import UsageHistory
    from app.db.session import engine
    from app.modules.dashboard import service as dashboard_service
    from app.modules.dashboard.repository import DashboardRepository

    now = utcnow().replace(microsecond=0)
    # Freeze the service clock so both responses are computed for the same
    # instant and only the fetched history can differ between them.
    monkeypatch.setattr(dashboard_service, "utcnow", lambda: now)
    fetched_row_counts: list[int] = []
    real_bulk_fetch = DashboardRepository.bulk_usage_history_since

    async def _recording_bulk_fetch(self, *args, **kwargs):
        grouped = await real_bulk_fetch(self, *args, **kwargs)
        fetched_row_counts.append(sum(len(rows) for rows in grouped.values()))
        return grouped

    monkeypatch.setattr(DashboardRepository, "bulk_usage_history_since", _recording_bulk_fetch)
    reset_at = int(naive_utc_to_epoch(now + timedelta(days=2)))

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        await accounts_repo.upsert(_make_account("acc_dense_weekly", "dense@example.com", plan_type="pro"))
        # One row every 10 minutes for 7 days on the primary stream carrying
        # the weekly window: ~1000 rows, far more than the 64-row tail
        # between the 7-day cutoff and the 3h floor.
        rows = []
        used = 3.0
        for index in range(7 * 24 * 6, 0, -1):
            used += 0.04 + 0.03 * ((index * 7919) % 11) / 11.0
            rows.append(
                UsageHistory(
                    account_id="acc_dense_weekly",
                    window="primary",
                    window_minutes=10080,
                    used_percent=round(used, 6),
                    reset_at=reset_at,
                    recorded_at=now - timedelta(minutes=10 * (index - 1) + 1),
                )
            )
        session.add_all(rows)
        await session.commit()

    capped = await async_client.get("/api/dashboard/projections")
    assert capped.status_code == 200
    capped_payload = capped.json()
    assert capped_payload["depletionSecondary"] is not None
    assert capped_payload["weeklyCreditPace"] is not None
    assert capped_payload["weeklyCreditPace"]["burnRateRecentCreditsPerHour"] > 0

    # Same database, same instant: lift the cap so every in-cutoff row is
    # hydrated, and compare against the capped response.
    monkeypatch.setattr(dashboard_service, "_PROJECTION_EWMA_TAIL_ROWS", 10**6)
    uncapped = await async_client.get("/api/dashboard/projections")
    assert uncapped.status_code == 200
    uncapped_payload = uncapped.json()

    # One primary-window fetch per request (weekly-only primary-source
    # account, no secondary rows). On PostgreSQL the capped fetch hydrates
    # the 18 rows inside the 3h floor plus the 64-row tail; SQLite serves its
    # shared-floor snapshot cache and ignores the cap.
    assert len(fetched_row_counts) == 2
    capped_rows, uncapped_rows = fetched_row_counts
    assert uncapped_rows == 7 * 24 * 6
    if str(engine.url).startswith("postgresql"):
        assert capped_rows == 18 + 64
    else:
        assert capped_rows == uncapped_rows

    _assert_json_close(
        capped_payload["depletionSecondary"], uncapped_payload["depletionSecondary"], "$.depletionSecondary"
    )
    _assert_json_close(capped_payload["weeklyCreditPace"], uncapped_payload["weeklyCreditPace"], "$.weeklyCreditPace")


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
            conversation_id="conv-timeframe-recent",
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
            conversation_id="conv-timeframe-old",
            requested_at=now - timedelta(days=2),
        )

    response = await async_client.get(f"/api/dashboard/overview?timeframe={timeframe}")
    assert response.status_code == 200
    payload = response.json()

    assert payload["timeframe"]["key"] == timeframe
    assert payload["timeframe"]["bucketCount"] == expected_bucket_count
    assert all(len(series) == expected_bucket_count for series in payload["trends"].values())
    assert any(point["v"] > 0 for point in payload["trends"]["conversations"])
    assert payload["summary"]["metrics"]["requests"] == expected_requests
    assert payload["summary"]["metrics"]["conversations"] == expected_requests
    if timeframe == "1d":
        assert payload["summary"]["metrics"]["errorCount"] == 0
        assert payload["summary"]["metrics"]["topError"] is None
    else:
        assert payload["summary"]["metrics"]["errorCount"] == 1
        assert payload["summary"]["metrics"]["topError"] == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_dashboard_overview_error_rate_excludes_cancelled_requests(async_client, db_setup):
    """Regression for #1552: cancelled/client_disconnected terminals are
    normal agent lifecycle — they must not inflate the overview error rate
    or top error, and they surface as a distinct cancelled count."""
    now = utcnow().replace(microsecond=0)

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        logs_repo = RequestLogsRepository(session)

        await accounts_repo.upsert(_make_account("acc_cancelled", "cancelled@example.com"))
        await usage_repo.add_entry(
            "acc_cancelled",
            20.0,
            window="primary",
            recorded_at=now - timedelta(minutes=5),
        )
        await usage_repo.add_entry(
            "acc_cancelled",
            40.0,
            window="secondary",
            recorded_at=now - timedelta(minutes=2),
        )
        await logs_repo.add_log(
            account_id="acc_cancelled",
            request_id="req_cx_success",
            model="gpt-5.1",
            input_tokens=100,
            output_tokens=50,
            latency_ms=50,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=10),
        )
        # Cancelled rows dominate the window (multi-agent disconnect churn).
        for index in range(2):
            await logs_repo.add_log(
                account_id="acc_cancelled",
                request_id=f"req_cx_cancelled_{index}",
                model="gpt-5.1",
                input_tokens=10,
                output_tokens=0,
                latency_ms=20,
                status="cancelled",
                error_code="client_disconnected",
                requested_at=now - timedelta(minutes=20 + index),
            )
        await logs_repo.add_log(
            account_id="acc_cancelled",
            request_id="req_cx_error",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=0,
            latency_ms=20,
            status="error",
            error_code="upstream_500",
            requested_at=now - timedelta(minutes=30),
        )

    response = await async_client.get("/api/dashboard/overview")
    assert response.status_code == 200
    metrics = response.json()["summary"]["metrics"]

    assert metrics["requests"] == 4
    assert metrics["errorCount"] == 1
    assert metrics["errorRate"] == pytest.approx(0.25)
    assert metrics["cancelledCount"] == 2
    assert metrics["topError"] == "upstream_500"


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


@pytest.mark.asyncio
async def test_dashboard_overview_includes_previous_window_summary_when_history_covers_full_cycle(
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

        await accounts_repo.upsert(_make_account("acc_compare", "compare@example.com"))
        await usage_repo.add_entry(
            "acc_compare",
            20.0,
            window="primary",
            recorded_at=fixed_now - timedelta(minutes=5),
        )
        await usage_repo.add_entry(
            "acc_compare",
            40.0,
            window="secondary",
            recorded_at=fixed_now - timedelta(minutes=2),
        )
        await logs_repo.add_log(
            account_id="acc_compare",
            request_id="req_compare_coverage",
            model="gpt-5.1",
            input_tokens=1,
            output_tokens=0,
            latency_ms=50,
            status="success",
            error_code=None,
            requested_at=fixed_now - timedelta(days=2, minutes=1),
        )
        await logs_repo.add_log(
            account_id="acc_compare",
            request_id="req_compare_previous",
            model="gpt-5.1",
            input_tokens=200,
            output_tokens=100,
            latency_ms=50,
            status="success",
            error_code=None,
            requested_at=fixed_now - timedelta(days=1, hours=1),
        )
        await logs_repo.add_log(
            account_id="acc_compare",
            request_id="req_compare_current",
            model="gpt-5.1",
            input_tokens=100,
            output_tokens=50,
            latency_ms=50,
            status="success",
            error_code=None,
            requested_at=fixed_now - timedelta(hours=2),
        )

    response = await async_client.get("/api/dashboard/overview?timeframe=1d")
    assert response.status_code == 200

    payload = response.json()
    assert payload["summary"]["metrics"]["requests"] == 1
    assert payload["summary"]["metrics"]["tokens"] == 150
    assert payload["summary"]["comparison"] == {
        "canCompare": True,
        "previous": {
            "requests": 1,
            "tokens": 300,
            "costUsd": pytest.approx(0.00125),
        },
    }


@pytest.mark.asyncio
async def test_dashboard_overview_hides_previous_window_summary_when_history_does_not_cover_full_cycle(
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

        await accounts_repo.upsert(_make_account("acc_partial_compare", "partial-compare@example.com"))
        await usage_repo.add_entry(
            "acc_partial_compare",
            20.0,
            window="primary",
            recorded_at=fixed_now - timedelta(minutes=5),
        )
        await usage_repo.add_entry(
            "acc_partial_compare",
            40.0,
            window="secondary",
            recorded_at=fixed_now - timedelta(minutes=2),
        )
        await logs_repo.add_log(
            account_id="acc_partial_compare",
            request_id="req_partial_previous",
            model="gpt-5.1",
            input_tokens=200,
            output_tokens=100,
            latency_ms=50,
            status="success",
            error_code=None,
            requested_at=fixed_now - timedelta(days=1, hours=1),
        )
        await logs_repo.add_log(
            account_id="acc_partial_compare",
            request_id="req_partial_current",
            model="gpt-5.1",
            input_tokens=100,
            output_tokens=50,
            latency_ms=50,
            status="success",
            error_code=None,
            requested_at=fixed_now - timedelta(hours=2),
        )

    response = await async_client.get("/api/dashboard/overview?timeframe=1d")
    assert response.status_code == 200

    payload = response.json()
    assert payload["summary"]["comparison"]["canCompare"] is False


@pytest.mark.asyncio
async def test_dashboard_overview_exposes_zero_previous_window_totals_when_full_cycle_has_no_usage(
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

        await accounts_repo.upsert(_make_account("acc_zero_compare", "zero-compare@example.com"))
        await usage_repo.add_entry(
            "acc_zero_compare",
            20.0,
            window="primary",
            recorded_at=fixed_now - timedelta(minutes=5),
        )
        await usage_repo.add_entry(
            "acc_zero_compare",
            40.0,
            window="secondary",
            recorded_at=fixed_now - timedelta(minutes=2),
        )
        await logs_repo.add_log(
            account_id="acc_zero_compare",
            request_id="req_zero_coverage",
            model="gpt-5.1",
            input_tokens=1,
            output_tokens=0,
            latency_ms=50,
            status="success",
            error_code=None,
            requested_at=fixed_now - timedelta(days=2, minutes=1),
        )
        await logs_repo.add_log(
            account_id="acc_zero_compare",
            request_id="req_zero_current",
            model="gpt-5.1",
            input_tokens=100,
            output_tokens=50,
            latency_ms=50,
            status="success",
            error_code=None,
            requested_at=fixed_now - timedelta(hours=2),
        )

    response = await async_client.get("/api/dashboard/overview?timeframe=1d")
    assert response.status_code == 200

    payload = response.json()
    assert payload["summary"]["comparison"] == {
        "canCompare": True,
        "previous": {
            "requests": 0,
            "tokens": 0,
            "costUsd": 0.0,
        },
    }


def _pin(
    pin_key: str,
    *,
    kind: str,
    expires_in: timedelta,
    purge_in: timedelta = timedelta(days=22),
) -> ModelSourcePin:
    now = datetime.now(timezone.utc)
    return ModelSourcePin(
        pin_key=pin_key,
        kind=kind,
        source_id="src_overflow",
        api_key_id=None,
        created_at=now - timedelta(hours=1),
        last_seen_at=now - timedelta(minutes=1),
        expires_at=now + expires_in,
        purge_at=now + purge_in,
    )


async def _designate_overflow(
    session: AsyncSession,
    *,
    source_id: str | None = "src_overflow",
    drain_until: datetime | None = None,
) -> None:
    """Put the install in a state where overflow rows and pins can exist.

    The overview's overflow read is gated on the same two settings columns the
    request path's ship-dark gate reads, so a test that seeds overflow rows
    without designating a source would be seeding a state production cannot
    reach. ``drain_until`` alone models a de-designated install still draining
    its pins.
    """
    settings = await SettingsRepository(session).get_or_create()
    settings.subscription_overflow_source_id = source_id
    settings.subscription_overflow_drain_until = drain_until
    await session.commit()


async def _add_overflow_log(
    logs_repo: RequestLogsRepository,
    request_id: str,
    *,
    source: str,
    requested_at: datetime,
    cost_usd: float | None,
    status: str = "success",
    with_usage: bool = True,
) -> None:
    await logs_repo.add_log(
        account_id=None,
        request_id=request_id,
        model="gpt-5.1",
        model_source_id="src_overflow",
        model_source_kind="openai_compatible",
        input_tokens=100 if with_usage else None,
        output_tokens=50 if with_usage else None,
        latency_ms=50,
        status=status,
        error_code=None if status == "success" else "client_disconnected",
        source=source,
        cost_usd=cost_usd,
        requested_at=requested_at,
    )


@pytest.mark.asyncio
async def test_dashboard_overview_omits_subscription_overflow_on_a_clean_install(async_client, db_setup):
    """The ship-dark guarantee for the dashboard: nothing new is rendered."""
    response = await async_client.get("/api/dashboard/overview")

    assert response.status_code == 200
    assert response.json()["summary"]["subscriptionOverflow"] is None


@pytest.mark.asyncio
async def test_dashboard_overview_reports_overflow_spend_pins_and_usage_less_rows(async_client, db_setup):
    now = utcnow().replace(microsecond=0)

    async with SessionLocal() as session:
        await _designate_overflow(session)
        logs_repo = RequestLogsRepository(session)
        await _add_overflow_log(
            logs_repo,
            "req_overflow_fresh",
            source="subscription_overflow",
            requested_at=now - timedelta(hours=1),
            cost_usd=0.25,
        )
        await _add_overflow_log(
            logs_repo,
            "req_overflow_pinned",
            source="subscription_overflow_pinned",
            requested_at=now - timedelta(hours=2),
            cost_usd=0.5,
        )
        await _add_overflow_log(
            logs_repo,
            "req_overflow_cancelled",
            source="subscription_overflow",
            requested_at=now - timedelta(hours=3),
            cost_usd=0.125,
            status="cancelled",
        )
        # No usage reported by the source: the owner writes null token columns and
        # `add_log` prices the row at 0.0, so it must be counted, not priced.
        await _add_overflow_log(
            logs_repo,
            "req_overflow_usage_less",
            source="subscription_overflow_pinned",
            requested_at=now - timedelta(hours=4),
            cost_usd=None,
            with_usage=False,
        )
        # Control rows: a warmup source and an unattributed subscription row.
        await logs_repo.add_log(
            account_id=None,
            request_id="req_overflow_control_warmup",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=10,
            latency_ms=10,
            status="success",
            error_code=None,
            source="limit_warmup",
            cost_usd=9.0,
            requested_at=now - timedelta(hours=1),
        )
        await logs_repo.add_log(
            account_id=None,
            request_id="req_overflow_control_plain",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=10,
            latency_ms=10,
            status="success",
            error_code=None,
            cost_usd=7.0,
            requested_at=now - timedelta(hours=1),
        )
        session.add_all(
            [
                _pin("thread:conv-live", kind="thread", expires_in=timedelta(days=6)),
                _pin("thread:conv-live-2", kind="thread", expires_in=timedelta(days=1)),
                _pin("thread:conv-tombstone", kind="thread", expires_in=-timedelta(hours=1)),
                _pin("anchor:key:resp_1", kind="anchor", expires_in=timedelta(days=6)),
                _pin("bounce:conv-bounced", kind="bounce", expires_in=timedelta(seconds=60)),
            ]
        )
        await session.commit()

    response = await async_client.get("/api/dashboard/overview")

    assert response.status_code == 200
    overflow = response.json()["summary"]["subscriptionOverflow"]
    assert overflow == {
        "requests": 4,
        "costUsd": pytest.approx(0.875),
        "usageLessRequests": 1,
        "livePins": 2,
    }


@pytest.mark.asyncio
async def test_dashboard_overview_overflow_cost_is_a_slice_of_the_estimated_cost(async_client, db_setup):
    now = utcnow().replace(microsecond=0)

    async with SessionLocal() as session:
        await _designate_overflow(session)
        logs_repo = RequestLogsRepository(session)
        await _add_overflow_log(
            logs_repo,
            "req_slice_overflow",
            source="subscription_overflow",
            requested_at=now - timedelta(hours=1),
            cost_usd=2.0,
        )
        await logs_repo.add_log(
            account_id=None,
            request_id="req_slice_subscription",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=10,
            latency_ms=10,
            status="success",
            error_code=None,
            cost_usd=3.0,
            requested_at=now - timedelta(hours=1),
        )

    payload = (await async_client.get("/api/dashboard/overview")).json()

    assert payload["summary"]["cost"]["totalUsd"] == pytest.approx(5.0)
    assert payload["summary"]["subscriptionOverflow"]["costUsd"] == pytest.approx(2.0)
    assert payload["summary"]["metrics"]["requests"] == 2


@pytest.mark.asyncio
async def test_dashboard_overview_keeps_the_overflow_tile_after_the_window_moves_on(async_client, db_setup):
    """Post-drain / out-of-window installs keep the neutral empty state."""
    now = utcnow().replace(microsecond=0)

    async with SessionLocal() as session:
        await _designate_overflow(session)
        logs_repo = RequestLogsRepository(session)
        await _add_overflow_log(
            logs_repo,
            "req_overflow_ancient",
            source="subscription_overflow",
            requested_at=now - timedelta(days=40),
            cost_usd=1.0,
        )

    payload = (await async_client.get("/api/dashboard/overview?timeframe=7d")).json()

    assert payload["summary"]["subscriptionOverflow"] == {
        "requests": 0,
        "costUsd": pytest.approx(0.0),
        "usageLessRequests": 0,
        "livePins": 0,
    }
    # Still out of range at the widest timeframe, and still visible.
    wide = (await async_client.get("/api/dashboard/overview?timeframe=30d")).json()
    assert wide["summary"]["subscriptionOverflow"]["requests"] == 0
    assert wide["summary"]["subscriptionOverflow"]["costUsd"] == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_dashboard_overview_shows_the_overflow_tile_for_a_live_pin_without_rows(async_client, db_setup):
    async with SessionLocal() as session:
        # De-designated but still draining: the drain deadline alone keeps the
        # tile alive, which is the state a switched-off canary leaves behind.
        await _designate_overflow(session, source_id=None, drain_until=utcnow() + timedelta(days=29))
        session.add(_pin("thread:conv-only-pin", kind="thread", expires_in=timedelta(days=3)))
        await session.commit()

    payload = (await async_client.get("/api/dashboard/overview")).json()

    assert payload["summary"]["subscriptionOverflow"] == {
        "requests": 0,
        "costUsd": pytest.approx(0.0),
        "usageLessRequests": 0,
        "livePins": 1,
    }


@pytest.mark.asyncio
async def test_dashboard_overview_overflow_slice_excludes_soft_deleted_rows(async_client, db_setup):
    now = utcnow().replace(microsecond=0)

    async with SessionLocal() as session:
        await _designate_overflow(session)
        logs_repo = RequestLogsRepository(session)
        await _add_overflow_log(
            logs_repo,
            "req_overflow_deleted",
            source="subscription_overflow",
            requested_at=now - timedelta(hours=1),
            cost_usd=4.0,
        )
        await session.execute(
            update(RequestLog).where(RequestLog.request_id == "req_overflow_deleted").values(deleted_at=now)
        )
        await session.commit()

    payload = (await async_client.get("/api/dashboard/overview")).json()

    # Excluded from the window slice, but the dispatch still happened, so the
    # tile stays visible with its neutral state.
    assert payload["summary"]["subscriptionOverflow"] == {
        "requests": 0,
        "costUsd": pytest.approx(0.0),
        "usageLessRequests": 0,
        "livePins": 0,
    }


@pytest.mark.asyncio
async def test_dashboard_overview_issues_no_overflow_statement_on_a_clean_install(async_client, db_setup, monkeypatch):
    """Query-count neutrality: a ship-dark poll costs exactly what it cost before the tile existed.

    The tile's read is three bounded statements (the windowed aggregate, the
    live thread-pin count and the existence probe). On an install that never
    designated a source none of them may be issued -- an overview poll runs
    every 30 s forever, so "invisible" has to mean free, not merely
    unrendered. The gate itself must also be free: it reads the settings row
    ``get_overview`` already loads, so the poll's ``dashboard_settings``
    lookup count may not grow either.

    The baseline is a control measured in this run, and the claim is the
    *delta* between two polls of the same install: designating a source adds
    exactly the tile's three statements and changes nothing else. Two things
    would otherwise make the measurement depend on timing rather than on the
    gate, and both are neutralised here instead of asserted around:

    * statements another task issues into the same engine while a window is
      open (see ``capture_task_statements``: the 0.5 s cache-invalidation
      poll and the 10 s bridge ring heartbeat), and
    * the two 5 s TTL caches the poll's own auth middleware reads, which
      re-issue four statements (three ``dashboard_users`` /
      ``dashboard_identities`` reads and one ``dashboard_settings`` load) on
      the first poll after they expire. Held warm for the whole test, so a
      stall between a warm-up poll and the measured poll cannot land them in
      one window only.
    """
    monkeypatch.setattr(get_dashboard_users_cache(), "_ttl_seconds", 3600.0)
    monkeypatch.setattr(get_settings_cache(), "_ttl_seconds", 3600.0)

    # Warm the poll once so the settings row exists and every cache is primed:
    # what is measured below is the steady state, not first-boot.
    assert (await async_client.get("/api/dashboard/overview")).status_code == 200

    async with capture_task_statements(engine) as clean_sql:
        clean = await async_client.get("/api/dashboard/overview")

    assert clean.status_code == 200
    assert clean.json()["summary"]["subscriptionOverflow"] is None
    assert not [statement for statement in clean_sql if "model_source_pins" in statement]
    assert not [statement for statement in clean_sql if "request_logs.source in" in statement]
    # One settings lookup, as before the gate existed: the second caller in
    # ``get_overview`` is served from the session identity map.
    assert len([statement for statement in clean_sql if "from dashboard_settings" in statement]) == 1
    clean_statements = Counter(clean_sql)

    async with SessionLocal() as session:
        await _designate_overflow(session)

    assert (await async_client.get("/api/dashboard/overview")).status_code == 200
    async with capture_task_statements(engine) as designated_sql:
        designated = await async_client.get("/api/dashboard/overview")

    assert designated.status_code == 200
    # Still nothing to render -- but now the poll has to *ask*, and the three
    # statements it asks with are exactly the cost the clean install above no
    # longer pays. This is the residual the gate closes, measured end to end.
    assert designated.json()["summary"]["subscriptionOverflow"] is None
    assert len([statement for statement in designated_sql if "model_source_pins" in statement]) == 1
    assert len([statement for statement in designated_sql if "request_logs.source in" in statement]) == 2
    assert len([statement for statement in designated_sql if "from dashboard_settings" in statement]) == 1

    # Same steady state, one designation later. Naming the added statements is
    # what the bare ``+ 3`` used to imply; ``dropped`` keeps the other
    # direction honest -- a statement the clean poll issues may not disappear
    # to pay for the tile.
    added = Counter(designated_sql) - clean_statements
    dropped = clean_statements - Counter(designated_sql)
    assert dropped == Counter(), dropped
    assert sum(added.values()) == 3, added
    assert len([statement for statement in added.elements() if "model_source_pins" in statement]) == 1
    assert len([statement for statement in added.elements() if "request_logs.source in" in statement]) == 2

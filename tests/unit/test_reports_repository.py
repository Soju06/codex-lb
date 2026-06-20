from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, Base, RequestLog
from app.modules.reports.repository import DailyReportRangeTooLargeError, ReportsRepository

pytestmark = pytest.mark.unit


@pytest.fixture
async def async_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    session = session_factory()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


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


@pytest.mark.asyncio
async def test_aggregate_daily_rows_groups_in_sql_and_returns_only_buckets_with_data(
    async_session: AsyncSession,
) -> None:
    repo = ReportsRepository(async_session)
    timezone_info = timezone(timedelta(hours=8))

    async_session.add(_make_account("acc_reports_daily", "reports-daily@example.com"))
    async_session.add_all(
        [
            RequestLog(
                account_id="acc_reports_daily",
                request_id="report-daily-1",
                requested_at=datetime(2026, 6, 1, 16, 30, tzinfo=timezone.utc).replace(tzinfo=None),
                model="gpt-5.1",
                status="success",
                input_tokens=10,
                output_tokens=4,
                cached_input_tokens=2,
                cost_usd=0.25,
            ),
            RequestLog(
                account_id=None,
                request_id="report-daily-2",
                requested_at=datetime(2026, 6, 3, 16, 30, tzinfo=timezone.utc).replace(tzinfo=None),
                model="gpt-5.1",
                status="error",
                input_tokens=5,
                output_tokens=1,
                cached_input_tokens=0,
                cost_usd=0.1,
            ),
        ]
    )
    await async_session.commit()

    rows = await repo.aggregate_daily_rows(
        date(2026, 6, 2),
        date(2026, 6, 4),
        timezone_info,
    )

    assert [row.date for row in rows] == ["2026-06-02", "2026-06-04"]
    assert rows[0].requests == 1
    assert rows[0].input_tokens == 10
    assert rows[0].output_tokens == 4
    assert rows[0].cached_input_tokens == 2
    assert rows[0].cost_usd == 0.25
    assert rows[0].active_accounts == 1
    assert rows[0].error_count == 0

    assert rows[1].requests == 1
    assert rows[1].input_tokens == 5
    assert rows[1].output_tokens == 1
    assert rows[1].cached_input_tokens == 0
    assert rows[1].cost_usd == 0.1
    assert rows[1].active_accounts == 0
    assert rows[1].error_count == 1


@pytest.mark.asyncio
async def test_aggregate_daily_rows_supports_ranges_longer_than_sqlite_compound_limit(
    async_session: AsyncSession,
) -> None:
    repo = ReportsRepository(async_session)
    timezone_info = timezone.utc
    start_date = date(2024, 1, 1)
    end_date = start_date + timedelta(days=500)

    async_session.add(_make_account("acc_reports_long_range", "reports-long-range@example.com"))
    async_session.add_all(
        [
            RequestLog(
                account_id="acc_reports_long_range",
                request_id="report-long-range-1",
                requested_at=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc).replace(tzinfo=None),
                model="gpt-5.1",
                status="success",
                input_tokens=10,
                output_tokens=4,
                cached_input_tokens=2,
                cost_usd=0.25,
            ),
            RequestLog(
                account_id="acc_reports_long_range",
                request_id="report-long-range-2",
                requested_at=datetime(2025, 5, 15, 12, 0, tzinfo=timezone.utc).replace(tzinfo=None),
                model="gpt-5.1",
                status="error",
                input_tokens=5,
                output_tokens=1,
                cached_input_tokens=0,
                cost_usd=0.1,
            ),
        ]
    )
    await async_session.commit()

    rows = await repo.aggregate_daily_rows(start_date, end_date, timezone_info)

    assert [row.date for row in rows] == ["2024-01-01", "2025-05-15"]
    assert rows[0].requests == 1
    assert rows[0].cost_usd == 0.25
    assert rows[1].requests == 1
    assert rows[1].cost_usd == 0.1


@pytest.mark.asyncio
async def test_aggregate_daily_rows_rejects_ranges_over_supported_window(
    async_session: AsyncSession,
) -> None:
    repo = ReportsRepository(async_session)

    with pytest.raises(DailyReportRangeTooLargeError, match="730 days or less"):
        await repo.aggregate_daily_rows(
            date(2024, 1, 1),
            date(2026, 1, 1),
            timezone.utc,
        )


@pytest.mark.asyncio
async def test_report_filters_apply_to_all_aggregates_including_earliest_activity(
    async_session: AsyncSession,
) -> None:
    repo = ReportsRepository(async_session)
    matched_at = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc).replace(tzinfo=None)
    filtered_out_at = datetime(2026, 5, 30, 9, 0, tzinfo=timezone.utc).replace(tzinfo=None)

    async_session.add(_make_account("acc_reports_filters", "reports-filters@example.com"))
    async_session.add_all(
        [
            RequestLog(
                account_id="acc_reports_filters",
                request_id="report-filter-match",
                requested_at=matched_at,
                model="gpt-5.1",
                useragent_group="opencode",
                status="success",
                input_tokens=10,
                output_tokens=4,
                cached_input_tokens=2,
                cost_usd=0.25,
            ),
            RequestLog(
                account_id="acc_reports_filters",
                request_id="report-filter-other-useragent",
                requested_at=filtered_out_at,
                model="gpt-5.1",
                useragent_group="CodexCLI",
                status="success",
                input_tokens=100,
                output_tokens=40,
                cached_input_tokens=20,
                cost_usd=2.5,
            ),
        ]
    )
    await async_session.commit()

    summary = await repo.aggregate_summary(
        datetime(2026, 6, 1, 0, 0),
        datetime(2026, 6, 2, 0, 0),
        useragent_group="opencode",
    )
    daily_rows = await repo.aggregate_daily_rows(
        date(2026, 6, 1),
        date(2026, 6, 1),
        timezone.utc,
        useragent_group="opencode",
    )
    by_model = await repo.aggregate_by_model(
        datetime(2026, 6, 1, 0, 0),
        datetime(2026, 6, 2, 0, 0),
        useragent_group="opencode",
    )
    by_account = await repo.aggregate_by_account(
        datetime(2026, 6, 1, 0, 0),
        datetime(2026, 6, 2, 0, 0),
        useragent_group="opencode",
    )
    earliest_activity_at = await repo.earliest_report_activity_at(useragent_group="opencode")

    assert summary.total_requests == 1
    assert summary.total_cost_usd == 0.25
    assert len(daily_rows) == 1
    assert daily_rows[0].requests == 1
    assert by_model[0].model == "gpt-5.1"
    assert by_model[0].cost_usd == 0.25
    assert by_model[0].request_count == 1
    assert by_account[0].account_id == "acc_reports_filters"
    assert by_account[0].request_count == 1
    assert earliest_activity_at == matched_at


@pytest.mark.asyncio
async def test_aggregate_by_useragent_separates_real_unknown_from_missing_groups(
    async_session: AsyncSession,
) -> None:
    repo = ReportsRepository(async_session)

    async_session.add(_make_account("acc_reports_useragents", "reports-useragents@example.com"))
    async_session.add_all(
        [
            RequestLog(
                account_id="acc_reports_useragents",
                request_id="report-useragent-opencode",
                requested_at=datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc).replace(tzinfo=None),
                model="gpt-5.1",
                useragent_group="opencode",
                status="success",
                input_tokens=10,
                output_tokens=4,
                cached_input_tokens=0,
                cost_usd=0.5,
            ),
            RequestLog(
                account_id="acc_reports_useragents",
                request_id="report-useragent-codex",
                requested_at=datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc).replace(tzinfo=None),
                model="gpt-5.2",
                useragent_group="CodexCLI",
                status="success",
                input_tokens=9,
                output_tokens=3,
                cached_input_tokens=0,
                cost_usd=0.3,
            ),
            RequestLog(
                account_id="acc_reports_useragents",
                request_id="report-useragent-real-unknown",
                requested_at=datetime(2026, 6, 1, 13, 30, tzinfo=timezone.utc).replace(tzinfo=None),
                model="gpt-5.0",
                useragent_group="Unknown",
                status="success",
                input_tokens=9,
                output_tokens=2,
                cached_input_tokens=0,
                cost_usd=0.4,
            ),
            RequestLog(
                account_id="acc_reports_useragents",
                request_id="report-useragent-blank",
                requested_at=datetime(2026, 6, 1, 14, 0, tzinfo=timezone.utc).replace(tzinfo=None),
                model="gpt-5.3",
                useragent_group="",
                status="success",
                input_tokens=8,
                output_tokens=2,
                cached_input_tokens=0,
                cost_usd=0.2,
            ),
            RequestLog(
                account_id="acc_reports_useragents",
                request_id="report-useragent-null",
                requested_at=datetime(2026, 6, 1, 15, 0, tzinfo=timezone.utc).replace(tzinfo=None),
                model="gpt-5.4",
                useragent_group=None,
                status="success",
                input_tokens=7,
                output_tokens=1,
                cached_input_tokens=0,
                cost_usd=0.1,
            ),
        ]
    )
    await async_session.commit()

    rows = await repo.aggregate_by_useragent(
        datetime(2026, 6, 1, 0, 0),
        datetime(2026, 6, 2, 0, 0),
    )

    assert [(row.useragent_group, row.cost_usd, row.request_count) for row in rows] == [
        ("opencode", 0.5, 1),
        ("Unknown", 0.4, 1),
        ("CodexCLI", 0.3, 1),
        ("Missing User-Agent", 0.1, 1),
    ]

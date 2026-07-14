from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, Base, RequestLog, RequestLogDailyAggregate
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


def _make_daily_aggregate(
    *,
    aggregate_key: str,
    bucket_date: date,
    account_id: str | None = "acc_reports_rollup",
    model: str = "gpt-5.1",
    status: str = "success",
    request_count: int = 1,
    error_count: int = 0,
    input_tokens: int = 10,
    output_tokens: int = 4,
    cached_input_tokens: int = 2,
    cost_usd: float = 0.25,
    useragent_group: str | None = "CodexCLI",
) -> RequestLogDailyAggregate:
    return RequestLogDailyAggregate(
        aggregate_key=aggregate_key,
        bucket_date=bucket_date,
        api_key_id="api_key_reports_rollup",
        account_id=account_id,
        model=model,
        status=status,
        error_code="rate_limit_exceeded" if error_count else None,
        request_kind="normal",
        service_tier="priority",
        requested_service_tier="priority",
        actual_service_tier="priority",
        transport="websocket",
        upstream_transport="websocket",
        source="codex",
        useragent_group=useragent_group,
        plan_type="plus",
        is_deleted=False,
        request_count=request_count,
        error_count=error_count,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        effective_output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        reasoning_tokens=0,
        cost_usd=cost_usd,
        cost_microdollars=int(cost_usd * 1_000_000),
        account_request_count=request_count,
        account_input_tokens=input_tokens,
        account_output_tokens=output_tokens,
        account_cached_input_tokens=cached_input_tokens,
        account_cost_usd=cost_usd,
        latency_ms_sum=0,
        latency_ms_count=0,
        latency_first_token_ms_sum=0,
        latency_first_token_ms_count=0,
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
async def test_report_aggregates_include_daily_rollups_and_recent_raw_rows(
    async_session: AsyncSession,
) -> None:
    repo = ReportsRepository(async_session)
    async_session.add(_make_account("acc_reports_rollup", "reports-rollup@example.com"))
    async_session.add(_make_account("acc_reports_recent", "reports-recent@example.com"))
    async_session.add_all(
        [
            _make_daily_aggregate(
                aggregate_key="reports-rollup-old-success",
                bucket_date=date(2026, 5, 31),
                account_id="acc_reports_rollup",
                model="gpt-5.1",
                request_count=3,
                error_count=0,
                input_tokens=30,
                output_tokens=12,
                cached_input_tokens=6,
                cost_usd=0.75,
            ),
            _make_daily_aggregate(
                aggregate_key="reports-rollup-old-error",
                bucket_date=date(2026, 6, 1),
                account_id=None,
                model="gpt-5.2",
                status="error",
                request_count=2,
                error_count=2,
                input_tokens=8,
                output_tokens=1,
                cached_input_tokens=0,
                cost_usd=0.2,
            ),
            RequestLog(
                account_id="acc_reports_recent",
                request_id="report-recent-raw",
                requested_at=datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc).replace(tzinfo=None),
                model="gpt-5.1",
                status="success",
                input_tokens=5,
                output_tokens=5,
                cached_input_tokens=1,
                cost_usd=0.1,
                request_kind="normal",
                source="codex",
            ),
        ]
    )
    await async_session.commit()

    start_at = datetime(2026, 5, 31, tzinfo=timezone.utc).replace(tzinfo=None)
    end_at = datetime(2026, 6, 3, tzinfo=timezone.utc).replace(tzinfo=None)

    summary = await repo.aggregate_summary(start_at, end_at)
    daily_rows = await repo.aggregate_daily_rows(date(2026, 5, 31), date(2026, 6, 2), timezone.utc)
    model_rows = await repo.aggregate_by_model(start_at, end_at)
    account_rows = await repo.aggregate_by_account(start_at, end_at)
    active_accounts = await repo.count_active_accounts(start_at, end_at)
    earliest = await repo.earliest_report_activity_at()

    assert summary.total_requests == 6
    assert summary.total_errors == 2
    assert summary.total_input_tokens == 43
    assert summary.total_output_tokens == 18
    assert summary.total_cached_tokens == 7
    assert summary.total_cost_usd == pytest.approx(1.05)
    assert summary.active_accounts == 2

    assert [(row.date, row.requests, row.error_count) for row in daily_rows] == [
        ("2026-05-31", 3, 0),
        ("2026-06-01", 2, 2),
        ("2026-06-02", 1, 0),
    ]
    assert [(row.model, row.cost_usd) for row in model_rows] == [
        ("gpt-5.1", pytest.approx(0.85)),
        ("gpt-5.2", pytest.approx(0.2)),
    ]
    assert [(row.account_id, row.request_count) for row in account_rows] == [
        ("acc_reports_rollup", 3),
        (None, 2),
        ("acc_reports_recent", 1),
    ]
    assert active_accounts == 2
    assert earliest == datetime(2026, 5, 31)


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
            _make_daily_aggregate(
                aggregate_key="report-filter-rollup-match",
                bucket_date=date(2026, 6, 1),
                account_id="acc_reports_filters",
                model="gpt-5.1",
                request_count=3,
                input_tokens=30,
                output_tokens=12,
                cached_input_tokens=6,
                cost_usd=0.75,
                useragent_group="opencode",
            ),
            _make_daily_aggregate(
                aggregate_key="report-filter-rollup-other-useragent",
                bucket_date=date(2026, 5, 29),
                account_id="acc_reports_filters",
                request_count=10,
                cost_usd=5.0,
                useragent_group="CodexCLI",
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

    assert summary.total_requests == 4
    assert summary.total_cost_usd == 1.0
    assert len(daily_rows) == 1
    assert daily_rows[0].requests == 4
    assert by_model[0].model == "gpt-5.1"
    assert by_model[0].cost_usd == 1.0
    assert by_model[0].request_count == 4
    assert by_account[0].account_id == "acc_reports_filters"
    assert by_account[0].request_count == 4
    assert earliest_activity_at == datetime(2026, 6, 1, 0, 0)


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
            _make_daily_aggregate(
                aggregate_key="report-useragent-rollup-opencode",
                bucket_date=date(2026, 6, 1),
                model="gpt-5.1",
                request_count=2,
                cost_usd=0.2,
                useragent_group="opencode",
            ),
            _make_daily_aggregate(
                aggregate_key="report-useragent-rollup-missing",
                bucket_date=date(2026, 6, 1),
                model="gpt-5.4",
                request_count=3,
                cost_usd=0.15,
                useragent_group=None,
            ),
            _make_daily_aggregate(
                aggregate_key="report-useragent-rollup-blank",
                bucket_date=date(2026, 6, 1),
                model="gpt-5.3",
                request_count=20,
                cost_usd=3.0,
                useragent_group="",
            ),
        ]
    )
    await async_session.commit()

    rows = await repo.aggregate_by_useragent(
        datetime(2026, 6, 1, 0, 0),
        datetime(2026, 6, 2, 0, 0),
    )

    assert [(row.useragent_group, row.cost_usd, row.request_count) for row in rows] == [
        ("opencode", 0.7, 3),
        ("Unknown", 0.4, 1),
        ("CodexCLI", 0.3, 1),
        ("Missing User-Agent", 0.25, 4),
    ]

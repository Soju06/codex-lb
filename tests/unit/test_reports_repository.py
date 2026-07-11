from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.dialects.postgresql import dialect as postgresql_dialect
from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.crypto import TokenEncryptor
from app.db.models import Account, AccountStatus, Base, RequestLog
from app.modules.reports.repository import (
    DailyReportRangeTooLargeError,
    ReportsRepository,
    _daily_speed_medians_stmt,
)

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
                latency_ms=1200,
                latency_first_token_ms=200,
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
                latency_ms=2600,
                latency_first_token_ms=600,
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
    assert rows[0].median_ttft_ms == 200
    assert rows[0].median_tps == 4

    assert rows[1].requests == 1
    assert rows[1].input_tokens == 5
    assert rows[1].output_tokens == 1
    assert rows[1].cached_input_tokens == 0
    assert rows[1].cost_usd == 0.1
    assert rows[1].active_accounts == 0
    assert rows[1].error_count == 1
    assert rows[1].median_ttft_ms == 600
    assert rows[1].median_tps == 0.5


@pytest.mark.asyncio
async def test_aggregate_daily_rows_calculates_sql_medians_for_odd_even_and_invalid_speed_samples(
    async_session: AsyncSession,
) -> None:
    repo = ReportsRepository(async_session)
    account_id = "acc_reports_speed_medians"
    async_session.add(_make_account(account_id, "reports-speed-medians@example.com"))
    async_session.add_all(
        [
            # Day one ignores missing TTFT and invalid TPS samples: TTFT [100, 200, 300], TPS [10].
            # Reasoning tokens are not used for the existing output TPS metric.
            RequestLog(
                account_id=account_id,
                request_id="report-speed-even-1",
                requested_at=datetime(2026, 6, 1, 9, 0),
                model="gpt-5.1",
                status="success",
                output_tokens=None,
                reasoning_tokens=10,
                latency_ms=1100,
                latency_first_token_ms=100,
            ),
            RequestLog(
                account_id=account_id,
                request_id="report-speed-even-2",
                requested_at=datetime(2026, 6, 1, 10, 0),
                model="gpt-5.1",
                status="success",
                output_tokens=12,
                reasoning_tokens=999,
                latency_ms=1500,
                latency_first_token_ms=300,
            ),
            RequestLog(
                account_id=account_id,
                request_id="report-speed-even-missing-ttft",
                requested_at=datetime(2026, 6, 1, 11, 0),
                model="gpt-5.1",
                status="success",
                output_tokens=None,
                reasoning_tokens=None,
                latency_ms=1500,
                latency_first_token_ms=None,
            ),
            RequestLog(
                account_id=account_id,
                request_id="report-speed-even-invalid-generation",
                requested_at=datetime(2026, 6, 1, 12, 0),
                model="gpt-5.1",
                status="success",
                output_tokens=9,
                latency_ms=200,
                latency_first_token_ms=200,
            ),
            # Day two ignores reasoning-only and zero-output rows for TPS: TTFT [100, 200, 300, 400], TPS [4, 20].
            RequestLog(
                account_id=account_id,
                request_id="report-speed-odd-1",
                requested_at=datetime(2026, 6, 2, 9, 0),
                model="gpt-5.1",
                status="success",
                output_tokens=20,
                latency_ms=1100,
                latency_first_token_ms=100,
            ),
            RequestLog(
                account_id=account_id,
                request_id="report-speed-odd-2",
                requested_at=datetime(2026, 6, 2, 10, 0),
                model="gpt-5.1",
                status="success",
                output_tokens=3,
                latency_ms=950,
                latency_first_token_ms=200,
            ),
            RequestLog(
                account_id=account_id,
                request_id="report-speed-odd-invalid-output",
                requested_at=datetime(2026, 6, 2, 11, 0),
                model="gpt-5.1",
                status="success",
                output_tokens=0,
                reasoning_tokens=50,
                latency_ms=700,
                latency_first_token_ms=300,
            ),
            RequestLog(
                account_id=account_id,
                request_id="report-speed-odd-reasoning-only",
                requested_at=datetime(2026, 6, 2, 12, 0),
                model="gpt-5.1",
                status="success",
                output_tokens=None,
                reasoning_tokens=40,
                latency_ms=800,
                latency_first_token_ms=400,
            ),
        ]
    )
    await async_session.commit()

    rows = await repo.aggregate_daily_rows(date(2026, 6, 1), date(2026, 6, 2), timezone.utc)

    assert [(row.date, row.median_ttft_ms, row.median_tps) for row in rows] == [
        ("2026-06-01", 200.0, 10.0),
        ("2026-06-02", 250.0, 12.0),
    ]


@pytest.mark.asyncio
async def test_aggregate_daily_rows_speed_medians_preserve_filters_and_timezone_buckets(
    async_session: AsyncSession,
) -> None:
    repo = ReportsRepository(async_session)
    async_session.add_all(
        [
            _make_account("acc_reports_speed_filter", "reports-speed-filter@example.com"),
            _make_account("acc_reports_speed_other", "reports-speed-other@example.com"),
            RequestLog(
                account_id="acc_reports_speed_filter",
                request_id="report-speed-filter-match",
                requested_at=datetime(2026, 6, 1, 7, 0),
                model="gpt-5.1",
                useragent_group="opencode",
                status="success",
                output_tokens=4,
                latency_ms=1100,
                latency_first_token_ms=100,
            ),
            RequestLog(
                account_id="acc_reports_speed_filter",
                request_id="report-speed-filter-before-local-day",
                requested_at=datetime(2026, 6, 1, 6, 59, 59),
                model="gpt-5.1",
                useragent_group="opencode",
                status="success",
                output_tokens=9,
                latency_ms=1000,
                latency_first_token_ms=900,
            ),
            RequestLog(
                account_id="acc_reports_speed_other",
                request_id="report-speed-filter-other-account",
                requested_at=datetime(2026, 6, 1, 7, 0),
                model="gpt-5.1",
                useragent_group="opencode",
                status="success",
                output_tokens=8,
                latency_ms=1000,
                latency_first_token_ms=800,
            ),
            RequestLog(
                account_id="acc_reports_speed_filter",
                request_id="report-speed-filter-other-model",
                requested_at=datetime(2026, 6, 1, 7, 0),
                model="gpt-5.2",
                useragent_group="opencode",
                status="success",
                output_tokens=7,
                latency_ms=1000,
                latency_first_token_ms=700,
            ),
            RequestLog(
                account_id="acc_reports_speed_filter",
                request_id="report-speed-filter-other-useragent",
                requested_at=datetime(2026, 6, 1, 7, 0),
                model="gpt-5.1",
                useragent_group="CodexCLI",
                status="success",
                output_tokens=6,
                latency_ms=1000,
                latency_first_token_ms=600,
            ),
        ]
    )
    await async_session.commit()

    rows = await repo.aggregate_daily_rows(
        date(2026, 6, 1),
        date(2026, 6, 1),
        ZoneInfo("America/Los_Angeles"),
        account_ids=["acc_reports_speed_filter"],
        model="gpt-5.1",
        useragent_group="opencode",
    )

    assert [(row.date, row.requests, row.median_ttft_ms, row.median_tps) for row in rows] == [
        ("2026-06-01", 1, 100.0, 4.0),
    ]


@pytest.mark.asyncio
async def test_daily_speed_medians_stmt_returns_only_one_row_per_populated_day_at_high_cardinality(
    async_session: AsyncSession,
) -> None:
    day_ranges = [
        ("2026-06-01", datetime(2026, 6, 1), datetime(2026, 6, 2)),
        ("2026-06-02", datetime(2026, 6, 2), datetime(2026, 6, 3)),
    ]
    async_session.add_all(
        [
            RequestLog(
                request_id=f"report-speed-many-{day}-{sample}",
                requested_at=datetime(2026, 6, day, 12, sample % 60),
                model="gpt-5.1",
                status="success",
                output_tokens=sample + 1,
                latency_ms=1000 + sample,
                latency_first_token_ms=sample,
            )
            for day in (1, 2)
            for sample in range(512)
        ]
    )
    await async_session.commit()

    result = await async_session.execute(_daily_speed_medians_stmt(day_ranges, None, None, None))
    rows = result.all()

    assert [(row.report_date, row.median_ttft_ms, row.median_tps) for row in rows] == [
        ("2026-06-01", 255.5, 256.5),
        ("2026-06-02", 255.5, 256.5),
    ]
    assert len(rows) == len(day_ranges)


def test_daily_speed_medians_stmt_compiles_to_portable_window_sql() -> None:
    statement = _daily_speed_medians_stmt(
        [("2026-06-01", datetime(2026, 6, 1), datetime(2026, 6, 2))],
        None,
        None,
        None,
    )

    for dialect in (sqlite_dialect(), postgresql_dialect()):
        sql = str(statement.compile(dialect=dialect, compile_kwargs={"literal_binds": True})).lower()

        assert "row_number() over" in sql
        assert "count(*) over" in sql
        assert "group by daily_ttft_ranks.report_date" in sql
        assert "group by daily_tps_ranks.report_date" in sql
        assert "percentile_cont" not in sql


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

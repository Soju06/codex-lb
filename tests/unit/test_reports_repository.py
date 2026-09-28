from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, datetime, timedelta, timezone
from typing import TypedDict
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.dialects.postgresql import dialect as postgresql_dialect
from sqlalchemy.dialects.sqlite import dialect as sqlite_dialect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.crypto import TokenEncryptor
from app.core.usage.speed import generation_speed_from_log
from app.db.models import Account, AccountStatus, Base, RequestLog
from app.modules.reports.repository import (
    DailyReportRangeTooLargeError,
    ReportsRepository,
    _daily_speed_medians_stmt,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("status", "output", "reasoning", "ttft", "first_output", "total", "terminal", "chunks", "expected_status"),
    [
        ("success", 200, 40, 100, 200, 1000, 1000, 2, "estimated"),
        ("success", 10000, 0, 100, 200, 300, 300, 2, "estimated"),
        ("success", 120, 110, 2000, 2000, 2005, 2005, 2, "insufficient_sample"),
        ("success", 120, 110, 100, 200, 1000, 1000, 1, "insufficient_sample"),
        ("success", 120, None, 100, 200, 1000, 1000, 2, "missing_usage"),
        ("success", None, 10, 100, 200, 1000, 1000, 2, "missing_usage"),
        ("success", 10, -1, 100, 200, 1000, 1000, 2, "invalid_sample"),
        ("success", 10, 11, 100, 200, 1000, 1000, 2, "invalid_sample"),
        ("success", 10, 10, 100, 200, 1000, 1000, 2, "insufficient_sample"),
        ("success", 10, 0, -1, 200, 1000, 1000, 2, "invalid_sample"),
        ("success", 10, 0, 300, 200, 1000, 1000, 2, "invalid_sample"),
        ("success", 10, 0, 100, 1200, 1000, 1000, 2, "invalid_sample"),
        ("success", 10, 0, None, 200, 1000, 1000, 2, "missing_timing"),
        ("success", 10, 0, 100, None, 1000, 1000, 2, "missing_timing"),
        ("success", 10, 0, 100, None, 1000, None, None, "legacy_estimate"),
        ("cancelled", 10, 0, 100, 200, 1000, 1000, 2, "incomplete"),
        ("error", 10, 0, 100, 200, 1000, 1000, 2, "incomplete"),
        ("success", 200, 40, 100, 200, 3000, 1000, 2, "estimated"),
        ("success", 10, 0, 100, 200, 1000, 1100, 2, "invalid_sample"),
        ("success", 10, 0, 100, 200, 1000, 150, 2, "invalid_sample"),
        ("success", 10, 0, 100, 200, 1000, None, 2, "missing_timing"),
        ("success", 10, 0, 100, None, 1000, 900, None, "missing_timing"),
        ("success", 10, 0, 100, 200, 1000, 250, 2, "insufficient_sample"),
    ],
)
@pytest.mark.asyncio
async def test_daily_tps_qualification_matches_request_speed(
    async_session: AsyncSession,
    status: str,
    output: int | None,
    reasoning: int | None,
    ttft: int | None,
    first_output: int | None,
    total: int,
    terminal: int | None,
    chunks: int | None,
    expected_status: str,
) -> None:
    log = RequestLog(
        request_id="speed-quality-parity",
        requested_at=datetime(2026, 9, 15, 12),
        model="gpt-test",
        request_kind="normal",
        status=status,
        output_tokens=output,
        reasoning_tokens=reasoning,
        latency_first_token_ms=ttft,
        latency_first_output_ms=first_output,
        latency_upstream_terminal_ms=terminal,
        latency_ms=total,
        output_delta_count=chunks,
    )
    async_session.add(log)
    await async_session.commit()
    speed = generation_speed_from_log(log)
    assert speed.status == expected_status
    rows = await ReportsRepository(async_session).aggregate_daily_rows(
        date(2026, 9, 15), date(2026, 9, 15), timezone.utc
    )
    expected_tps = speed.tps if speed.status == "estimated" else None
    assert rows[0].median_tps == expected_tps
    assert rows[0].tps_sample_count == (1 if expected_tps is not None else 0)


@pytest.mark.asyncio
async def test_timing_medians_exclude_prewarms_and_invalid_samples(async_session: AsyncSession) -> None:
    for index, kind, total, ttft, queue in [
        (0, "normal", 1000, 200, 0),
        (1, "prewarm", 1000, 800, 900),
        (2, "normal", 100, 200, 900),
        (3, "normal", 1000, -1, 900),
        (4, "normal", 1000, 200, -1),
    ]:
        async_session.add(
            RequestLog(
                request_id=f"timing-quality-{index}",
                requested_at=datetime(2026, 9, 15, 12),
                model="gpt-test",
                request_kind=kind,
                status="success",
                latency_ms=total,
                latency_first_token_ms=ttft,
                latency_queue_ms=queue,
            )
        )
    await async_session.commit()
    rows = await ReportsRepository(async_session).aggregate_daily_rows(
        date(2026, 9, 15), date(2026, 9, 15), timezone.utc
    )
    assert rows[0].median_ttft_ms == 200
    assert rows[0].median_queue_ms == 0
    assert rows[0].median_tps is None


class ReportAggregateFilters(TypedDict, total=False):
    account_ids: list[str]
    model: str
    useragent_group: str


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
                latency_first_output_ms=200,
                latency_upstream_terminal_ms=1200,
                output_delta_count=2,
                reasoning_tokens=0,
                latency_queue_ms=350,
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
            # Cancelled (client-disconnect) terminal on the same local day:
            # counted in requests, excluded from error_count (#1552).
            RequestLog(
                account_id=None,
                request_id="report-daily-3",
                requested_at=datetime(2026, 6, 3, 17, 30, tzinfo=timezone.utc).replace(tzinfo=None),
                model="gpt-5.1",
                status="cancelled",
                error_code="client_disconnected",
                input_tokens=3,
                output_tokens=0,
                cached_input_tokens=0,
                cost_usd=0.0,
                latency_ms=100,
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
    assert rows[0].median_queue_ms == 350

    assert rows[0].cancelled_count == 0

    assert rows[1].requests == 2
    assert rows[1].input_tokens == 8
    assert rows[1].output_tokens == 1
    assert rows[1].cached_input_tokens == 0
    assert rows[1].cost_usd == 0.1
    assert rows[1].active_accounts == 0
    assert rows[1].error_count == 1
    assert rows[1].cancelled_count == 1
    assert rows[1].median_ttft_ms is None
    assert rows[1].median_tps is None
    # Failed and cancelled requests do not populate timing medians.
    assert rows[1].median_queue_ms is None


@pytest.mark.asyncio
async def test_aggregate_summary_excludes_cancelled_from_errors_and_counts_them(
    async_session: AsyncSession,
) -> None:
    """Regression for #1552: cancelled terminals stay in total_requests but
    leave total_errors, surfacing as total_cancelled instead."""
    repo = ReportsRepository(async_session)
    base = datetime(2026, 6, 1, 12, 0)
    async_session.add(_make_account("acc_reports_cx", "reports-cx@example.com"))
    statuses = [
        ("success", None),
        ("cancelled", "client_disconnected"),
        ("cancelled", "client_disconnected"),
        ("error", "upstream_500"),
    ]
    async_session.add_all(
        [
            RequestLog(
                account_id="acc_reports_cx",
                request_id=f"report-cx-{index}",
                requested_at=base + timedelta(minutes=index),
                model="gpt-5.1",
                status=status,
                error_code=error_code,
                input_tokens=10,
                output_tokens=5,
                cached_input_tokens=0,
                cost_usd=0.1,
            )
            for index, (status, error_code) in enumerate(statuses)
        ]
    )
    await async_session.commit()

    summary = await repo.aggregate_summary(base - timedelta(hours=1), base + timedelta(hours=1))

    assert summary.total_requests == 4
    assert summary.total_errors == 1
    assert summary.total_cancelled == 2


@pytest.mark.asyncio
async def test_report_conversation_aggregates_are_distinct_nonblank_filtered_and_daily(
    async_session: AsyncSession,
) -> None:
    repo = ReportsRepository(async_session)
    async_session.add_all(
        [
            _make_account("acc_reports_conversations", "reports-conversations@example.com"),
            _make_account("acc_reports_other", "reports-other@example.com"),
            RequestLog(
                account_id="acc_reports_conversations",
                request_id="report-conversation-span-1",
                requested_at=datetime(2026, 6, 1, 10, 0),
                model="gpt-5.1",
                useragent_group="opencode",
                status="success",
                conversation_id="conv-span",
            ),
            RequestLog(
                account_id="acc_reports_conversations",
                request_id="report-conversation-span-duplicate",
                requested_at=datetime(2026, 6, 1, 11, 0),
                model="gpt-5.1",
                useragent_group="opencode",
                status="success",
                conversation_id="conv-span",
            ),
            RequestLog(
                account_id="acc_reports_conversations",
                request_id="report-conversation-span-2",
                requested_at=datetime(2026, 6, 2, 10, 0),
                model="gpt-5.1",
                useragent_group="opencode",
                status="success",
                conversation_id="conv-span",
            ),
            RequestLog(
                account_id="acc_reports_conversations",
                request_id="report-conversation-other",
                requested_at=datetime(2026, 6, 2, 11, 0),
                model="gpt-5.1",
                useragent_group="opencode",
                status="success",
                conversation_id="conv-other",
            ),
            RequestLog(
                account_id="acc_reports_conversations",
                request_id="report-conversation-null",
                requested_at=datetime(2026, 6, 2, 12, 0),
                model="gpt-5.1",
                useragent_group="opencode",
                status="success",
                conversation_id=None,
            ),
            RequestLog(
                account_id="acc_reports_conversations",
                request_id="report-conversation-empty",
                requested_at=datetime(2026, 6, 2, 13, 0),
                model="gpt-5.1",
                useragent_group="opencode",
                status="success",
                conversation_id="",
            ),
            RequestLog(
                account_id="acc_reports_conversations",
                request_id="report-conversation-whitespace",
                requested_at=datetime(2026, 6, 2, 14, 0),
                model="gpt-5.1",
                useragent_group="opencode",
                status="success",
                conversation_id="   ",
            ),
            RequestLog(
                account_id="acc_reports_conversations",
                request_id="report-conversation-warmup",
                requested_at=datetime(2026, 6, 2, 15, 0),
                model="gpt-5.1",
                useragent_group="opencode",
                status="success",
                request_kind="warmup",
                conversation_id="conv-warmup",
            ),
            RequestLog(
                account_id="acc_reports_other",
                request_id="report-conversation-other-account",
                requested_at=datetime(2026, 6, 1, 10, 0),
                model="gpt-5.1",
                useragent_group="opencode",
                status="success",
                conversation_id="conv-filtered-account",
            ),
            RequestLog(
                account_id="acc_reports_conversations",
                request_id="report-conversation-other-model",
                requested_at=datetime(2026, 6, 1, 10, 0),
                model="gpt-5.2",
                useragent_group="opencode",
                status="success",
                conversation_id="conv-filtered-model",
            ),
            RequestLog(
                account_id="acc_reports_conversations",
                request_id="report-conversation-other-useragent",
                requested_at=datetime(2026, 6, 1, 10, 0),
                model="gpt-5.1",
                useragent_group="CodexCLI",
                status="success",
                conversation_id="conv-filtered-useragent",
            ),
        ]
    )
    await async_session.commit()

    summary = await repo.aggregate_summary(
        datetime(2026, 6, 1),
        datetime(2026, 6, 3),
        account_ids=["acc_reports_conversations"],
        model="gpt-5.1",
        useragent_group="opencode",
    )
    daily_rows = await repo.aggregate_daily_rows(
        date(2026, 6, 1),
        date(2026, 6, 2),
        timezone.utc,
        account_ids=["acc_reports_conversations"],
        model="gpt-5.1",
        useragent_group="opencode",
    )

    assert summary.conversation_count == 2
    assert [(row.date, row.conversation_count) for row in daily_rows] == [("2026-06-01", 1), ("2026-06-02", 2)]


@pytest.mark.asyncio
async def test_report_conversation_aggregates_exclude_tab_and_newline_only_ids(
    async_session: AsyncSession,
) -> None:
    repo = ReportsRepository(async_session)
    async_session.add_all(
        [
            RequestLog(
                request_id="report-conversation-tab-only",
                requested_at=datetime(2026, 6, 1, 10, 0),
                model="gpt-5.1",
                status="success",
                conversation_id="\t",
            ),
            RequestLog(
                request_id="report-conversation-newline-only",
                requested_at=datetime(2026, 6, 1, 11, 0),
                model="gpt-5.1",
                status="success",
                conversation_id="\n",
            ),
        ]
    )
    await async_session.commit()

    summary = await repo.aggregate_summary(datetime(2026, 6, 1), datetime(2026, 6, 2))
    daily_rows = await repo.aggregate_daily_rows(date(2026, 6, 1), date(2026, 6, 1), timezone.utc)

    assert summary.conversation_count == 0
    assert daily_rows[0].conversation_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_name", "rows", "filters", "start_at", "end_at", "expected"),
    [
        (
            "duplicate_dedupe",
            [{"conversation_id": "conv-duplicate"}, {"conversation_id": "conv-duplicate"}],
            {},
            datetime(2026, 6, 1),
            datetime(2026, 6, 2),
            1,
        ),
        ("null_id", [{"conversation_id": None}], {}, datetime(2026, 6, 1), datetime(2026, 6, 2), 0),
        ("empty_id", [{"conversation_id": ""}], {}, datetime(2026, 6, 1), datetime(2026, 6, 2), 0),
        ("space_only_id", [{"conversation_id": "   "}], {}, datetime(2026, 6, 1), datetime(2026, 6, 2), 0),
        ("tab_only_id", [{"conversation_id": "\t"}], {}, datetime(2026, 6, 1), datetime(2026, 6, 2), 0),
        ("newline_only_id", [{"conversation_id": "\n"}], {}, datetime(2026, 6, 1), datetime(2026, 6, 2), 0),
        (
            "warmup_traffic",
            [{"conversation_id": "conv-warmup", "request_kind": "warmup"}],
            {},
            datetime(2026, 6, 1),
            datetime(2026, 6, 2),
            0,
        ),
        (
            "abnormal_source_traffic",
            [{"conversation_id": "conv-source-warmup", "source": "limit_warmup"}],
            {},
            datetime(2026, 6, 1),
            datetime(2026, 6, 2),
            0,
        ),
        (
            "account_filter",
            [
                {"account_id": "acc-selected", "conversation_id": "conv-selected-account"},
                {"account_id": "acc-other", "conversation_id": "conv-other-account"},
            ],
            {"account_ids": ["acc-selected"]},
            datetime(2026, 6, 1),
            datetime(2026, 6, 2),
            1,
        ),
        (
            "model_filter",
            [
                {"model": "gpt-selected", "conversation_id": "conv-selected-model"},
                {"model": "gpt-other", "conversation_id": "conv-other-model"},
            ],
            {"model": "gpt-selected"},
            datetime(2026, 6, 1),
            datetime(2026, 6, 2),
            1,
        ),
        (
            "useragent_filter",
            [
                {"useragent_group": "opencode", "conversation_id": "conv-selected-useragent"},
                {"useragent_group": "CodexCLI", "conversation_id": "conv-other-useragent"},
            ],
            {"useragent_group": "opencode"},
            datetime(2026, 6, 1),
            datetime(2026, 6, 2),
            1,
        ),
        (
            "out_of_range_date",
            [{"requested_at": datetime(2026, 6, 3), "conversation_id": "conv-out-of-range"}],
            {},
            datetime(2026, 6, 1),
            datetime(2026, 6, 2),
            0,
        ),
    ],
    ids=[
        "duplicate_dedupe",
        "null_id",
        "empty_id",
        "space_only_id",
        "tab_only_id",
        "newline_only_id",
        "warmup_traffic",
        "abnormal_source_traffic",
        "account_filter",
        "model_filter",
        "useragent_filter",
        "out_of_range_date",
    ],
)
async def test_report_conversation_aggregate_rules_are_independently_counted(
    async_session: AsyncSession,
    case_name: str,
    rows: list[dict[str, object]],
    filters: ReportAggregateFilters,
    start_at: datetime,
    end_at: datetime,
    expected: int,
) -> None:
    del case_name
    async_session.add_all(
        [
            RequestLog(
                request_id=f"report-conversation-rule-{index}",
                requested_at=row.get("requested_at", start_at),
                model=row.get("model", "gpt-5.1"),
                status="success",
                account_id=row.get("account_id"),
                useragent_group=row.get("useragent_group"),
                source=row.get("source"),
                request_kind=row.get("request_kind", "normal"),
                conversation_id=row.get("conversation_id"),
            )
            for index, row in enumerate(rows)
        ]
    )
    await async_session.commit()

    summary = await ReportsRepository(async_session).aggregate_summary(
        start_at,
        end_at,
        account_ids=filters.get("account_ids"),
        model=filters.get("model"),
        useragent_group=filters.get("useragent_group"),
    )

    assert summary.conversation_count == expected


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
            # TPS excludes the two reasoning tokens from the valid sample.
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
                latency_queue_ms=40,
            ),
            RequestLog(
                account_id=account_id,
                request_id="report-speed-even-2",
                requested_at=datetime(2026, 6, 1, 10, 0),
                model="gpt-5.1",
                status="success",
                output_tokens=14,
                reasoning_tokens=2,
                latency_ms=1500,
                latency_first_token_ms=300,
                latency_first_output_ms=300,
                latency_upstream_terminal_ms=1500,
                output_delta_count=2,
                latency_queue_ms=60,
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
                latency_first_output_ms=100,
                latency_upstream_terminal_ms=1100,
                output_delta_count=2,
                reasoning_tokens=0,
                latency_queue_ms=10,
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
                latency_first_output_ms=200,
                latency_upstream_terminal_ms=950,
                output_delta_count=2,
                reasoning_tokens=0,
                latency_queue_ms=30,
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
                latency_queue_ms=500,
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

    assert [(row.date, row.median_ttft_ms, row.median_tps, row.median_queue_ms) for row in rows] == [
        ("2026-06-01", 200.0, 10.0, 50.0),
        ("2026-06-02", 250.0, 12.0, 30.0),
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
                latency_first_output_ms=100,
                latency_upstream_terminal_ms=1100,
                output_delta_count=2,
                reasoning_tokens=0,
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
                latency_first_output_ms=900,
                latency_upstream_terminal_ms=1000,
                output_delta_count=2,
                reasoning_tokens=0,
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
                latency_first_output_ms=800,
                latency_upstream_terminal_ms=1000,
                output_delta_count=2,
                reasoning_tokens=0,
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
                latency_first_output_ms=700,
                latency_upstream_terminal_ms=1000,
                output_delta_count=2,
                reasoning_tokens=0,
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
                latency_first_output_ms=600,
                latency_upstream_terminal_ms=1000,
                output_delta_count=2,
                reasoning_tokens=0,
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
                latency_first_output_ms=sample,
                latency_upstream_terminal_ms=1000 + sample,
                output_delta_count=2,
                reasoning_tokens=0,
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

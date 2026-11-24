from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RequestLog
from app.db.session import SessionLocal, engine
from app.modules.reports import repository as module
from app.modules.reports.repository import ReportsRepository

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "zone,first",
    [
        ("UTC", datetime(2026, 3, 30, 0, 30)),
        ("America/New_York", datetime(2026, 3, 30, 0, 30)),
        ("Australia/Sydney", datetime(2026, 3, 30, 21, 30)),
        ("America/New_York", datetime(2026, 3, 8, 6, 30)),
    ],
)
async def test_daily_history_bound_preserves_exact_results_timezone_and_original_speed_guard(
    db_setup,
    monkeypatch,
    zone,
    first,
):
    async with SessionLocal() as session:
        session.add_all(
            [
                RequestLog(
                    request_id="selected",
                    model="selected",
                    status="success",
                    requested_at=first,
                    input_tokens=100,
                    output_tokens=20,
                    cost_usd=0.5,
                ),
                RequestLog(
                    request_id="other",
                    model="other",
                    status="success",
                    requested_at=datetime(2026, 1, 1),
                    input_tokens=200,
                    output_tokens=40,
                    cost_usd=1.0,
                ),
            ]
        )
        await session.commit()
    captured = []
    original_source = module.report_source

    def source(session, windows, *args, **kwargs):
        if windows[0][0] != "earliest":
            captured.append(len(windows))
        return original_source(session, windows, *args, **kwargs)

    monkeypatch.setattr(module, "report_source", source)
    monkeypatch.setattr(
        module, "_daily_speed_medians_stmt", lambda *_: pytest.fail("long-range speed guard was lifted")
    )
    end = date(2026, 3, 10) if first.day == 8 else date(2026, 3, 31)
    async with SessionLocal() as session:
        repo = ReportsRepository(session)
        earliest = await repo.earliest_report_activity_at(model="selected")
        full = await repo.aggregate_daily_rows(date(2026, 1, 1), end, ZoneInfo(zone), model="selected")
        trimmed = await repo.aggregate_daily_rows(
            date(2026, 1, 1), end, ZoneInfo(zone), model="selected", earliest_activity_at=earliest
        )
        assert trimmed == full
        assert len(trimmed) == 1
        assert captured[1] < captured[0]
        assert captured[1] <= 3
        assert (
            await repo.aggregate_daily_rows(
                date(2025, 1, 1), date(2025, 3, 31), ZoneInfo(zone), model="selected", earliest_activity_at=earliest
            )
            == []
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dialect,isolation,allowed",
    [
        ("postgresql", "REPEATABLE READ", True),
        ("postgresql", "READ COMMITTED", False),
        ("sqlite", "SERIALIZABLE", False),
    ],
)
async def test_bound_requires_a_real_repeatable_server_view(dialect, isolation, allowed):
    connection = SimpleNamespace(get_isolation_level=AsyncMock(return_value=isolation))
    session = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name=dialect)),
        connection=AsyncMock(return_value=connection),
    )
    assert await ReportsRepository(cast(AsyncSession, session)).has_repeatable_history_view() is allowed
    if dialect == "sqlite":
        session.connection.assert_not_awaited()


@pytest.mark.asyncio
async def test_late_historical_insert_does_not_invalidate_same_transaction_bound(db_setup):
    if engine.dialect.name != "postgresql":
        pytest.skip("Real PostgreSQL repeatable-view/backfill proof")
    async with SessionLocal() as writer:
        writer.add(RequestLog(request_id="known", model="m", status="success", requested_at=datetime(2026, 3, 30)))
        await writer.commit()
    async with SessionLocal() as reader:
        await reader.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        repo = ReportsRepository(reader)
        assert await repo.has_repeatable_history_view()
        first = await repo.earliest_report_activity_at()
        async with SessionLocal() as writer:
            writer.add(
                RequestLog(request_id="backfill", model="m", status="success", requested_at=datetime(2026, 2, 1))
            )
            await writer.commit()
        full = await repo.aggregate_daily_rows(date(2026, 1, 1), date(2026, 3, 31), ZoneInfo("UTC"))
        clipped = await repo.aggregate_daily_rows(
            date(2026, 1, 1), date(2026, 3, 31), ZoneInfo("UTC"), earliest_activity_at=first
        )
        assert full == clipped
        assert len(full) == 1
        await reader.commit()
        assert await repo.earliest_report_activity_at() == datetime(2026, 2, 1)


@pytest.mark.asyncio
async def test_report_worker_isolation_is_owned_and_restored(db_setup, monkeypatch):
    if engine.dialect.name != "postgresql":
        pytest.skip("Real PostgreSQL connection-isolation reset proof")
    from app.db.session import get_background_session
    from app.modules.read_snapshots import scheduler as scheduler_module
    from app.modules.read_snapshots.repository import SnapshotRepository
    from app.modules.read_snapshots.scheduler import ReadSnapshotScheduler

    async with get_background_session() as session:
        before = await (await session.connection()).get_isolation_level()
    seen = []

    async def collect(session, parameters):
        seen.append(await (await session.connection()).get_isolation_level())
        assert await ReportsRepository(session).has_repeatable_history_view()
        return {"prepared": True}

    monkeypatch.setattr(scheduler_module, "report_snapshot", collect)
    worker = ReadSnapshotScheduler()
    assert await worker.leader.try_acquire()
    async with get_background_session() as session:
        key = await SnapshotRepository(session).register("report-range", {"timezone": "UTC"})
    await worker.refresh_one(key, "report-range", {"timezone": "UTC"})
    assert seen == ["REPEATABLE READ"]
    async with get_background_session() as session:
        assert await (await session.connection()).get_isolation_level() == before
        value = await SnapshotRepository(session).read(key, interval_seconds=300)
        assert value is not None
        assert value.payload == {"prepared": True}
    assert await worker.stop()

from __future__ import annotations

from datetime import timedelta

import pytest

from app.core.utils.time import utcnow
from app.db.session import SessionLocal
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_usage_repository_aggregate(db_setup):
    async with SessionLocal() as session:
        repo = UsageRepository(session)
        now = utcnow()
        await repo.add_entry("acc1", 10.0, recorded_at=now - timedelta(hours=1))
        await repo.add_entry("acc1", 30.0, recorded_at=now - timedelta(minutes=30))
        await repo.add_entry("acc2", 50.0, recorded_at=now - timedelta(minutes=10))

        rows = await repo.aggregate_since(now - timedelta(hours=5))
        row_map = {row.account_id: row for row in rows}
        assert row_map["acc1"].used_percent_avg == pytest.approx(20.0)
        assert row_map["acc2"].used_percent_avg == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_request_logs_repository_filters(db_setup):
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)
        now = utcnow()
        await repo.add_log(
            account_id="acc1",
            request_id="req_repo_1",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=20,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=10),
        )
        await repo.add_log(
            account_id="acc2",
            request_id="req_repo_2",
            model="gpt-5.1",
            input_tokens=5,
            output_tokens=5,
            latency_ms=50,
            status="error",
            error_code="rate_limit_exceeded",
            requested_at=now - timedelta(minutes=5),
        )

        results = await repo.list_recent(limit=0, account_id="acc1")
        assert len(results) == 1
        assert results[0].account_id == "acc1"

        results = await repo.list_recent(limit=0, status="error")
        assert len(results) == 1
        assert results[0].error_code == "rate_limit_exceeded"

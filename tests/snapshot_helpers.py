"""Explicit out-of-request preparation for numerical API fixtures.

Tests seed observations, run the real independently fenced producer, then issue
HTTP reads. No client/transport hook computes snapshots implicitly.
"""

from __future__ import annotations

from datetime import date
from functools import partial

from httpx import URL

from app.db.session import SessionLocal
from app.modules.read_snapshots.keys import report_parameters
from app.modules.read_snapshots.repository import SnapshotRepository
from app.modules.read_snapshots.scheduler import ReadSnapshotScheduler
from app.modules.reports.service import InvalidReportDateRangeError


async def prepare_read_snapshot(path: str, *, params=None) -> None:
    url = URL(path)
    query = url.params.merge(params or {})
    if url.path.startswith("/api/dashboard/"):
        if query.get("timeframe", "7d") not in ("1d", "7d", "30d"):
            return
        kind, parameters = "dashboard", {}
    elif url.path.startswith("/api/reports"):
        identity = url.path.endswith("thread-identity")
        main = url.path == "/api/reports"
        try:
            kind, parameters, _ = report_parameters(
                date.fromisoformat(query["start_date"]) if query.get("start_date") else None,
                date.fromisoformat(query["end_date"]) if query.get("end_date") else None,
                query.get("timezone"),
                query.get_list("account_id") if not identity else None,
                query.get_list("api_key_id") if not identity else None,
                query.get("model") if main else None,
                query.get("useragent_group") if main else None,
            )
        except (ValueError, InvalidReportDateRangeError):
            return  # The HTTP parser, not a fixture, must reject invalid input.
    else:
        return
    async with SessionLocal() as session:
        key = await SnapshotRepository(session).register(kind, parameters)
    worker = ReadSnapshotScheduler()
    try:
        await worker.leader.run_if_leader(partial(worker.refresh_one, key, kind, parameters))
        async with SessionLocal() as session:
            value = await SnapshotRepository(session).read(key, interval_seconds=300)
            assert value is not None and value.payload is not None, None if value is None else value.last_error
    finally:
        assert await worker.stop()

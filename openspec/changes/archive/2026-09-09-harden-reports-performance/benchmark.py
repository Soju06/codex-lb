"""Synthetic PostgreSQL benchmark. REPORT_BENCH_DATABASE_URL must point to an empty disposable DB."""
# ruff: noqa: E402

import asyncio
import json
import os
from datetime import date, datetime, timedelta
from time import perf_counter

os.environ["CODEX_LB_DATABASE_URL"] = os.environ["REPORT_BENCH_DATABASE_URL"]

from sqlalchemy import text

from app.db.models import Base
from app.db.session import SessionLocal, engine
from app.modules.reports.repository import ReportsRepository
from app.modules.reports.rollup import fold_next_report_slice
from app.modules.reports.service import ReportsService


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        count = (await conn.execute(text("SELECT count(*) FROM request_logs"))).scalar_one()
        if count:
            raise RuntimeError("Benchmark requires an empty disposable database")
        await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_report_bench_time ON request_logs(requested_at, id)"))
        await conn.execute(
            text("""
            INSERT INTO request_logs(request_id, requested_at, model, useragent_group, api_key_id,
                conversation_id, status, input_tokens, output_tokens, cached_input_tokens,
                cost_usd, latency_ms, latency_first_token_ms, latency_queue_ms)
            SELECT 'bench-' || i, timestamp '2026-06-01' + (i-1) * interval '90 days' / 300000,
                'model-' || ((i/50)%3), 'client-' || ((i/50)%2), 'key-' || ((i/50)%5),
                'conversation-' || (i/50), CASE WHEN i%20=0 THEN 'error' ELSE 'success' END,
                1000, 200, 500, 0.01, 1200, 200, 20
            FROM generate_series(1, 300000) i
        """)
        )
        await conn.execute(text("ANALYZE request_logs"))

    async def measure():
        async with SessionLocal() as session:
            service = ReportsService(ReportsRepository(session))
            start = perf_counter()
            report = await service.get_reports(date(2026, 6, 1), date(2026, 8, 29), "Asia/Seoul")
            duration = perf_counter() - start
            start = perf_counter()
            options = await service.get_options(date(2026, 6, 1), date(2026, 8, 29), "Asia/Seoul")
            return report, options, duration, perf_counter() - start

    before, options_before, raw_duration, raw_options = await measure()
    slices = []
    # Leave a two-hour raw live tail, as in the production steady state.
    target = datetime(2026, 8, 30) - timedelta(hours=2)
    async with SessionLocal() as session:
        while True:
            start = perf_counter()
            if not await fold_next_report_slice(session, target):
                break
            slices.append(perf_counter() - start)
    after, options_after, folded_duration, folded_options = await measure()
    assert before.model_dump(exclude={"generated_at"}) == after.model_dump(exclude={"generated_at"})
    assert options_before == options_after
    async with engine.connect() as conn:
        folded_rows = (await conn.execute(text("SELECT count(*) FROM request_report_hourly_rollups"))).scalar_one()
        size = (await conn.execute(text("SELECT pg_size_pretty(pg_database_size(current_database()))"))).scalar_one()
    print(
        json.dumps(
            {
                "raw_rows": 300000,
                "rollup_rows": folded_rows,
                "database_size": size,
                "raw_90d_seconds": raw_duration,
                "rollup_90d_seconds": folded_duration,
                "raw_options_seconds": raw_options,
                "rollup_options_seconds": folded_options,
                "fold_slices": len(slices),
                "fold_total_seconds": sum(slices),
                "fold_max_slice_seconds": max(slices),
                "parity": True,
            },
            indent=2,
        ),
        flush=True,
    )
    await engine.dispose()


asyncio.run(main())

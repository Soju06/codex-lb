"""Synthetic PostgreSQL benchmark; requires a confirmed, empty codex_lb_report_bench_* database."""
# ruff: noqa: E402

import asyncio
import json
import os
from datetime import date, datetime, timedelta
from time import perf_counter

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection

benchmark_url = make_url(os.environ["REPORT_BENCH_DATABASE_URL"])
if (
    benchmark_url.get_backend_name() != "postgresql"
    or not (benchmark_url.database or "").startswith("codex_lb_report_bench_")
    or os.environ.get("REPORT_BENCH_CONFIRM_DISPOSABLE") != benchmark_url.database
):
    raise RuntimeError(
        "Use a disposable PostgreSQL database named codex_lb_report_bench_* and set "
        "REPORT_BENCH_CONFIRM_DISPOSABLE to its exact database name"
    )
os.environ["CODEX_LB_DATABASE_URL"] = os.environ["REPORT_BENCH_DATABASE_URL"]

from app.db.models import Base
from app.db.session import SessionLocal, engine
from app.modules.reports.repository import ReportsRepository
from app.modules.reports.rollup import fold_next_report_slice
from app.modules.reports.service import ReportsService


async def validate_target(conn: AsyncConnection) -> None:
    database = (await conn.execute(text("SELECT current_database()"))).scalar_one()
    populated = (
        await conn.execute(
            text("""
            SELECT EXISTS (
                SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE left(n.nspname, 3) <> 'pg_' AND n.nspname <> 'information_schema'
            )
            """)
        )
    ).scalar_one()
    if database != benchmark_url.database or populated:
        raise RuntimeError("Benchmark target must match the confirmed database and contain no user relations")


async def main():
    async with engine.begin() as conn:
        await validate_target(conn)
        await conn.run_sync(Base.metadata.create_all)
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


if __name__ == "__main__":
    asyncio.run(main())

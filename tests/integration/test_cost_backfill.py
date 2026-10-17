from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, func, select

from app.core.usage.logs import calculated_cost_from_log
from app.db.models import (
    AccountUsageRollup,
    AccountUsageRollupState,
    ApiKey,
    ApiKeyUsageRollup,
    RequestDemandQuarterRollup,
    RequestLog,
    RequestReportHourlyRollup,
    RequestUsageHourlyRollup,
)
from app.db.session import SessionLocal
from app.modules.accounts.usage_rollup import run_fold_pass
from app.modules.accounts.usage_time_rollup import run_hourly_fold_pass
from app.modules.reports.rollup import run_report_fold_pass
from app.modules.request_logs.cost_backfill import backfill_missing_costs
from tests.integration.test_account_usage_rollup import _make_account

pytestmark = pytest.mark.integration
BASE = datetime(2026, 9, 8, 10)
NOW = BASE + timedelta(hours=4)


async def seed():
    async with SessionLocal() as session:
        session.add(_make_account("a", "a@example.com"))
        session.add(ApiKey(id="k", name="test", key_hash="hash", key_prefix="key"))
        await session.flush()
        variants = [
            {},
            {"service_tier": "priority"},
            {"service_tier": "flex"},
            {"deleted_at": BASE},
            {"request_kind": "warmup"},
            {"source": "limit_warmup"},
            {"request_id": "duplicate"},
            {"request_id": "duplicate"},
            {"account_id": None},
            {"output_tokens": None, "reasoning_tokens": 100},
            {"model": "gpt-6-astra-2026-09-04"},
            {"model": "unknown"},
            {"model_source_id": "external"},
            {"model_source_kind": "openai_compatible"},
            {"cost_usd": 0.0},
            {"cost_usd": 7.0, "request_id": "pruned"},
            {"requested_at": NOW, "request_id": "live"},
        ]
        for i, variant in enumerate(variants):
            values: dict[str, object] = dict(
                account_id="a",
                api_key_id="k",
                request_id=f"r{i}",
                requested_at=BASE,
                model="gpt-6-astra",
                input_tokens=1000,
                output_tokens=100,
                cached_input_tokens=500,
                status="success",
                request_kind="normal",
                conversation_id=" \tconversation\n",
                useragent_group="\x1fagent",
            )
            values.update(variant)
            session.add(RequestLog(**values))
        await session.commit()


async def fold():
    await run_fold_pass(now=NOW)
    await run_hourly_fold_pass(now=NOW)
    await run_report_fold_pass(now=NOW)


async def test_backfill_preserves_folded_history_filters_and_idempotency(async_client):
    await seed()
    await fold()
    async with SessionLocal() as session:
        before_counts = {
            table: await session.scalar(select(func.sum(table.request_count)))
            for table in [
                AccountUsageRollup,
                ApiKeyUsageRollup,
                RequestUsageHourlyRollup,
                RequestDemandQuarterRollup,
                RequestReportHourlyRollup,
            ]
        }
        await session.execute(delete(RequestLog).where(RequestLog.request_id == "pruned"))
        await session.commit()
    cursor = 0
    updated = 0
    while True:
        async with SessionLocal() as session:
            batch = await backfill_missing_costs(session, after_id=cursor, limit=3)
        updated += batch.updated
        cursor = batch.last_id
        if batch.scanned < 3:
            break
    assert updated == 12
    async with SessionLocal() as session:
        assert (await backfill_missing_costs(session)).updated == 0
        logs = (await session.scalars(select(RequestLog).order_by(RequestLog.id))).all()
        for log in logs:
            if log.model == "unknown" or log.model_source_id or log.model_source_kind == "openai_compatible":
                assert log.cost_usd is None
            else:
                assert log.cost_usd is not None
        folded = [log for log in logs if log.requested_at < NOW]

        def cost(rows):
            return sum(log.cost_usd or 0 for log in rows)

        normal = [log for log in folded if log.request_kind not in ("warmup", "limit_warmup")]
        account_rows = {
            (log.account_id, log.request_id, log.requested_at): log
            for log in normal
            if log.account_id is not None and log.deleted_at is None
        }
        assert await session.scalar(select(AccountUsageRollup.total_cost_usd)) == pytest.approx(
            7 + cost(account_rows.values())
        )
        assert await session.scalar(select(ApiKeyUsageRollup.total_cost_usd)) == pytest.approx(7 + cost(normal))
        for table in [RequestUsageHourlyRollup, RequestDemandQuarterRollup]:
            assert await session.scalar(select(func.sum(table.cost_usd))) == pytest.approx(7 + cost(folded))
        assert await session.scalar(select(func.sum(RequestUsageHourlyRollup.cost_count))) == 1 + sum(
            log.cost_usd is not None for log in folded
        )
        assert await session.scalar(select(func.sum(RequestReportHourlyRollup.cost_usd))) == pytest.approx(
            7 + cost([log for log in normal if log.source != "limit_warmup"])
        )
        for table, count in before_counts.items():
            assert await session.scalar(select(func.sum(table.request_count))) == count
    response = await async_client.get("/api/request-logs?limit=100")
    assert response.status_code == 200
    rows = response.json()["requests"]
    astra = next(row for row in rows if row["requestId"] == "r0")
    assert astra["costUsd"] == pytest.approx(0.0105)


async def test_failed_mirror_rolls_back_raw_and_all_aggregates(db_setup, monkeypatch):
    await seed()
    await fold()
    import app.modules.request_logs.cost_backfill as module

    original = module._mirror_cost

    async def fail_after_mirror(*args):
        await original(*args)
        raise RuntimeError("interrupted repair")

    monkeypatch.setattr(module, "_mirror_cost", fail_after_mirror)
    async with SessionLocal() as session:
        with pytest.raises(RuntimeError, match="interrupted repair"):
            await backfill_missing_costs(session)
    async with SessionLocal() as session:
        assert await session.scalar(select(RequestLog.cost_usd).where(RequestLog.request_id == "r0")) is None
        assert await session.scalar(select(AccountUsageRollup.total_cost_usd)) == 7.0
        assert await session.scalar(select(func.sum(RequestUsageHourlyRollup.cost_usd))) == 7.0


async def test_watermark_equality_is_lifetime_inclusive_and_hourly_exclusive(db_setup):
    await seed()
    await fold()
    async with SessionLocal() as session:
        state = await session.get(AccountUsageRollupState, 1)
        assert state is not None
        at = state.hourly_folded_through
        session.add(
            RequestLog(
                account_id="a",
                request_id="boundary",
                requested_at=at,
                model="gpt-6-astra",
                input_tokens=1000,
                output_tokens=100,
                status="success",
            )
        )
        await session.commit()
    # Fold lifetime to include the equality row, while hourly stays at the same hour.
    await run_fold_pass(now=NOW)
    async with SessionLocal() as session:
        await backfill_missing_costs(session)
        boundary = await session.scalar(select(RequestLog).where(RequestLog.request_id == "boundary"))
        assert boundary is not None
        assert boundary.cost_usd == calculated_cost_from_log(boundary)

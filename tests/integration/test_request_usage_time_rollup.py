from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select

from app.db.models import AccountUsageRollupState, RequestUsageHourlyRollup
from app.db.session import SessionLocal
from app.modules.accounts.usage_rollup import lock_fold_state
from app.modules.accounts.usage_time_rollup import (
    DIMENSION_SENTINEL,
    HOURLY_BUCKET_SECONDS,
    QUARTER_SLOT_SECONDS,
    HourlyErrorRollupRow,
    HourlyUsageRollupRow,
    QuarterDemandRollupRow,
    RequestUsageTimeRollupRepository,
    from_dimension,
    to_dimension,
)

pytestmark = pytest.mark.integration

_EPOCH = datetime(1970, 1, 1)
_HOUR = 1_753_300_800  # 2025-07-23T20:00:00Z, a whole UTC hour


def _hourly_row(
    bucket_epoch: int = _HOUR,
    *,
    account_id: str = "acc_a",
    api_key_id: str = DIMENSION_SENTINEL,
    model: str = "gpt-5.1-codex",
    service_tier: str = DIMENSION_SENTINEL,
    request_kind: str = "normal",
    is_deleted: bool = False,
    **measures,
) -> HourlyUsageRollupRow:
    return HourlyUsageRollupRow(
        bucket_epoch=bucket_epoch,
        account_id=account_id,
        api_key_id=api_key_id,
        model=model,
        service_tier=service_tier,
        request_kind=request_kind,
        is_deleted=is_deleted,
        **measures,
    )


async def _bootstrap_state() -> None:
    async with SessionLocal() as session:
        await lock_fold_state(session)
        await session.commit()


def test_dimension_sentinel_round_trip():
    assert to_dimension(None) == DIMENSION_SENTINEL
    assert to_dimension("acc_a") == "acc_a"
    assert from_dimension(DIMENSION_SENTINEL) is None
    assert from_dimension("acc_a") == "acc_a"
    assert HOURLY_BUCKET_SECONDS == 3600
    assert QUARTER_SLOT_SECONDS == 900


@pytest.mark.asyncio
async def test_read_without_state_row_returns_no_watermark(db_setup):
    async with SessionLocal() as session:
        repo = RequestUsageTimeRollupRepository(session)
        for rows, watermark in (
            await repo.read_hourly(),
            await repo.read_errors(),
            await repo.read_demand(),
        ):
            assert rows == []
            assert watermark is None


@pytest.mark.asyncio
async def test_bootstrap_state_defaults_hourly_watermark_to_epoch(db_setup):
    # The lifetime bootstrap inserts only (id, folded_through); the hourly
    # watermark must come from the column's server default so pre-existing
    # write paths need no change.
    await _bootstrap_state()
    async with SessionLocal() as session:
        state = (
            await session.execute(select(AccountUsageRollupState).where(AccountUsageRollupState.id == 1))
        ).scalar_one()
        assert state.hourly_folded_through == _EPOCH

        repo = RequestUsageTimeRollupRepository(session)
        rows, watermark = await repo.read_hourly()
        assert rows == []
        assert watermark == _EPOCH


@pytest.mark.asyncio
async def test_hourly_upsert_inserts_then_merge_adds(db_setup):
    await _bootstrap_state()
    row = _hourly_row(
        request_count=3,
        error_count=1,
        input_tokens=1000,
        output_tokens=50,
        reasoning_tokens=20,
        output_or_reasoning_tokens=50,
        cached_input_tokens=800,
        cached_input_tokens_clamped=800,
        cost_usd=0.5,
        cost_count=3,
    )
    async with SessionLocal() as session:
        repo = RequestUsageTimeRollupRepository(session)
        await repo.add_hourly([row])
        await session.commit()
    async with SessionLocal() as session:
        repo = RequestUsageTimeRollupRepository(session)
        await repo.add_hourly([row])
        await session.commit()

    async with SessionLocal() as session:
        rows, watermark = await RequestUsageTimeRollupRepository(session).read_hourly()
        assert watermark == _EPOCH
        assert len(rows) == 1
        merged = rows[0]
        assert merged.bucket_epoch == _HOUR
        assert merged.account_id == "acc_a"
        assert merged.api_key_id == DIMENSION_SENTINEL
        assert merged.is_deleted is False
        assert merged.request_count == 6
        assert merged.error_count == 2
        assert merged.input_tokens == 2000
        assert merged.output_tokens == 100
        assert merged.reasoning_tokens == 40
        assert merged.output_or_reasoning_tokens == 100
        assert merged.cached_input_tokens == 1600
        assert merged.cached_input_tokens_clamped == 1600
        assert merged.cost_usd == pytest.approx(1.0)
        assert merged.cost_count == 6


@pytest.mark.asyncio
async def test_hourly_upsert_premerges_duplicate_keys_in_one_batch(db_setup):
    # PostgreSQL rejects one INSERT..ON CONFLICT touching the same key twice;
    # the repository must pre-merge instead of relying on the dialect.
    await _bootstrap_state()
    async with SessionLocal() as session:
        repo = RequestUsageTimeRollupRepository(session)
        await repo.add_hourly(
            [
                _hourly_row(request_count=1, input_tokens=10),
                _hourly_row(request_count=2, input_tokens=30),
                _hourly_row(request_kind="warmup", request_count=5),
            ]
        )
        await session.commit()

    async with SessionLocal() as session:
        rows, _ = await RequestUsageTimeRollupRepository(session).read_hourly()
        by_kind = {row.request_kind: row for row in rows}
        assert by_kind["normal"].request_count == 3
        assert by_kind["normal"].input_tokens == 40
        assert by_kind["warmup"].request_count == 5


@pytest.mark.asyncio
async def test_dimension_variants_are_distinct_rows(db_setup):
    # is_deleted and the '' sentinels are PK participants: the same hour and
    # model must keep deleted/live and attributed/orphaned traffic separate.
    await _bootstrap_state()
    variants = [
        _hourly_row(request_count=1),
        _hourly_row(request_count=1, is_deleted=True),
        _hourly_row(request_count=1, account_id=DIMENSION_SENTINEL),
        _hourly_row(request_count=1, api_key_id="key_b"),
        _hourly_row(request_count=1, service_tier="flex"),
    ]
    async with SessionLocal() as session:
        repo = RequestUsageTimeRollupRepository(session)
        await repo.add_hourly(variants)
        await session.commit()

    async with SessionLocal() as session:
        rows, _ = await RequestUsageTimeRollupRepository(session).read_hourly()
        assert len(rows) == 5
        assert all(row.request_count == 1 for row in rows)


@pytest.mark.asyncio
async def test_read_range_is_half_open(db_setup):
    await _bootstrap_state()
    hours = [_HOUR, _HOUR + 3600, _HOUR + 7200]
    async with SessionLocal() as session:
        repo = RequestUsageTimeRollupRepository(session)
        await repo.add_hourly([_hourly_row(bucket_epoch=hour, request_count=1) for hour in hours])
        await session.commit()

    async with SessionLocal() as session:
        repo = RequestUsageTimeRollupRepository(session)
        rows, watermark = await repo.read_hourly(since_epoch=_HOUR, until_epoch=_HOUR + 7200)
        assert sorted(row.bucket_epoch for row in rows) == [_HOUR, _HOUR + 3600]
        assert watermark == _EPOCH

        # An empty range still reports the watermark (LEFT JOIN from state).
        rows, watermark = await repo.read_hourly(since_epoch=_HOUR + 10 * 3600)
        assert rows == []
        assert watermark == _EPOCH


@pytest.mark.asyncio
async def test_error_satellite_upsert_and_range_read(db_setup):
    await _bootstrap_state()
    async with SessionLocal() as session:
        repo = RequestUsageTimeRollupRepository(session)
        await repo.add_errors(
            [
                HourlyErrorRollupRow(bucket_epoch=_HOUR, account_id="acc_a", error_code="upstream_500", error_count=2),
                HourlyErrorRollupRow(bucket_epoch=_HOUR, account_id="acc_a", error_code="upstream_500", error_count=3),
                HourlyErrorRollupRow(
                    bucket_epoch=_HOUR + 3600,
                    account_id=DIMENSION_SENTINEL,
                    error_code="timeout",
                    error_count=1,
                ),
            ]
        )
        await session.commit()

    async with SessionLocal() as session:
        repo = RequestUsageTimeRollupRepository(session)
        rows, watermark = await repo.read_errors()
        assert watermark == _EPOCH
        by_code = {(row.bucket_epoch, row.error_code): row for row in rows}
        assert by_code[(_HOUR, "upstream_500")].error_count == 5
        assert by_code[(_HOUR + 3600, "timeout")].account_id == DIMENSION_SENTINEL

        rows, _ = await repo.read_errors(since_epoch=_HOUR + 3600)
        assert [row.error_code for row in rows] == ["timeout"]


@pytest.mark.asyncio
async def test_quarter_demand_upsert_and_range_read(db_setup):
    await _bootstrap_state()
    slot = _HOUR  # any 900-multiple; whole hours are too
    async with SessionLocal() as session:
        repo = RequestUsageTimeRollupRepository(session)
        await repo.add_demand(
            [
                QuarterDemandRollupRow(
                    slot_epoch=slot,
                    account_id="acc_a",
                    request_kind="normal",
                    is_deleted=False,
                    request_count=2,
                    input_tokens=100,
                    output_or_reasoning_tokens=40,
                    cached_input_tokens=80,
                    cost_usd=0.2,
                ),
                QuarterDemandRollupRow(
                    slot_epoch=slot,
                    account_id="acc_a",
                    request_kind="normal",
                    is_deleted=True,
                    request_count=7,
                ),
                QuarterDemandRollupRow(
                    slot_epoch=slot + QUARTER_SLOT_SECONDS,
                    account_id="acc_a",
                    request_kind="warmup",
                    is_deleted=False,
                    request_count=1,
                ),
            ]
        )
        await session.commit()

    async with SessionLocal() as session:
        repo = RequestUsageTimeRollupRepository(session)
        rows, watermark = await repo.read_demand()
        assert watermark == _EPOCH
        assert len(rows) == 3
        by_key = {(row.slot_epoch, row.request_kind, row.is_deleted): row for row in rows}
        assert by_key[(slot, "normal", False)].input_tokens == 100
        assert by_key[(slot, "normal", True)].request_count == 7
        assert by_key[(slot + QUARTER_SLOT_SECONDS, "warmup", False)].request_count == 1

        rows, _ = await repo.read_demand(since_epoch=slot, until_epoch=slot + QUARTER_SLOT_SECONDS)
        assert len(rows) == 2


@pytest.mark.asyncio
async def test_empty_add_batches_are_noops(db_setup):
    async with SessionLocal() as session:
        repo = RequestUsageTimeRollupRepository(session)
        await repo.add_hourly([])
        await repo.add_errors([])
        await repo.add_demand([])
        await session.commit()
    async with SessionLocal() as session:
        count = len((await session.execute(select(RequestUsageHourlyRollup))).scalars().all())
        assert count == 0

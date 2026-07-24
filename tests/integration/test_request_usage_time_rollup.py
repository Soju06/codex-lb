from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, update

import app.modules.accounts.usage_time_rollup as time_rollup_module
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import (
    Account,
    AccountStatus,
    AccountUsageRollupState,
    RequestLog,
    RequestUsageHourlyRollup,
)
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.usage_rollup import FOLD_LAG, lock_fold_state
from app.modules.accounts.usage_time_rollup import (
    DIMENSION_SENTINEL,
    HOURLY_BUCKET_SECONDS,
    QUARTER_SLOT_SECONDS,
    HourlyErrorRollupRow,
    HourlyUsageRollupRow,
    QuarterDemandRollupRow,
    RequestUsageTimeRollupRepository,
    epoch_seconds,
    floor_to_hour,
    from_dimension,
    run_hourly_fold_pass,
    to_dimension,
)
from app.modules.request_logs.repository import RequestLogsRepository

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
                    api_key_id="key_1",
                    model="gpt-5.1-codex",
                    reasoning_effort="",
                    request_kind="normal",
                    status="success",
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
                    api_key_id="key_1",
                    model="gpt-5.1-codex",
                    reasoning_effort="",
                    request_kind="normal",
                    status="success",
                    is_deleted=True,
                    request_count=7,
                ),
                QuarterDemandRollupRow(
                    slot_epoch=slot + QUARTER_SLOT_SECONDS,
                    account_id="acc_a",
                    api_key_id="",
                    model="gpt-5.1-codex",
                    reasoning_effort="medium",
                    request_kind="warmup",
                    status="success",
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


# --- Hourly fold pass ------------------------------------------------------


def _make_account(account_id: str, email: str, chatgpt_account_id: str | None = None) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email,
        plan_type="plus",
        chatgpt_account_id=chatgpt_account_id,
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


async def _add_log(
    logs_repo: RequestLogsRepository,
    *,
    account_id: str | None,
    request_id: str,
    requested_at: datetime,
    input_tokens: int | None = 100,
    output_tokens: int | None = 50,
    reasoning_tokens: int | None = None,
    cached_input_tokens: int | None = 0,
    cost_usd: float | None = 0.01,
    status: str = "success",
    error_code: str | None = None,
    request_kind: str = "normal",
    service_tier: str | None = None,
    api_key_id: str | None = None,
    model: str = "gpt-5.1-codex",
):
    return await logs_repo.add_log(
        account_id=account_id,
        request_id=request_id,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        cached_input_tokens=cached_input_tokens,
        latency_ms=100,
        status=status,
        error_code=error_code,
        requested_at=requested_at,
        cost_usd=cost_usd,
        request_kind=request_kind,
        service_tier=service_tier,
        api_key_id=api_key_id,
    )


async def _add_orphan_deleted_log(
    session,
    *,
    request_id: str,
    requested_at: datetime,
    status: str = "success",
    error_code: str | None = None,
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> None:
    session.add(
        RequestLog(
            account_id=None,
            request_id=request_id,
            model="gpt-5.1-codex",
            status=status,
            error_code=error_code,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            requested_at=requested_at,
            deleted_at=requested_at + timedelta(hours=1),
        )
    )
    await session.commit()


async def _dump_all_rollups():
    async with SessionLocal() as session:
        repo = RequestUsageTimeRollupRepository(session)
        hourly, watermark = await repo.read_hourly()
        errors, _ = await repo.read_errors()
        demand, _ = await repo.read_demand()
        return (
            sorted(
                hourly,
                key=lambda r: (
                    r.bucket_epoch,
                    *map(str, (r.account_id, r.api_key_id, r.model, r.service_tier, r.request_kind, r.is_deleted)),
                ),
            ),
            sorted(errors, key=lambda r: (r.bucket_epoch, r.account_id, r.error_code)),
            sorted(
                demand,
                key=lambda r: (
                    r.slot_epoch,
                    r.account_id,
                    r.api_key_id,
                    r.model,
                    r.reasoning_effort,
                    r.request_kind,
                    r.status,
                    str(r.is_deleted),
                ),
            ),
            watermark,
        )


def _hourly_target(now: datetime) -> datetime:
    return floor_to_hour(now - FOLD_LAG)


@pytest.mark.asyncio
async def test_hourly_fold_folds_dimensions_and_measures(db_setup):
    now = utcnow()
    hour0 = floor_to_hour(now - timedelta(days=3))
    hour1 = hour0 + timedelta(hours=1)
    hour2 = hour0 + timedelta(hours=2)
    hour3 = hour0 + timedelta(hours=3)
    hour4 = hour0 + timedelta(hours=4)
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(_make_account("acc_f", "fold-ts@example.com"))
        logs = RequestLogsRepository(session)
        # hour0, slot 0: success with cached tokens (clamped to input).
        await _add_log(
            logs,
            account_id="acc_f",
            request_id="r_a",
            requested_at=hour0 + timedelta(seconds=300),
            input_tokens=100,
            output_tokens=50,
            cached_input_tokens=120,
            cost_usd=0.01,
        )
        # hour0, slot 1: reasoning-only error row with NULL cost/cached.
        # Inserted directly: add_log always derives a cost, and a true NULL
        # cost row is what exercises the cost_count fold semantics.
        session.add(
            RequestLog(
                account_id="acc_f",
                request_id="r_b",
                model="gpt-5.1-codex",
                status="error",
                error_code="upstream_500",
                input_tokens=200,
                output_tokens=None,
                reasoning_tokens=30,
                cached_input_tokens=None,
                cost_usd=None,
                requested_at=hour0 + timedelta(seconds=960),
            )
        )
        await session.commit()
        # hour1: warmup kind is folded verbatim (reads filter by dimension),
        # plus a cached>input clamp case.
        await _add_log(
            logs,
            account_id="acc_f",
            request_id="r_warm",
            requested_at=hour1 + timedelta(seconds=60),
            request_kind="warmup",
            input_tokens=777,
        )
        await _add_log(
            logs,
            account_id="acc_f",
            request_id="r_clamp",
            requested_at=hour1 + timedelta(seconds=120),
            input_tokens=10,
            output_tokens=20,
            cached_input_tokens=50,
            cost_usd=0.02,
        )
        # hour1, distinct model bucket: NULL input with cached tokens — the
        # clamp keeps the cached value (`cached_input_tokens_from_log` only
        # clamps to input when input is present), it must not zero it.
        await _add_log(
            logs,
            account_id="acc_f",
            request_id="r_null_input",
            requested_at=hour1 + timedelta(seconds=180),
            model="gpt-5.3-mini",
            input_tokens=None,
            output_tokens=8,
            cached_input_tokens=30,
            cost_usd=0.001,
        )
        # hour2: service_tier and api_key_id dimensions.
        await _add_log(
            logs,
            account_id="acc_f",
            request_id="r_tier",
            requested_at=hour2 + timedelta(seconds=60),
            service_tier="flex",
            api_key_id="key_1",
        )
        # hour3: duplicate rows sharing (account, request_id, requested_at):
        # the hourly fold does NOT dedupe (#904 dedupe is a lifetime-rollup
        # semantic); both raw rows count, matching the raw read paths.
        dup_at = hour3 + timedelta(seconds=90)
        await _add_log(logs, account_id="acc_f", request_id="r_dup", requested_at=dup_at, input_tokens=5)
        await _add_log(logs, account_id="acc_f", request_id="r_dup", requested_at=dup_at, input_tokens=7)
        # hour4: orphaned soft-deleted error row (NULL account, deleted_at
        # set) — counted under the ('' , is_deleted) dimensions, and INCLUDED
        # in the error satellite (top-error reads include deleted rows).
        await _add_orphan_deleted_log(
            session,
            request_id="r_orphan",
            requested_at=hour4 + timedelta(seconds=30),
            status="error",
            error_code="timeout",
        )
        # Live tail: young row stays unfolded.
        await _add_log(logs, account_id="acc_f", request_id="r_new", requested_at=now, input_tokens=1)

    committed = await run_hourly_fold_pass(now=now)
    assert committed >= 1

    hourly, errors, demand, watermark = await _dump_all_rollups()
    assert watermark == _hourly_target(now)
    assert epoch_seconds(watermark) % HOURLY_BUCKET_SECONDS == 0

    by_key = {
        (r.bucket_epoch, r.account_id, r.api_key_id, r.model, r.service_tier, r.request_kind, r.is_deleted): r
        for r in hourly
    }
    h0 = by_key[(epoch_seconds(hour0), "acc_f", "", "gpt-5.1-codex", "", "normal", False)]
    assert h0.request_count == 2
    assert h0.error_count == 1
    assert h0.input_tokens == 300
    assert h0.output_tokens == 50
    assert h0.reasoning_tokens == 30
    assert h0.output_or_reasoning_tokens == 50 + 30
    assert h0.cached_input_tokens == 120
    assert h0.cached_input_tokens_clamped == 100  # min(120, 100) + 0
    assert h0.cost_usd == pytest.approx(0.01)
    assert h0.cost_count == 1

    warm = by_key[(epoch_seconds(hour1), "acc_f", "", "gpt-5.1-codex", "", "warmup", False)]
    assert warm.request_count == 1
    assert warm.input_tokens == 777

    clamp = by_key[(epoch_seconds(hour1), "acc_f", "", "gpt-5.1-codex", "", "normal", False)]
    assert clamp.cached_input_tokens == 50
    assert clamp.cached_input_tokens_clamped == 10  # min(50, 10)

    null_input = by_key[(epoch_seconds(hour1), "acc_f", "", "gpt-5.3-mini", "", "normal", False)]
    assert null_input.input_tokens == 0
    assert null_input.cached_input_tokens == 30
    assert null_input.cached_input_tokens_clamped == 30  # NULL input keeps cached

    tier = by_key[(epoch_seconds(hour2), "acc_f", "key_1", "gpt-5.1-codex", "flex", "normal", False)]
    assert tier.request_count == 1

    dup = by_key[(epoch_seconds(hour3), "acc_f", "", "gpt-5.1-codex", "", "normal", False)]
    assert dup.request_count == 2
    assert dup.input_tokens == 12

    orphan = by_key[(epoch_seconds(hour4), "", "", "gpt-5.1-codex", "", "normal", True)]
    assert orphan.request_count == 1
    assert orphan.error_count == 1

    # No row for the live-tail log's hour.
    tail_bucket = epoch_seconds(floor_to_hour(now))
    assert not any(r.bucket_epoch == tail_bucket for r in hourly)

    error_keys = {(r.bucket_epoch, r.account_id, r.error_code): r.error_count for r in errors}
    assert error_keys == {
        (epoch_seconds(hour0), "acc_f", "upstream_500"): 1,
        (epoch_seconds(hour4), "", "timeout"): 1,
    }

    # Demand keeps the FULL legacy grain (slot, account, api_key, model,
    # reasoning_effort, kind, status, is_deleted): `_bin_demand_units` takes
    # max() per bin, so a coarser fold would change forecasts.
    demand_keys = {
        (
            r.slot_epoch,
            r.account_id,
            r.api_key_id,
            r.model,
            r.reasoning_effort,
            r.request_kind,
            r.status,
            r.is_deleted,
        ): r
        for r in demand
    }
    slot_a = demand_keys[(epoch_seconds(hour0), "acc_f", "", "gpt-5.1-codex", "", "normal", "success", False)]
    assert slot_a.request_count == 1
    assert slot_a.input_tokens == 100
    assert slot_a.output_or_reasoning_tokens == 50
    assert slot_a.cached_input_tokens == 120
    assert slot_a.cost_usd == pytest.approx(0.01)
    # Same slot arithmetic, but the error row lands in its own bin (status
    # is a demand dimension).
    slot_b = demand_keys[
        (epoch_seconds(hour0) + QUARTER_SLOT_SECONDS, "acc_f", "", "gpt-5.1-codex", "", "normal", "error", False)
    ]
    assert slot_b.request_count == 1
    assert slot_b.output_or_reasoning_tokens == 30
    assert (epoch_seconds(hour1), "acc_f", "", "gpt-5.1-codex", "", "warmup", "success", False) in demand_keys
    assert (epoch_seconds(hour2), "acc_f", "key_1", "gpt-5.1-codex", "", "normal", "success", False) in demand_keys
    assert (epoch_seconds(hour4), "", "", "gpt-5.1-codex", "", "normal", "error", True) in demand_keys


@pytest.mark.asyncio
async def test_hourly_fold_is_idempotent(db_setup):
    now = utcnow()
    hour = floor_to_hour(now - timedelta(days=2))
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(_make_account("acc_idem_ts", "idem-ts@example.com"))
        logs = RequestLogsRepository(session)
        await _add_log(logs, account_id="acc_idem_ts", request_id="r_1", requested_at=hour + timedelta(seconds=5))
        await _add_log(
            logs,
            account_id="acc_idem_ts",
            request_id="r_2",
            requested_at=hour + timedelta(seconds=10),
            status="error",
            error_code="boom",
        )

    assert await run_hourly_fold_pass(now=now) >= 1
    first = await _dump_all_rollups()
    assert await run_hourly_fold_pass(now=now) == 0
    assert await _dump_all_rollups() == first


@pytest.mark.asyncio
async def test_model_rewrite_skips_folded_rows(db_setup):
    """`update_model_for_request` must never rewrite rows below a rollup
    watermark: model is a folded dimension and cost a folded measure, so a
    pre-watermark rewrite (a client-reused request id colliding with old
    traffic) would silently diverge the permanent rollups from raw."""
    now = utcnow()
    old_at = floor_to_hour(now - timedelta(days=3)) + timedelta(seconds=30)
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(_make_account("acc_rw", "rewrite-ts@example.com"))
        logs = RequestLogsRepository(session)
        await _add_log(logs, account_id="acc_rw", request_id="r_rw", requested_at=old_at)
        await _add_log(logs, account_id="acc_rw", request_id="r_rw", requested_at=now)

    assert await run_hourly_fold_pass(now=now) >= 1
    # The bound is min(lifetime, hourly) watermark; advance the lifetime one
    # to match so the hourly watermark is what gates the rewrite.
    async with SessionLocal() as session:
        state = (
            await session.execute(select(AccountUsageRollupState).where(AccountUsageRollupState.id == 1))
        ).scalar_one()
        await session.execute(
            update(AccountUsageRollupState)
            .where(AccountUsageRollupState.id == 1)
            .values(folded_through=state.hourly_folded_through)
        )
        await session.commit()

    async with SessionLocal() as session:
        updated = await RequestLogsRepository(session).update_model_for_request("r_rw", "gpt-image-1")
    assert updated == 1  # the live-tail row only

    async with SessionLocal() as session:
        models_by_age = dict((await session.execute(select(RequestLog.requested_at, RequestLog.model))).all())
    assert models_by_age[old_at] == "gpt-5.1-codex"
    assert models_by_age[now] == "gpt-image-1"

    # The folded hourly bucket still carries the original model dimension.
    hourly, _, _, _ = await _dump_all_rollups()
    assert {r.model for r in hourly} == {"gpt-5.1-codex"}

    # A rewrite matching nothing must release the fold-state lock cleanly
    # (regression guard for the early-return path).
    async with SessionLocal() as session:
        assert await RequestLogsRepository(session).update_model_for_request("r_missing", "gpt-image-1") == 0
    assert await run_hourly_fold_pass(now=now) == 0


@pytest.mark.asyncio
async def test_hourly_fold_crash_resumes_without_double_counting(db_setup, monkeypatch):
    """A crash between slice commits must resume exactly where it left off:
    the committed prefix stays, the interrupted slice re-runs from scratch
    (DELETE-then-INSERT), and the final state equals an uninterrupted run."""
    monkeypatch.setattr(time_rollup_module, "TS_FOLD_SLICE", timedelta(hours=24))
    now = utcnow()
    seeded = 0
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(_make_account("acc_crash", "crash-ts@example.com"))
        logs = RequestLogsRepository(session)
        for day in (6, 5, 4, 3, 2):
            hour = floor_to_hour(now - timedelta(days=day))
            await _add_log(
                logs,
                account_id="acc_crash",
                request_id=f"r_d{day}",
                requested_at=hour + timedelta(seconds=60),
                input_tokens=100,
            )
            seeded += 1

    original = time_rollup_module._fold_next_hourly_slice
    calls = {"count": 0}

    async def _flaky(session, target):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("injected crash")
        return await original(session, target)

    monkeypatch.setattr(time_rollup_module, "_fold_next_hourly_slice", _flaky)
    with pytest.raises(RuntimeError, match="injected crash"):
        await run_hourly_fold_pass(now=now)

    _, _, _, watermark_after_crash = await _dump_all_rollups()
    assert watermark_after_crash is not None
    assert datetime(1970, 1, 1) < watermark_after_crash < _hourly_target(now)

    monkeypatch.setattr(time_rollup_module, "_fold_next_hourly_slice", original)
    assert await run_hourly_fold_pass(now=now) >= 1

    hourly, _, demand, watermark = await _dump_all_rollups()
    assert watermark == _hourly_target(now)
    assert sum(r.request_count for r in hourly) == seeded
    assert sum(r.input_tokens for r in hourly) == seeded * 100
    assert sum(r.request_count for r in demand) == seeded

    # And the resumed state is a fixed point: re-running changes nothing.
    resumed = await _dump_all_rollups()
    assert await run_hourly_fold_pass(now=now) == 0
    assert await _dump_all_rollups() == resumed


@pytest.mark.asyncio
async def test_hourly_backfill_progresses_across_capped_passes(db_setup, monkeypatch):
    """The per-pass slice cap paces the initial backfill: one pass folds at
    most TS_MAX_SLICES_PER_PASS slices and the next pass resumes from the
    committed watermark until history is exhausted."""
    monkeypatch.setattr(time_rollup_module, "TS_FOLD_SLICE", timedelta(hours=24))
    monkeypatch.setattr(time_rollup_module, "TS_MAX_SLICES_PER_PASS", 2)
    now = utcnow()
    seeded = 0
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(_make_account("acc_pace", "pace-ts@example.com"))
        logs = RequestLogsRepository(session)
        for day in (8, 7, 6, 5, 4, 3, 2):
            hour = floor_to_hour(now - timedelta(days=day))
            await _add_log(
                logs, account_id="acc_pace", request_id=f"r_p{day}", requested_at=hour + timedelta(seconds=30)
            )
            seeded += 1

    committed_first = await run_hourly_fold_pass(now=now)
    assert committed_first == 2  # capped
    _, _, _, watermark = await _dump_all_rollups()
    assert watermark < _hourly_target(now)  # not done yet

    passes = 1
    while watermark < _hourly_target(now):
        assert passes < 10, "backfill did not converge"
        committed = await run_hourly_fold_pass(now=now)
        assert committed >= 1
        _, _, _, watermark = await _dump_all_rollups()
        passes += 1

    hourly, _, _, _ = await _dump_all_rollups()
    assert sum(r.request_count for r in hourly) == seeded
    assert await run_hourly_fold_pass(now=now) == 0


@pytest.mark.asyncio
async def test_hourly_fold_boundary_attribution_and_lag(db_setup):
    now = utcnow()
    target = _hourly_target(now)
    boundary = floor_to_hour(now - timedelta(days=2))
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(_make_account("acc_edge", "edge-ts@example.com"))
        logs = RequestLogsRepository(session)
        # Exactly on an hour boundary: belongs to the bucket it STARTS.
        await _add_log(logs, account_id="acc_edge", request_id="r_on", requested_at=boundary)
        # Last instant of the previous bucket.
        await _add_log(logs, account_id="acc_edge", request_id="r_before", requested_at=boundary - timedelta(seconds=1))
        # Exactly AT the fold target: half-open [start, target) leaves it in
        # the live tail.
        await _add_log(logs, account_id="acc_edge", request_id="r_at_target", requested_at=target)
        # Younger than FOLD_LAG: untouched.
        await _add_log(logs, account_id="acc_edge", request_id="r_young", requested_at=now - timedelta(hours=2))

    await run_hourly_fold_pass(now=now)

    hourly, _, _, watermark = await _dump_all_rollups()
    assert watermark == target
    buckets = {r.bucket_epoch: r.request_count for r in hourly}
    assert buckets == {
        epoch_seconds(boundary): 1,
        epoch_seconds(boundary) - HOURLY_BUCKET_SECONDS: 1,
    }


@pytest.mark.asyncio
async def test_hourly_fold_empty_history_advances_watermark(db_setup):
    """No raw rows below the target: the pass advances the watermark in one
    hop (keeping readers' tail windows and the retention min-gate current)
    without writing any rollup rows."""
    now = utcnow()
    assert await run_hourly_fold_pass(now=now) == 1
    hourly, errors, demand, watermark = await _dump_all_rollups()
    assert (hourly, errors, demand) == ([], [], [])
    assert watermark == _hourly_target(now)
    assert await run_hourly_fold_pass(now=now) == 0


@pytest.mark.asyncio
async def test_hourly_fold_jumps_empty_prefix_and_gaps(db_setup):
    """Sparse history (empty prefix before the first row, week-long gap in
    the middle) folds in a handful of slices — passes never walk empty
    48-hour windows one by one."""
    now = utcnow()
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(_make_account("acc_gap", "gap-ts@example.com"))
        logs = RequestLogsRepository(session)
        await _add_log(
            logs,
            account_id="acc_gap",
            request_id="r_ancient",
            requested_at=floor_to_hour(now - timedelta(days=400)) + timedelta(seconds=10),
        )
        await _add_log(
            logs,
            account_id="acc_gap",
            request_id="r_recent",
            requested_at=floor_to_hour(now - timedelta(days=2)) + timedelta(seconds=10),
        )

    # Slice 1 covers the ancient row, slice 2 jumps the ~398-day gap to the
    # recent row, slice 3 advances the watermark to the target: 3 commits,
    # not ~200 empty windows.
    committed = await run_hourly_fold_pass(now=now)
    assert committed <= 3
    hourly, _, _, watermark = await _dump_all_rollups()
    assert watermark == _hourly_target(now)
    assert sum(r.request_count for r in hourly) == 2


# --- Account lifecycle mirrors --------------------------------------------


async def _seed_account_history(account_id: str, email: str, now: datetime, *, chatgpt_account_id=None) -> int:
    """Seed an account with two-day-old history that populates all three
    rollup tables once folded (a success row, and an error row carrying an
    api_key dimension). Returns the seeded request count. Does NOT fold —
    the watermark only moves forward, so callers must seed everything before
    the first fold."""
    hour = floor_to_hour(now - timedelta(days=2))
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(
            _make_account(account_id, email, chatgpt_account_id=chatgpt_account_id), merge_by_email=False
        )
        logs = RequestLogsRepository(session)
        await _add_log(
            logs,
            account_id=account_id,
            request_id=f"r_{account_id}_1",
            requested_at=hour + timedelta(seconds=30),
            input_tokens=100,
        )
        await _add_log(
            logs,
            account_id=account_id,
            request_id=f"r_{account_id}_2",
            requested_at=hour + timedelta(seconds=60),
            input_tokens=200,
            status="error",
            error_code="upstream_500",
            api_key_id="key_life",
        )
    return 2


@pytest.mark.asyncio
async def test_account_soft_delete_mirrors_folded_buckets(db_setup):
    """Soft account deletion retroactively detaches the account's WHOLE raw
    history (account_id=NULL, deleted_at=now); the folded buckets must move
    to the ('' , is_deleted=true) dimension — merged onto any pre-existing
    orphan bucket — or the time series and the (possibly pruned) raw diverge
    forever."""
    now = utcnow()
    hour = floor_to_hour(now - timedelta(days=2))
    # Pre-existing orphaned-deleted bucket in the SAME hour/model/kind (must
    # exist BEFORE the fold — the watermark only moves forward): the mirror
    # must merge-add onto it, not collide with it.
    async with SessionLocal() as session:
        await _add_orphan_deleted_log(
            session, request_id="r_pre_orphan", requested_at=hour + timedelta(seconds=90), input_tokens=7
        )
    await _seed_account_history("acc_soft", "soft-ts@example.com", now)
    await run_hourly_fold_pass(now=now)

    hourly_before, errors_before, demand_before, _ = await _dump_all_rollups()
    total_before = sum(r.request_count for r in hourly_before)
    error_total_before = sum(r.error_count for r in errors_before)

    async with SessionLocal() as session:
        assert await AccountsRepository(session).delete("acc_soft")

    hourly, errors, demand, _ = await _dump_all_rollups()
    # Totals preserved, no account-attributed rows left.
    assert sum(r.request_count for r in hourly) == total_before
    assert all(r.account_id == "" and r.is_deleted for r in hourly)
    merged = {r.api_key_id: r for r in hourly}
    assert merged[""].request_count == 2  # orphan(1) + folded acc row(1)
    assert merged[""].input_tokens == 100 + 7
    assert merged["key_life"].request_count == 1

    assert sum(r.error_count for r in errors) == error_total_before
    assert all(r.account_id == "" for r in errors)

    assert all(r.account_id == "" and r.is_deleted for r in demand)
    assert sum(r.request_count for r in demand) == total_before


@pytest.mark.asyncio
async def test_account_hard_delete_removes_folded_buckets(db_setup):
    now = utcnow()
    await _seed_account_history("acc_hard", "hard-ts@example.com", now)
    await run_hourly_fold_pass(now=now)
    hourly, errors, demand, _ = await _dump_all_rollups()
    assert hourly and errors and demand

    async with SessionLocal() as session:
        assert await AccountsRepository(session).delete("acc_hard", delete_history=True)

    hourly, errors, demand, _ = await _dump_all_rollups()
    assert (hourly, errors, demand) == ([], [], [])


@pytest.mark.asyncio
async def test_identity_merge_mirrors_folded_buckets(db_setup):
    """Duplicate-account consolidation reassigns the duplicate's raw logs to
    the canonical account; folded buckets must follow bucket-wise."""
    now = utcnow()
    await _seed_account_history("acc_can", "merge-ts@example.com", now, chatgpt_account_id="chatgpt_ts")
    await _seed_account_history("acc_can__copy", "merge-ts@example.com", now, chatgpt_account_id="chatgpt_ts")
    await run_hourly_fold_pass(now=now)

    async with SessionLocal() as session:
        reauth = _make_account("acc_can", "merge-ts@example.com", chatgpt_account_id="chatgpt_ts")
        saved = await AccountsRepository(session).upsert(reauth, merge_by_email=False, merge_by_chatgpt_identity=True)
        assert saved.id == "acc_can"

    hourly, errors, demand, _ = await _dump_all_rollups()
    assert {r.account_id for r in hourly} == {"acc_can"}
    assert {r.account_id for r in errors} == {"acc_can"}
    assert {r.account_id for r in demand} == {"acc_can"}
    # Same hour/dims from both accounts merged bucket-wise: totals add up.
    assert sum(r.request_count for r in hourly) == 4
    assert sum(r.error_count for r in errors) == 2


@pytest.mark.asyncio
async def test_soft_delete_racing_hourly_fold_loses_no_usage(db_setup):
    """Account deletion and a concurrent hourly fold serialize on the
    fold-state row lock: whichever commits first, every raw row's
    contribution ends up under the orphaned-deleted dimension after the next
    fold — never attributed to the deleted account, never dropped."""
    now = utcnow()
    hour = floor_to_hour(now - timedelta(days=2))
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(_make_account("acc_race_ts", "race-ts@example.com"))
        logs = RequestLogsRepository(session)
        for index in range(4):
            await _add_log(
                logs,
                account_id="acc_race_ts",
                request_id=f"r_race_{index}",
                requested_at=hour + timedelta(minutes=index),
                input_tokens=250,
            )

    async def _delete():
        async with SessionLocal() as session:
            await AccountsRepository(session).delete("acc_race_ts")

    await asyncio.gather(run_hourly_fold_pass(now=now), _delete())
    # A second fold covers the ordering where the delete landed first (the
    # then-unfolded rows are folded from their post-delete raw state).
    await run_hourly_fold_pass(now=now)

    hourly, _, demand, _ = await _dump_all_rollups()
    assert sum(r.request_count for r in hourly) == 4
    assert sum(r.input_tokens for r in hourly) == 1000
    assert all(r.account_id == "" and r.is_deleted for r in hourly)
    assert sum(r.request_count for r in demand) == 4


@pytest.mark.asyncio
async def test_rewound_watermark_refold_converges(db_setup):
    """Escape hatch (spec: 'A rewound watermark self-heals'): resetting
    `hourly_folded_through` to epoch while raw history still exists makes the
    next passes re-fold to EXACTLY the same table contents — the defensive
    per-slice DELETE prevents both double counting and stale leftovers."""
    from sqlalchemy import update as sa_update

    now = utcnow()
    hour = floor_to_hour(now - timedelta(days=2))
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(_make_account("acc_rewind", "rewind-ts@example.com"))
        logs = RequestLogsRepository(session)
        await _add_log(logs, account_id="acc_rewind", request_id="r_rw_1", requested_at=hour + timedelta(seconds=10))
        await _add_log(
            logs,
            account_id="acc_rewind",
            request_id="r_rw_2",
            requested_at=hour + timedelta(seconds=20),
            status="error",
            error_code="boom",
        )

    await run_hourly_fold_pass(now=now)
    baseline = await _dump_all_rollups()

    async with SessionLocal() as session:
        await session.execute(
            sa_update(AccountUsageRollupState)
            .where(AccountUsageRollupState.id == 1)
            .values(hourly_folded_through=datetime(1970, 1, 1))
        )
        await session.commit()

    assert await run_hourly_fold_pass(now=now) >= 1
    assert await _dump_all_rollups() == baseline

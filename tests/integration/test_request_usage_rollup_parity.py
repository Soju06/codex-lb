"""Parity harness for the rollup-backed read paths (the hard contract):

every switched reader must return EXACTLY what the legacy raw-scanning
reader returned, for any watermark position. The reference snapshot is taken
at watermark = epoch — in that state the switched code paths degrade to the
identical single raw query the legacy readers ran — and re-asserted after
folding to a mid-history hour and to the full target, plus under a
concurrently-advancing fold, after the operator escape hatch, and after
retention physically pruned the folded raw rows (the headline: statistics
survive raw deletion; conversation metrics are expectedly raw-bound).
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, select, update

import app.modules.request_logs.repository as request_logs_repository_module
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import (
    Account,
    AccountStatus,
    AccountUsageRollupState,
    RequestDemandQuarterRollup,
    RequestLog,
    RequestUsageHourlyErrorRollup,
    RequestUsageHourlyRollup,
)
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.usage_rollup import FOLD_LAG
from app.modules.accounts.usage_time_rollup import floor_to_hour, run_hourly_fold_pass
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.quota_planner.repository import QuotaPlannerRepository
from app.modules.request_logs.repository import RequestLogsRepository

pytestmark = pytest.mark.integration

_EPOCH = datetime(1970, 1, 1)

# Fixed 10-day corpus timeline (all naive UTC, matching requested_at).
BASE = datetime(2025, 7, 1)
NOW = BASE + timedelta(days=10, minutes=37)
TARGET_W = floor_to_hour(NOW - FOLD_LAG)  # BASE + 9d
MID_W = BASE + timedelta(days=5, hours=3)  # whole hour mid-history

SINCE_ALIGNED = BASE + timedelta(days=2)
SINCE_UNALIGNED = BASE + timedelta(days=2, minutes=30)
UNTIL_UNALIGNED = BASE + timedelta(days=8, hours=5, minutes=45)
FOLDED_ONLY_WINDOW = (BASE + timedelta(days=1), BASE + timedelta(days=4))
TAIL_ONLY_WINDOW = (TARGET_W + timedelta(minutes=10), NOW)
# Past the last corpus error row; only a success edge row lives here.
EMPTY_ERROR_WINDOW = (BASE + timedelta(days=9, hours=22), NOW)

_MODELS = ("gpt-5.1-codex", "gpt-5.3-mini")
_TIERS = (None, "flex", "priority")
_KEYS = (None, "key_1", "key_2")
_ACCOUNTS = ("acc_par_a", "acc_par_b", None)


def _make_account(account_id: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=f"{account_id}@example.com",
        plan_type="pro",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


def _log(requested_at: datetime, **overrides) -> RequestLog:
    values = {
        "account_id": "acc_par_a",
        "api_key_id": "key_1",
        "request_id": f"r_{requested_at.isoformat()}_{overrides.get('request_id_suffix', '')}",
        "model": "gpt-5.1-codex",
        "service_tier": None,
        "request_kind": "normal",
        "status": "success",
        "error_code": None,
        "input_tokens": 100,
        "output_tokens": 50,
        "reasoning_tokens": None,
        "cached_input_tokens": 20,
        "cost_usd": 0.01,
        "conversation_id": None,
        "requested_at": requested_at,
        "deleted_at": None,
    }
    overrides.pop("request_id_suffix", None)
    values.update(overrides)
    return RequestLog(**values)


def _corpus() -> list[RequestLog]:
    rows: list[RequestLog] = []
    # Deterministic bulk spread: 8 rows per day for 10 days, cycling every
    # dimension, hitting hour boundaries and odd offsets alike.
    for day in range(10):
        for slot in range(8):
            index = day * 8 + slot
            at = BASE + timedelta(days=day, hours=slot * 3, minutes=(index * 7) % 60, seconds=(index * 13) % 60)
            rows.append(
                _log(
                    at,
                    request_id_suffix=str(index),
                    account_id=_ACCOUNTS[index % 3],
                    api_key_id=_KEYS[index % 3],
                    model=_MODELS[index % 2],
                    service_tier=_TIERS[index % 3],
                    input_tokens=100 + index,
                    output_tokens=None if index % 5 == 0 else 40 + index,
                    reasoning_tokens=17 + index if index % 5 == 0 else None,
                    cached_input_tokens=None if index % 7 == 0 else (index * 11) % 260,
                    cost_usd=None if index % 6 == 0 else 0.001 * (index % 9),
                    conversation_id=("conv_a", "conv_b", None, " \t")[index % 4],
                    status="error" if index % 4 == 3 else "success",
                    error_code=("e_rate", "e_upstream", "e_zzz")[index % 3] if index % 4 == 3 else None,
                )
            )
    # Warmup kinds (excluded by the dashboard readers, folded verbatim,
    # counted by the planner) on both sides of every candidate watermark.
    for offset in (timedelta(hours=30), timedelta(days=5, hours=1), timedelta(days=9, hours=20)):
        rows.append(_log(BASE + offset, request_id_suffix="w", request_kind="warmup", cost_usd=0.002))
        rows.append(_log(BASE + offset + timedelta(minutes=20), request_id_suffix="lw", request_kind="limit_warmup"))
    # Soft-deleted orphans (account detached): kept by dashboard buckets and
    # top-error, excluded by the planner.
    for offset in (timedelta(days=1, hours=2), timedelta(days=6, hours=7, minutes=31)):
        rows.append(
            _log(
                BASE + offset,
                request_id_suffix="del",
                account_id=None,
                api_key_id=None,
                deleted_at=BASE + offset + timedelta(hours=1),
                status="error",
                error_code="e_deleted",
                cost_usd=0.5,
            )
        )
    # cached > input clamp candidate and an error-code tie: e_rate and
    # e_upstream get one extra hit each inside the unaligned window so the
    # deterministic (count desc, code asc) tie-break is exercised.
    rows.append(
        _log(BASE + timedelta(days=3, minutes=1), request_id_suffix="c", input_tokens=10, cached_input_tokens=99)
    )
    # NULL input with cached tokens: the folded clamp keeps the cached value
    # (cached_input_tokens_from_log only clamps when input is present).
    rows.append(
        _log(
            BASE + timedelta(days=3, minutes=7),
            request_id_suffix="ci",
            input_tokens=None,
            output_tokens=12,
            cached_input_tokens=44,
        )
    )
    # Empty-string dimensions: legitimate values the legacy GROUP BY keeps
    # distinct from NULL — the fold's collision-free encoding must preserve
    # the split (buckets by service_tier, demand bins by reasoning_effort).
    # A sentinel-prefixed tier exercises the SQL escape branch. One cluster
    # sits below every candidate watermark (folded), one in the live tail;
    # each cluster shares one hour bucket AND one 900s demand slot.
    for offset in (timedelta(days=2, hours=4), timedelta(days=9, hours=21)):
        rows.append(
            _log(
                BASE + offset + timedelta(minutes=7),
                request_id_suffix="esc",
                service_tier="\x1fodd",
                cost_usd=0.02,
            )
        )
        rows.append(
            _log(
                BASE + offset + timedelta(minutes=11),
                request_id_suffix="empty",
                service_tier="",
                reasoning_effort="",
                cost_usd=0.03,
            )
        )
        rows.append(
            _log(
                BASE + offset + timedelta(minutes=13),
                request_id_suffix="nulldim",
                service_tier=None,
                reasoning_effort=None,
                cost_usd=0.04,
            )
        )
    # UNaligned earliest row: while it survives, earliest_activity_at must
    # keep the exact sub-hour timestamp even though its folded bucket floors
    # it (the rollup fallback applies only once raw is pruned).
    rows.append(_log(BASE - timedelta(minutes=23), request_id_suffix="first"))
    rows.append(
        _log(
            BASE + timedelta(days=3, hours=1),
            request_id_suffix="t1",
            status="error",
            error_code="e_rate",
        )
    )
    rows.append(
        _log(
            BASE + timedelta(days=3, hours=2),
            request_id_suffix="t2",
            status="error",
            error_code="e_upstream",
        )
    )
    # Exact-boundary rows: at every candidate watermark and window edge.
    for exact in (
        BASE,
        MID_W - timedelta(seconds=1),
        MID_W,
        TARGET_W - timedelta(seconds=1),
        TARGET_W,
        SINCE_UNALIGNED - timedelta(seconds=1),
        SINCE_UNALIGNED,
        SINCE_UNALIGNED + timedelta(seconds=1),
        UNTIL_UNALIGNED - timedelta(seconds=1),
        UNTIL_UNALIGNED,
        NOW - timedelta(minutes=1),
    ):
        rows.append(_log(exact, request_id_suffix="edge", conversation_id="conv_edge"))
    # Duplicate request_id (#904-style): both rows count everywhere.
    dup_at = BASE + timedelta(days=4, hours=9, minutes=3)
    rows.append(_log(dup_at, request_id="r_dup", input_tokens=5))
    rows.append(_log(dup_at, request_id="r_dup", input_tokens=7))
    return rows


async def _seed_corpus() -> None:
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        for account_id in _ACCOUNTS:
            if account_id is not None:
                await accounts_repo.upsert(_make_account(account_id))
        session.add_all(_corpus())
        await session.commit()


async def _snapshot(*, lead_since: datetime = SINCE_UNALIGNED) -> dict:
    """Every switched read path, over aligned/unaligned/folded-only/tail-only
    windows and hour-multiple plus non-multiple display buckets.

    ``lead_since`` swaps the unaligned window start; the retention test uses
    its ceil-hour to model the documented partial-leading-hour undercount
    once raw below the prune gate is gone."""
    async with SessionLocal() as session:
        logs = RequestLogsRepository(session)
        planner = QuotaPlannerRepository(session)
        api_keys = ApiKeysRepository(session)
        return {
            "buckets_1h": await logs.aggregate_by_bucket(lead_since, 3600),
            "buckets_6h": await logs.aggregate_by_bucket(lead_since, 21600),
            "buckets_1d": await logs.aggregate_by_bucket(SINCE_ALIGNED, 86400),
            "buckets_raw_degrade": await logs.aggregate_by_bucket(lead_since, 5400),
            "activity_since": await logs.aggregate_activity_since(lead_since),
            "activity_between": await logs.aggregate_activity_between(lead_since, UNTIL_UNALIGNED),
            "activity_folded_only": await logs.aggregate_activity_between(*FOLDED_ONLY_WINDOW),
            "activity_tail_only": await logs.aggregate_activity_between(*TAIL_ONLY_WINDOW),
            "top_error_since": await logs.top_error_since(lead_since),
            "top_error_between": await logs.top_error_between(lead_since, UNTIL_UNALIGNED),
            "top_error_tie": await logs.top_error_between(BASE + timedelta(days=3), BASE + timedelta(days=3, hours=3)),
            "top_error_empty": await logs.top_error_between(*EMPTY_ERROR_WINDOW),
            "earliest": await logs.earliest_activity_at(),
            "demand": _project_demand(await planner.aggregate_demand_bins(since=BASE + timedelta(hours=1))),
            "trends_key1": await api_keys.trends_by_key("key_1", lead_since, NOW, 3600),
            "trends_key2": await api_keys.trends_by_key("key_2", SINCE_ALIGNED, UNTIL_UNALIGNED, 7200),
            "trends_raw_degrade": await api_keys.trends_by_key("key_1", lead_since, NOW, 5400),
            "trends_no_logs": await api_keys.trends_by_key("key_none", lead_since, NOW, 3600),
        }


def _project_demand(bins) -> dict:
    """Exact-grain projection: the rollup preserves the legacy demand grain
    (slot, account, api_key, model, reasoning_effort, kind, status) because
    `_bin_demand_units` applies max() per bin before summing — so folded and
    raw bins must agree bin-for-bin, not merely in additive totals."""
    projected: dict[tuple, list[float]] = {}
    for bin_row in bins:
        key = (
            bin_row.slot_epoch,
            bin_row.account_id,
            bin_row.api_key_id,
            bin_row.model,
            bin_row.reasoning_effort,
            bin_row.request_kind,
            bin_row.status,
        )
        entry = projected.setdefault(key, [0, 0, 0, 0, 0.0])
        entry[0] += bin_row.request_count
        entry[1] += bin_row.input_tokens
        entry[2] += bin_row.cached_input_tokens
        entry[3] += bin_row.output_tokens
        entry[4] += bin_row.cost_usd
    return projected


def _assert_snapshots_equal(actual: dict, expected: dict, *, skip_keys: tuple[str, ...] = ()) -> None:
    assert actual.keys() == expected.keys()
    for key, expected_value in expected.items():
        if key in skip_keys:
            continue
        actual_value = actual[key]
        if key.startswith("buckets"):
            _assert_bucket_lists_equal(actual_value, expected_value, key)
        elif key.startswith("activity"):
            assert replace(actual_value, cost_usd=0.0) == replace(expected_value, cost_usd=0.0), key
            assert actual_value.cost_usd == pytest.approx(expected_value.cost_usd, rel=1e-9, abs=1e-12), key
        elif key == "demand":
            assert actual_value.keys() == expected_value.keys(), key
            for demand_key, expected_entry in expected_value.items():
                actual_entry = actual_value[demand_key]
                assert actual_entry[:4] == expected_entry[:4], (key, demand_key)
                assert actual_entry[4] == pytest.approx(expected_entry[4], rel=1e-9, abs=1e-12), (key, demand_key)
        elif key.startswith("trends"):
            assert [(row.bucket_epoch, row.total_tokens) for row in actual_value] == [
                (row.bucket_epoch, row.total_tokens) for row in expected_value
            ], key
            assert [row.total_cost_usd for row in actual_value] == pytest.approx(
                [row.total_cost_usd for row in expected_value], rel=1e-9, abs=1e-9
            ), key
        else:
            assert actual_value == expected_value, key


def _assert_bucket_lists_equal(actual, expected, context: str) -> None:
    def _key(row):
        # NULL and '' tiers are distinct rows in the same (bucket, model);
        # the None-first component keeps their sort order deterministic.
        return (row.bucket_epoch, row.model, row.service_tier is not None, row.service_tier or "")

    actual_sorted = sorted(actual, key=_key)
    expected_sorted = sorted(expected, key=_key)
    assert len(actual_sorted) == len(expected_sorted), context
    for actual_row, expected_row in zip(actual_sorted, expected_sorted, strict=True):
        assert replace(actual_row, cost_usd=0.0) == replace(expected_row, cost_usd=0.0), context
        assert actual_row.cost_usd == pytest.approx(expected_row.cost_usd, rel=1e-9, abs=1e-12), context


async def _watermark() -> datetime | None:
    async with SessionLocal() as session:
        state = (
            await session.execute(select(AccountUsageRollupState).where(AccountUsageRollupState.id == 1))
        ).scalar_one_or_none()
        return None if state is None else state.hourly_folded_through


@pytest.mark.asyncio
async def test_switched_readers_match_legacy_across_watermark_states(db_setup):
    await _seed_corpus()

    # Watermark state 1 — epoch/pre-fold: the switched readers run the exact
    # legacy raw query (empty rollup segment). This is the reference.
    reference = await _snapshot()
    assert reference["top_error_tie"] == "e_rate"  # tie -> count desc, code asc
    assert reference["top_error_empty"] is None
    assert reference["trends_no_logs"] == []

    # Watermark state 2 — mid-history whole hour.
    await run_hourly_fold_pass(now=MID_W + FOLD_LAG)
    assert await _watermark() == MID_W
    _assert_snapshots_equal(await _snapshot(), reference)

    # Watermark state 3 — full target: everything older than FOLD_LAG folded.
    await run_hourly_fold_pass(now=NOW)
    assert await _watermark() == TARGET_W
    _assert_snapshots_equal(await _snapshot(), reference)


@pytest.mark.asyncio
async def test_reader_is_consistent_under_concurrent_fold_commit(db_setup, monkeypatch):
    """A fold slice committing between the reader's rollup+watermark read and
    its raw-tail read must not lose or double-count the just-folded window:
    the tail window derives from the watermark generation the rollup rows
    came from, and folding never deletes raw rows."""
    await _seed_corpus()
    reference = await _snapshot()
    await run_hourly_fold_pass(now=MID_W + FOLD_LAG)

    real_read_hourly_window = request_logs_repository_module.read_hourly_window
    fold_injections = {"count": 0}

    async def _read_then_fold(session, since, until=None, **kwargs):
        result = await real_read_hourly_window(session, since, until, **kwargs)
        if fold_injections["count"] == 0:
            fold_injections["count"] += 1
            await run_hourly_fold_pass(now=NOW)  # advances MID_W -> TARGET_W
        return result

    monkeypatch.setattr(request_logs_repository_module, "read_hourly_window", _read_then_fold)
    async with SessionLocal() as session:
        activity = await RequestLogsRepository(session).aggregate_activity_between(SINCE_UNALIGNED, UNTIL_UNALIGNED)
    assert fold_injections["count"] == 1
    assert await _watermark() == TARGET_W
    expected = reference["activity_between"]
    assert replace(activity, cost_usd=0.0) == replace(expected, cost_usd=0.0)
    assert activity.cost_usd == pytest.approx(expected.cost_usd, rel=1e-9)


@pytest.mark.asyncio
async def test_two_concurrent_fold_passes_serialize_without_double_count(db_setup):
    await _seed_corpus()
    reference = await _snapshot()

    await asyncio.gather(run_hourly_fold_pass(now=NOW), run_hourly_fold_pass(now=NOW))
    assert await _watermark() == TARGET_W
    assert await run_hourly_fold_pass(now=NOW) == 0  # fixed point
    _assert_snapshots_equal(await _snapshot(), reference)


@pytest.mark.asyncio
async def test_escape_hatch_reset_degrades_to_legacy_then_rebackfills(db_setup):
    """Operator escape hatch: delete ALL rollup rows + reset the hourly
    watermark to epoch in ONE transaction -> reads are immediately
    legacy-equivalent; the next fold pass re-backfills and converges."""
    await _seed_corpus()
    reference = await _snapshot()
    await run_hourly_fold_pass(now=NOW)
    _assert_snapshots_equal(await _snapshot(), reference)

    async with SessionLocal() as session:
        await session.execute(delete(RequestUsageHourlyRollup))
        await session.execute(delete(RequestUsageHourlyErrorRollup))
        await session.execute(delete(RequestDemandQuarterRollup))
        await session.execute(
            update(AccountUsageRollupState).where(AccountUsageRollupState.id == 1).values(hourly_folded_through=_EPOCH)
        )
        await session.commit()

    assert await _watermark() == _EPOCH
    _assert_snapshots_equal(await _snapshot(), reference)  # pure-raw degrade

    await run_hourly_fold_pass(now=NOW)
    assert await _watermark() == TARGET_W
    _assert_snapshots_equal(await _snapshot(), reference)  # re-backfill converged


@pytest.mark.asyncio
async def test_statistics_survive_retention_pruning_folded_raw(db_setup):
    """The headline guarantee: after raw rows below the retention gate
    (watermark - FOLD_LAG) are physically deleted, every rollup-served
    statistic is unchanged. Distinct-conversation metrics shrink to the
    surviving raw rows (documented non-goal), and earliest_activity_at falls
    back to the earliest folded bucket at whole-hour precision."""
    await _seed_corpus()
    reference = await _snapshot()
    await run_hourly_fold_pass(now=NOW)
    # The reference for unaligned window starts once their partial leading
    # hour is un-servable: identical to starting at the ceil hour (that
    # partial slice is ALWAYS raw-served, by design). Captured pre-prune;
    # its equivalence to the legacy reader is covered by the parity test.
    lead_ceil = floor_to_hour(SINCE_UNALIGNED) + timedelta(hours=1)
    leadless = await _snapshot(lead_since=lead_ceil)

    prune_cutoff = TARGET_W - FOLD_LAG
    async with SessionLocal() as session:
        await session.execute(delete(RequestLog).where(RequestLog.requested_at < prune_cutoff))
        await session.commit()

    pruned = await _snapshot()
    expected = dict(reference)
    for key in ("buckets_1h", "buckets_6h", "top_error_since", "top_error_between", "trends_key1"):
        expected[key] = leadless[key]
    _assert_snapshots_equal(
        pruned,
        expected,
        skip_keys=(
            # Raw-bound by design after pruning:
            "activity_since",  # conversation fields shrink (asserted below)
            "activity_between",
            "activity_folded_only",
            "earliest",  # hour-precision fallback (asserted below)
            # Non-hour-multiple display buckets are the documented full-raw
            # degrade path: after pruning they only see the surviving tail.
            "buckets_raw_degrade",
            "trends_raw_degrade",
        ),
    )
    # Additive activity totals are unchanged (modulo the pruned partial
    # leading hour for unaligned starts); only the distinct-conversation
    # metrics dropped to what raw still holds.
    for key, baseline in (
        ("activity_since", leadless),
        ("activity_between", leadless),
        ("activity_folded_only", reference),
    ):
        actual, wanted = pruned[key], baseline[key]
        assert replace(actual, cost_usd=0.0, conversation_count=0, conversation_request_count=0) == replace(
            wanted, cost_usd=0.0, conversation_count=0, conversation_request_count=0
        ), key
        assert actual.cost_usd == pytest.approx(wanted.cost_usd, rel=1e-9), key
        assert actual.conversation_count <= wanted.conversation_count, key
        assert actual.conversation_request_count <= wanted.conversation_request_count, key
    assert pruned["activity_folded_only"].conversation_count == 0  # fully pruned window
    # earliest_activity_at: raw min is gone; the rollup fallback reports the
    # first countable bucket at hour precision.
    assert pruned["earliest"] == floor_to_hour(reference["earliest"])
    # Full-raw degrade paths only cover surviving rows.
    prune_cutoff_epoch = int((prune_cutoff - _EPOCH).total_seconds())
    assert all(row.bucket_epoch >= prune_cutoff_epoch // 5400 * 5400 for row in pruned["buckets_raw_degrade"])
    assert all(row.bucket_epoch >= prune_cutoff_epoch // 5400 * 5400 for row in pruned["trends_raw_degrade"])
    assert pruned["buckets_raw_degrade"]  # the tail is still served


@pytest.mark.asyncio
async def test_dashboard_overview_json_is_identical_before_and_after_fold(async_client, db_setup, monkeypatch):
    """API-level equivalence: the exact overview JSON must not change when
    the same window flips from raw-scanned to rollup-served."""
    fixed_now = utcnow().replace(microsecond=0)
    monkeypatch.setattr("app.modules.dashboard.service.utcnow", lambda: fixed_now)

    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(_make_account("acc_par_a"))
        session.add_all(
            [
                _log(
                    fixed_now - timedelta(days=2, hours=hour, minutes=13),
                    request_id_suffix=f"api{hour}",
                    api_key_id=None,
                    service_tier="flex" if hour % 2 else None,
                    status="error" if hour % 3 == 0 else "success",
                    error_code="e_api" if hour % 3 == 0 else None,
                    conversation_id="conv_api",
                )
                for hour in range(12)
            ]
        )
        await session.commit()

    before = await async_client.get("/api/dashboard/overview?timeframe=7d")
    assert before.status_code == 200

    folded_slices = await run_hourly_fold_pass()  # real now: rows are > FOLD_LAG old
    assert folded_slices > 0
    async with SessionLocal() as session:
        folded = (await session.execute(select(RequestUsageHourlyRollup))).scalars().all()
    assert folded  # the window flipped to rollup-served

    after = await async_client.get("/api/dashboard/overview?timeframe=7d")
    assert after.status_code == 200
    assert after.json() == before.json()

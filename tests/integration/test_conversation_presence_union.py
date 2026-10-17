"""Focused tests for the conversation-presence UNION primitives.

The parity harness (``test_request_usage_rollup_parity.py``) pins the switched
readers against the legacy readers over the full corpus. This module pins the
union itself:

1. its aggregates equal a raw-only oracle for every watermark position
   relative to the read window — state row missing, epoch, before, inside,
   after — for aligned, unaligned and open-ended windows, with and without
   display buckets and soft-deleted rows;
2. the statement reads the watermark as scalar subqueries of the state row
   (never a joined column behind an ``IS NULL OR`` per-row filter);
3. on PostgreSQL the ``request_logs`` branch of the union is served by a
   bounded index range on ``requested_at``: the scan visits exactly the
   sub-hour edges and the un-folded tail for every watermark layout (below,
   at the window start, inside, at the window end, above).

The reports' conversation counts are served by the permanent report
aggregates (``app/modules/reports/rollup_read.py``) and are pinned by the
reports tests, not here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql import ClauseElement, Executable

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, AccountUsageRollupState, RequestConversationHourlyRollup, RequestLog
from app.db.session import SessionLocal, engine
from app.modules.accounts.repository import AccountsRepository
from app.modules.accounts.usage_rollup import _state_bootstrap_stmt
from app.modules.accounts.usage_time_rollup import (
    FOLD_LAG,
    HOURLY_BUCKET_SECONDS,
    WARMUP_REQUEST_KINDS,
    _requested_at_epoch_bucket_expr,
    conversation_id_expr,
    epoch_seconds,
    run_conversation_fold_pass,
)
from app.modules.accounts.usage_time_rollup_read import (
    ceil_to_grid,
    conversation_presence_union,
    floor_to_grid,
)
from app.modules.request_logs.repository import RequestLogsRepository

pytestmark = pytest.mark.integration

BASE = datetime(2025, 7, 1)
HOUR = timedelta(hours=1)

# Read windows: unaligned edges, hour-aligned edges, and an open-ended tail.
WINDOWS: tuple[tuple[datetime, datetime | None], ...] = (
    (BASE + timedelta(hours=10, minutes=30), BASE + timedelta(hours=50, minutes=45)),
    (BASE + timedelta(hours=12), BASE + timedelta(hours=48)),
    (BASE + timedelta(hours=10, minutes=30), None),
)
# Conversation watermark positions relative to the windows above (all whole
# hours); the fold only ever advances, so the oracle tests visit them in order.
W_BEFORE = BASE + timedelta(hours=6)
W_INSIDE = BASE + timedelta(hours=30)
W_AFTER = BASE + timedelta(hours=72)

_CONVERSATIONS = (None, "", "   ", "c_short", "c_long_a", "c_long_b")


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


def _log(requested_at: datetime, index: int, **overrides: Any) -> RequestLog:
    values: dict[str, Any] = {
        "account_id": "acc_conv",
        "api_key_id": None,
        "request_id": f"r_{index}",
        "model": "gpt-5.1-codex",
        "request_kind": "normal",
        "status": "success",
        "input_tokens": 10,
        "output_tokens": 5,
        "cached_input_tokens": 0,
        "cost_usd": 0.001,
        "conversation_id": None,
        "requested_at": requested_at,
        "deleted_at": None,
    }
    values.update(overrides)
    return RequestLog(**values)


def _corpus() -> list[RequestLog]:
    """72 hours of rows every 20 minutes: NULL/blank ids, two conversations
    that live for the whole corpus (so they straddle every fold boundary and
    display bucket), one short conversation, warmup rows and soft-deleted
    rows for every conversation."""
    rows: list[RequestLog] = []
    for index in range(72 * 3):
        at = BASE + timedelta(minutes=20 * index)
        rows.append(_log(at, index, conversation_id=_CONVERSATIONS[index % len(_CONVERSATIONS)]))
    extra = len(rows)
    for offset, conversation_id in enumerate(("c_long_a", "c_long_b", "c_short", "c_deleted_only")):
        at = BASE + timedelta(hours=7 + offset * 11, minutes=5)
        rows.append(_log(at, extra + offset, conversation_id=conversation_id, deleted_at=at + HOUR))
    rows.append(
        _log(BASE + timedelta(hours=20, minutes=1), extra + 10, conversation_id="c_warm", request_kind="warmup")
    )
    rows.append(
        _log(BASE + timedelta(hours=41, minutes=1), extra + 11, conversation_id="c_warm", request_kind="limit_warmup")
    )
    return rows


async def _seed_corpus() -> None:
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(_make_account("acc_conv"))
        session.add_all(_corpus())
        await session.commit()


def _dialect_name(session: AsyncSession) -> str:
    bind = session.get_bind()
    return bind.dialect.name if bind is not None else "sqlite"


def _raw_conditions(*, include_deleted: bool):
    conditions = [RequestLog.request_kind.not_in(WARMUP_REQUEST_KINDS)]
    if not include_deleted:
        conditions.append(RequestLog.deleted_at.is_(None))
    return conditions


def _folded_bucket_range(start: datetime, end: datetime | None, watermark: datetime | None) -> tuple[int, int]:
    """Epoch bounds ``[fold_lo, folded_until)`` of the hour buckets the
    folded branch serves for a window (empty when the watermark is missing
    or below the window). Stated in terms of hour buckets — independent of
    how the SQL phrases the raw complement."""
    fold_lo = epoch_seconds(ceil_to_grid(start, HOURLY_BUCKET_SECONDS))
    if watermark is None:
        return fold_lo, fold_lo
    folded_until = epoch_seconds(watermark)
    if end is not None:
        folded_until = min(folded_until, epoch_seconds(floor_to_grid(end, HOURLY_BUCKET_SECONDS)))
    return fold_lo, folded_until


def _raw_complement_count(
    requested_ats: Iterable[datetime], start: datetime, end: datetime | None, watermark: datetime | None
) -> int:
    """Row-level oracle: a raw row belongs to the complement iff it is inside
    the window and its hour bucket is NOT one the folded branch serves."""
    fold_lo, folded_until = _folded_bucket_range(start, end, watermark)
    count = 0
    for requested_at in requested_ats:
        if requested_at < start or (end is not None and requested_at >= end):
            continue
        hour_epoch = epoch_seconds(floor_to_grid(requested_at, HOURLY_BUCKET_SECONDS))
        if fold_lo <= hour_epoch < folded_until:
            continue
        count += 1
    return count


async def _union_aggregates(
    session: AsyncSession,
    since: datetime,
    until: datetime | None,
    *,
    include_deleted: bool,
    display_bucket_seconds: int | None,
) -> Any:
    union = conversation_presence_union(
        session,
        since,
        until,
        include_deleted=include_deleted,
        raw_conditions=_raw_conditions(include_deleted=include_deleted),
        display_bucket_seconds=display_bucket_seconds,
    ).subquery()
    if display_bucket_seconds is None:
        stmt = select(func.count(func.distinct(union.c.cid)), func.coalesce(func.sum(union.c.request_count), 0))
        row = (await session.execute(stmt)).one()
        return int(row[0]), int(row[1])
    stmt = (
        select(union.c.bucket_epoch, func.count(func.distinct(union.c.cid)))
        .group_by(union.c.bucket_epoch)
        .order_by(union.c.bucket_epoch)
    )
    return [(int(bucket), int(count)) for bucket, count in (await session.execute(stmt)).all()]


async def _legacy_aggregates(
    session: AsyncSession,
    since: datetime,
    until: datetime | None,
    *,
    include_deleted: bool,
    display_bucket_seconds: int | None,
) -> Any:
    """The pre-rollup raw-only aggregation the union must reproduce."""
    cid = conversation_id_expr()
    conditions = [RequestLog.requested_at >= since, cid.is_not(None), *_raw_conditions(include_deleted=include_deleted)]
    if until is not None:
        conditions.append(RequestLog.requested_at < until)
    if display_bucket_seconds is None:
        row = (await session.execute(select(func.count(func.distinct(cid)), func.count()).where(*conditions))).one()
        return int(row[0]), int(row[1])
    bucket = _requested_at_epoch_bucket_expr(session, display_bucket_seconds).label("bucket_epoch")
    stmt = select(bucket, func.count(func.distinct(cid))).where(*conditions).group_by(bucket).order_by(bucket)
    return [(int(bucket_epoch), int(count)) for bucket_epoch, count in (await session.execute(stmt)).all()]


async def _assert_union_matches_legacy(label: str) -> None:
    async with SessionLocal() as session:
        for since, until in WINDOWS:
            for include_deleted in (False, True):
                for display_bucket_seconds in (None, 3600, 21600):
                    kwargs = {"include_deleted": include_deleted, "display_bucket_seconds": display_bucket_seconds}
                    actual = await _union_aggregates(session, since, until, **kwargs)
                    expected = await _legacy_aggregates(session, since, until, **kwargs)
                    assert actual == expected, (label, since, until, kwargs)
                    if display_bucket_seconds is None:
                        assert actual[0] > 0, (label, since, until, kwargs)  # the corpus is never empty


async def _conversation_watermark() -> datetime | None:
    async with SessionLocal() as session:
        state = (
            await session.execute(select(AccountUsageRollupState).where(AccountUsageRollupState.id == 1))
        ).scalar_one_or_none()
        return None if state is None else state.conversation_folded_through


async def _folded_row_count() -> int:
    async with SessionLocal() as session:
        return int(
            (await session.execute(select(func.count()).select_from(RequestConversationHourlyRollup))).scalar_one()
        )


async def _fold_through(watermark: datetime) -> None:
    await run_conversation_fold_pass(now=watermark + FOLD_LAG)
    assert await _conversation_watermark() == watermark


@pytest.mark.asyncio
async def test_union_matches_legacy_for_every_watermark_position(db_setup):
    await _seed_corpus()

    # State row missing (fresh schema): the folded branch is empty and the
    # raw branch covers the whole window.
    assert await _conversation_watermark() is None
    await _assert_union_matches_legacy("state row missing")

    # State row present with the epoch watermark: same degradation.
    async with SessionLocal() as session:
        await session.execute(_state_bootstrap_stmt(session))
        await session.commit()
    assert await _conversation_watermark() == datetime(1970, 1, 1)
    await _assert_union_matches_legacy("epoch watermark")

    # Watermark below every window: folded rows exist but none are in the
    # windows' range.
    await _fold_through(W_BEFORE)
    assert await _folded_row_count() > 0
    await _assert_union_matches_legacy("watermark before window")

    # Watermark inside the windows: folded head + raw tail, conversations
    # straddling the boundary are counted once.
    await _fold_through(W_INSIDE)
    await _assert_union_matches_legacy("watermark inside window")

    # Watermark past every bounded window: only the sub-hour edges (and the
    # open window's tail) come from raw.
    await _fold_through(W_AFTER)
    await _assert_union_matches_legacy("watermark after window")


@pytest.mark.asyncio
async def test_conversation_statements_read_watermark_as_scalar_subquery(db_setup):
    await _seed_corpus()
    await _fold_through(W_INSIDE)
    since, until = WINDOWS[0]
    assert until is not None

    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", _capture)
    try:
        async with SessionLocal() as session:
            logs = RequestLogsRepository(session)
            await logs.aggregate_activity_between(since, until)
            await logs.aggregate_conversations_by_bucket(since, 21600)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _capture)

    # The reports repository reads its conversation counts from the permanent
    # report aggregates, so the only switched conversation reads are the two
    # dashboard ones: activity metrics and hour-multiple trend buckets.
    union_statements = [stmt for stmt in statements if "request_conversation_hourly_rollups" in stmt]
    assert len(union_statements) == 2, "expected one merged statement per switched conversation read"
    for stmt in union_statements:
        assert "request_logs" in stmt  # both branches live in ONE statement
        assert "JOIN account_usage_rollup_state" not in stmt
        assert "conversation_folded_through IS NULL" not in stmt
        # The watermark is read exactly twice, once per UNION branch, as an
        # uncorrelated scalar subquery of the state row.
        state_row_reads = re.findall(r"FROM account_usage_rollup_state\s+WHERE account_usage_rollup_state\.id", stmt)
        assert len(state_row_reads) == 2, stmt


# --- PostgreSQL plan shape ---------------------------------------------------

_PLAN_ROWS = 7 * 24 * 24  # one row every 2.5 minutes for seven days
_PLAN_STEP = timedelta(days=7) / _PLAN_ROWS
_PLAN_REQUESTED_ATS = [BASE + _PLAN_STEP * index for index in range(_PLAN_ROWS)]
_PLAN_END = BASE + timedelta(days=7)
_PLAN_W_LOW = BASE + timedelta(days=1)  # below / at the start of the layouts probed first
_PLAN_W = BASE + timedelta(days=6, hours=21)  # three un-folded hours before the corpus end


class _Explain(Executable, ClauseElement):
    """``EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) <statement>`` executed with
    the statement's own bind parameters and bind casts — the prepared form
    the application sends, not a ``literal_binds`` rendering."""

    inherit_cache = False

    def __init__(self, statement: Any) -> None:
        self.statement = statement


@compiles(_Explain, "postgresql")
def _compile_explain(element: _Explain, compiler: Any, **kw: Any) -> str:
    return "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + compiler.process(element.statement, **kw)


async def _seed_plan_corpus() -> None:
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(_make_account("acc_conv"))
        session.add_all(
            _log(at, index, conversation_id=f"c_{index // 3}") for index, at in enumerate(_PLAN_REQUESTED_ATS)
        )
        await session.commit()


async def _analyze() -> None:
    autocommit_engine = engine.execution_options(isolation_level="AUTOCOMMIT")
    async with autocommit_engine.connect() as conn:
        await conn.execute(text("VACUUM (ANALYZE) request_logs"))
        await conn.execute(text("VACUUM (ANALYZE) request_conversation_hourly_rollups"))


def _request_logs_scan_nodes(node: dict[str, Any]) -> list[dict[str, Any]]:
    found = [node] if node.get("Relation Name") == "request_logs" else []
    for child in node.get("Plans", ()):
        found.extend(_request_logs_scan_nodes(child))
    return found


def _index_conds(node: dict[str, Any]) -> list[str]:
    conds = [node["Index Cond"]] if "Index Cond" in node else []
    for child in node.get("Plans", ()):
        conds.extend(_index_conds(child))
    return conds


async def _explain(session: AsyncSession, statement: Any) -> dict[str, Any]:
    plan = (await session.execute(_Explain(statement))).scalar_one()
    return json.loads(plan)[0]["Plan"] if isinstance(plan, str) else plan[0]["Plan"]


def _assert_raw_branch_bounded(plan: dict[str, Any], expected_raw_rows: int, *, context: Any) -> None:
    """Every request_logs node is an index-driven scan with an Index Cond on
    requested_at, and the rows it produced equal the exact raw complement
    (nothing filtered away per row after the index range). The statement has
    constant bounds and no join, so every scan runs exactly once and
    ``Actual Rows`` is exact."""
    scans = _request_logs_scan_nodes(plan)
    assert scans, json.dumps(plan)
    produced = 0
    for scan in scans:
        assert scan["Node Type"] in {"Index Scan", "Index Only Scan", "Bitmap Heap Scan"}, (context, scan["Node Type"])
        assert any("requested_at" in cond for cond in _index_conds(scan)), (context, scan)
        assert scan.get("Rows Removed by Filter", 0) == 0, (context, scan)
        assert scan.get("Rows Removed by Index Recheck", 0) == 0, (context, scan)
        assert int(scan["Actual Loops"]) == 1, (context, scan)
        produced += int(scan["Actual Rows"])
    assert produced == expected_raw_rows, (context, produced, expected_raw_rows, json.dumps(scans))


def _union_stmt(session: AsyncSession, since: datetime, until: datetime) -> Any:
    return conversation_presence_union(
        session, since, until, include_deleted=False, raw_conditions=_raw_conditions(include_deleted=False)
    )


@pytest.mark.asyncio
async def test_conversation_union_raw_branch_is_index_bounded_postgresql(db_setup):
    """The raw branch must be index ranges on requested_at — the sub-hour
    leading edge and the un-folded tail — not a window-wide scan filtered
    per row by the watermark, for every watermark layout relative to the
    window (both the ``greatest(lo, ...)`` clamp and the ``least(W, hi)``
    boundary are exercised with exact row counts)."""
    async with SessionLocal() as session:
        if _dialect_name(session) != "postgresql":
            pytest.skip("PostgreSQL-only query plan test")
    await _seed_plan_corpus()
    await _analyze()

    half_hour = timedelta(minutes=30)
    # (since, until) layouts; the expected raw-row count is computed by the
    # bucket-level oracle for the watermark in force at that step.
    layouts_low: list[tuple[datetime, datetime]] = [
        (_PLAN_W_LOW + timedelta(days=1) + half_hour, _PLAN_END),  # W below the window
        (_PLAN_W_LOW - half_hour, _PLAN_W_LOW + timedelta(days=2)),  # W == lo: the greatest() clamp
    ]
    layouts_high: list[tuple[datetime, datetime]] = [
        (BASE + half_hour, _PLAN_END),  # W inside: leading edge + 3 h tail
        (BASE + timedelta(days=1), _PLAN_W),  # until == W: least() boundary, empty tail
        (BASE + timedelta(days=1) + half_hour, _PLAN_W + half_hour),  # tail clipped to a half hour
        (BASE + timedelta(days=1), BASE + timedelta(days=2)),  # fully folded, hour-aligned: no raw rows
    ]

    async def _probe(layouts: Sequence[tuple[datetime, datetime]], watermark: datetime | None) -> None:
        async with SessionLocal() as session:
            # The InitPlan bound has no statistics, so the planner's cost pick
            # is not under test — the index path it must be able to use is.
            # SET LOCAL is scoped to this session's transaction (rolled back
            # on close), so the GUC cannot leak to other tests.
            await session.execute(text("SET LOCAL enable_seqscan = off"))
            for since, until in layouts:
                expected = _raw_complement_count(_PLAN_REQUESTED_ATS, since, until, watermark)
                plan = await _explain(session, _union_stmt(session, since, until))
                _assert_raw_branch_bounded(plan, expected, context=(watermark, since, until))
                if watermark is not None and until <= watermark and since == ceil_to_grid(since, HOURLY_BUCKET_SECONDS):
                    assert expected == 0  # the fully folded aligned layout really is empty

    # State row missing: the whole window is the raw complement.
    await _probe(layouts_low[:1], None)
    await _fold_through(_PLAN_W_LOW)
    await _analyze()
    await _probe(layouts_low, _PLAN_W_LOW)
    await _fold_through(_PLAN_W)
    await _analyze()
    await _probe(layouts_high, _PLAN_W)

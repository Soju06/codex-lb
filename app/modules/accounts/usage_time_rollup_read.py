"""Read-side partition/merge primitives for the time-axis usage rollups.

The switched read paths (dashboard buckets/activity/top-error, quota-planner
demand bins, api-key trends) serve folded history from the rollup tables and
only the un-folded tail from raw ``request_logs``. This module owns the ONE
partitioning rule they all share, so the boundary arithmetic cannot drift
apart across consumers:

- The rollup segment covers whole grid buckets inside ``[since, until)`` that
  lie below the hourly watermark: ``[ceil_grid(since), min(W, floor_grid(
  until)))``. The watermark is always hour-aligned (and 3600 % 900 == 0), so
  a folded bucket is never split between the rollup segment and the raw tail.
- The raw windows are the exact complement: a partial leading bucket
  ``[since, ceil_grid(since))`` and the tail ``[hi, until)`` (which for a
  fully historical window is a partial trailing bucket). Both are always
  served from raw — if retention has already pruned them, the loss is a
  deterministic ≤1-bucket undercount per unaligned edge, documented as out
  of parity scope (sub-bucket boundary contributions cannot be represented
  in the rollup grid; serving the whole edge bucket instead would
  over-count).
- Rollup rows and the watermark are read in ONE statement (state LEFT JOIN
  rollup, inherited from ``RequestUsageTimeRollupRepository``): a fold slice
  committing between the rollup read and the raw-tail read can never drop or
  double-count the just-folded window, because raw rows are never deleted by
  the fold and the tail window is derived from the watermark generation the
  rollup rows came from.
- With no state row or an epoch watermark (pre-backfill, or after the
  operator escape hatch reset) the rollup segment is empty and the raw
  windows collapse to the full ``[since, until)`` — the readers degrade to
  the exact legacy behaviour with no kill switch.

The conversation-presence primitives at the bottom follow the same
partitioning rule but resolve it INSIDE one statement (the watermark joined
into both UNION branches) instead of returning raw windows: distinct
conversation counts must merge folded ids and raw-tail ids in a single
snapshot, and the raw complement can be phrased watermark-relative as
``requested_at < ceil_grid(since) OR requested_at >= least(W,
floor_grid(until))`` — an exact complement of the folded buckets for any
watermark position, degrading to the full window when the watermark is at
the epoch (or the state row is missing, via the raw branch's OUTER join).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import BigInteger, ColumnElement, Integer, Select, and_, cast, func, literal, or_, select, union_all
from sqlalchemy import cast as sa_cast
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import CompoundSelect

from app.db.models import (
    AccountUsageRollupState,
    RequestConversationHourlyRollup,
    RequestDemandQuarterRollup,
    RequestLog,
    RequestUsageHourlyRollup,
)
from app.modules.accounts.usage_time_rollup import (
    _STATE_ROW_ID,
    HOURLY_BUCKET_SECONDS,
    QUARTER_SLOT_SECONDS,
    WARMUP_REQUEST_KINDS,
    HourlyErrorRollupRow,
    HourlyUsageRollupRow,
    QuarterDemandRollupRow,
    RequestUsageTimeRollupRepository,
    _requested_at_epoch_bucket_expr,
    conversation_id_expr,
    epoch_seconds,
)

_EPOCH = datetime(1970, 1, 1)

# A raw request_logs window: half-open [start, end), end=None meaning +inf.
RawWindow = tuple[datetime, datetime | None]


def epoch_to_datetime(epoch: int) -> datetime:
    """Unix epoch seconds to the naive-UTC datetime domain of requested_at."""
    return _EPOCH + timedelta(seconds=epoch)


def floor_to_grid(value: datetime, grid_seconds: int) -> datetime:
    return epoch_to_datetime(epoch_seconds(value) // grid_seconds * grid_seconds)


def ceil_to_grid(value: datetime, grid_seconds: int) -> datetime:
    floored = floor_to_grid(value, grid_seconds)
    return floored if floored == value else floored + timedelta(seconds=grid_seconds)


def _partition_raw_windows(
    since: datetime,
    until: datetime | None,
    watermark: datetime | None,
    grid_seconds: int,
) -> tuple[int | None, list[RawWindow]]:
    """Split ``[since, until)`` into the folded bound and raw complement.

    Returns ``(folded_until_epoch, raw_windows)``: rollup rows with
    ``epoch < folded_until_epoch`` are authoritative (``None`` = use no
    rollup rows), everything else must come from raw.
    """
    if watermark is None:
        return None, [(since, until)]
    lo = ceil_to_grid(since, grid_seconds)
    hi = watermark if until is None else min(watermark, floor_to_grid(until, grid_seconds))
    if hi <= lo:
        return None, [(since, until)]
    raw_windows: list[RawWindow] = []
    if since < lo:
        raw_windows.append((since, lo))
    if until is None or hi < until:
        raw_windows.append((hi, until))
    return epoch_seconds(hi), raw_windows


async def _read_window(reader, epoch_attr: str, since: datetime, until: datetime | None, grid_seconds: int, filters):
    lo_epoch = epoch_seconds(ceil_to_grid(since, grid_seconds))
    until_epoch = None if until is None else epoch_seconds(floor_to_grid(until, grid_seconds))
    rows, watermark = await reader(since_epoch=lo_epoch, until_epoch=until_epoch, filters=filters)
    folded_until_epoch, raw_windows = _partition_raw_windows(since, until, watermark, grid_seconds)
    if folded_until_epoch is None:
        return [], raw_windows
    return [row for row in rows if getattr(row, epoch_attr) < folded_until_epoch], raw_windows


async def read_hourly_window(
    session: AsyncSession,
    since: datetime,
    until: datetime | None = None,
    *,
    filters: Sequence[ColumnElement[bool]] = (),
) -> tuple[list[HourlyUsageRollupRow], list[RawWindow]]:
    repo = RequestUsageTimeRollupRepository(session)
    return await _read_window(repo.read_hourly, "bucket_epoch", since, until, HOURLY_BUCKET_SECONDS, filters)


async def read_errors_window(
    session: AsyncSession,
    since: datetime,
    until: datetime | None = None,
    *,
    filters: Sequence[ColumnElement[bool]] = (),
) -> tuple[list[HourlyErrorRollupRow], list[RawWindow]]:
    repo = RequestUsageTimeRollupRepository(session)
    return await _read_window(repo.read_errors, "bucket_epoch", since, until, HOURLY_BUCKET_SECONDS, filters)


async def read_demand_window(
    session: AsyncSession,
    since: datetime,
    until: datetime | None = None,
    *,
    filters: Sequence[ColumnElement[bool]] = (),
) -> tuple[list[QuarterDemandRollupRow], list[RawWindow]]:
    repo = RequestUsageTimeRollupRepository(session)
    return await _read_window(repo.read_demand, "slot_epoch", since, until, QUARTER_SLOT_SECONDS, filters)


async def sum_demand_window(
    session: AsyncSession,
    since: datetime,
    until: datetime | None = None,
    *,
    filters: Sequence[ColumnElement[bool]] = (),
) -> tuple[int, list[RawWindow]]:
    """Watermark-consistent SUM(request_count) over the demand rollup.

    Same partitioning rule as the row readers, aggregated in SQL so counting
    a long window does not materialize every demand-grain row. The watermark
    and the folded sum come from ONE statement (state LEFT JOIN demand, with
    the slot upper bound expressed against the state row's own watermark
    epoch), so an escape-hatch reset committing concurrently can never pair
    an old watermark with already-truncated rollups. With no watermark the
    sum is 0 and the raw windows cover the full range — the caller degrades
    to the exact legacy raw read.
    """
    lo_epoch = epoch_seconds(ceil_to_grid(since, QUARTER_SLOT_SECONDS))
    if session.get_bind().dialect.name == "postgresql":
        watermark_epoch = sa_cast(func.extract("epoch", AccountUsageRollupState.hourly_folded_through), BigInteger)
    else:
        watermark_epoch = sa_cast(func.strftime("%s", AccountUsageRollupState.hourly_folded_through), Integer)
    join_conditions = [
        *filters,
        RequestDemandQuarterRollup.slot_epoch >= lo_epoch,
        RequestDemandQuarterRollup.slot_epoch < watermark_epoch,
    ]
    if until is not None:
        join_conditions.append(
            RequestDemandQuarterRollup.slot_epoch < epoch_seconds(floor_to_grid(until, QUARTER_SLOT_SECONDS))
        )
    stmt = (
        select(
            AccountUsageRollupState.hourly_folded_through,
            func.coalesce(func.sum(RequestDemandQuarterRollup.request_count), 0),
        )
        .select_from(AccountUsageRollupState)
        .outerjoin(RequestDemandQuarterRollup, and_(*join_conditions))
        .where(AccountUsageRollupState.id == _STATE_ROW_ID)
        .group_by(AccountUsageRollupState.hourly_folded_through)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return 0, [(since, until)]
    watermark, folded_total = row[0], int(row[1])
    folded_until_epoch, raw_windows = _partition_raw_windows(since, until, watermark, QUARTER_SLOT_SECONDS)
    if folded_until_epoch is None:
        return 0, raw_windows
    return folded_total, raw_windows


def raw_windows_clause(windows: Sequence[RawWindow]) -> ColumnElement[bool]:
    """OR of half-open requested_at windows; callers skip raw entirely when
    ``windows`` is empty instead of emitting a degenerate clause."""
    clauses: list[ColumnElement[bool]] = []
    for start, end in windows:
        if end is None:
            clauses.append(RequestLog.requested_at >= start)
        else:
            clauses.append(and_(RequestLog.requested_at >= start, RequestLog.requested_at < end))
    if not clauses:
        raise ValueError("raw_windows_clause requires at least one window")
    return or_(*clauses)


# --- Conversation presence primitives -------------------------------------
#
# Distinct conversation counts are not additive across the fold boundary, so
# the conversation readers merge the folded ids and the raw-tail ids inside
# ONE statement: the state row is joined into both UNION branches, giving the
# whole read a single snapshot (a concurrent fold slice or an operator
# escape-hatch reset can never split the watermark generation between the
# folded segment and the raw complement). `request_count` rides along for the
# additive conversation-request totals (each raw row contributes 1).


def _conversation_watermark_epoch_expr(session: AsyncSession) -> ColumnElement:
    """Epoch seconds of the conversation watermark (whole hours, so the
    conversion is exact), dialect-split like the bucket expressions."""
    column = AccountUsageRollupState.conversation_folded_through
    if session.get_bind().dialect.name == "postgresql":
        return cast(func.floor(func.extract("epoch", column)), BigInteger)
    return cast(func.strftime("%s", column), Integer)


def _least_fn(session: AsyncSession):
    # SQLite's two-argument min() scalar function is its least().
    return func.least if session.get_bind().dialect.name == "postgresql" else func.min


def _conversation_folded_select(
    session: AsyncSession,
    since: datetime,
    until: datetime | None,
    *,
    include_deleted: bool,
    display_bucket_seconds: int | None,
) -> Select:
    rollup = RequestConversationHourlyRollup
    conditions: list[ColumnElement[bool]] = [
        rollup.bucket_epoch >= epoch_seconds(ceil_to_grid(since, HOURLY_BUCKET_SECONDS)),
        rollup.bucket_epoch < _conversation_watermark_epoch_expr(session),
    ]
    if until is not None:
        conditions.append(rollup.bucket_epoch < epoch_seconds(floor_to_grid(until, HOURLY_BUCKET_SECONDS)))
    if not include_deleted:
        conditions.append(rollup.is_deleted.is_(False))
    columns: list = [rollup.conversation_id.label("cid"), rollup.request_count.label("request_count")]
    if display_bucket_seconds is not None:
        # Dialect-split integer flooring: SQLAlchemy renders `/` as true
        # division (NUMERIC on PostgreSQL, where a bigint cast then ROUNDS),
        # so mirror the raw readers' bucket arithmetic instead.
        if session.get_bind().dialect.name == "postgresql":
            display = cast(
                func.floor(rollup.bucket_epoch / display_bucket_seconds) * display_bucket_seconds, BigInteger
            )
        else:
            display = cast(rollup.bucket_epoch / display_bucket_seconds, Integer) * display_bucket_seconds
        columns.insert(0, display.label("bucket_epoch"))
    return (
        select(*columns)
        .select_from(AccountUsageRollupState)
        .join(rollup, and_(*conditions))
        .where(AccountUsageRollupState.id == _STATE_ROW_ID)
    )


def _conversation_raw_complement_clause(
    session: AsyncSession, lo: datetime, hi: datetime | None
) -> ColumnElement[bool]:
    """Rows NOT covered by the folded segment ``[lo, min(W, hi))``: below the
    ceil-grid start, or at/above the watermark-clamped end. A NULL watermark
    (state row missing — the callers OUTER-join it) or an epoch watermark
    degrades to the caller's full window."""
    watermark = AccountUsageRollupState.conversation_folded_through
    tail_start = watermark if hi is None else _least_fn(session)(watermark, hi)
    return or_(watermark.is_(None), RequestLog.requested_at < lo, RequestLog.requested_at >= tail_start)


def _conversation_raw_select(
    session: AsyncSession,
    since: datetime,
    until: datetime | None,
    *,
    raw_conditions: Sequence[ColumnElement[bool]],
    display_bucket_seconds: int | None,
) -> Select:
    lo = ceil_to_grid(since, HOURLY_BUCKET_SECONDS)
    hi = None if until is None else floor_to_grid(until, HOURLY_BUCKET_SECONDS)
    conditions: list[ColumnElement[bool]] = [
        RequestLog.requested_at >= since,
        _conversation_raw_complement_clause(session, lo, hi),
        conversation_id_expr().is_not(None),
        *raw_conditions,
    ]
    if until is not None:
        conditions.append(RequestLog.requested_at < until)
    columns: list = [conversation_id_expr().label("cid"), literal(1).label("request_count")]
    if display_bucket_seconds is not None:
        columns.insert(0, _requested_at_epoch_bucket_expr(session, display_bucket_seconds).label("bucket_epoch"))
    return (
        select(*columns)
        .select_from(RequestLog)
        .outerjoin(AccountUsageRollupState, AccountUsageRollupState.id == _STATE_ROW_ID)
        .where(and_(*conditions))
    )


def conversation_presence_union(
    session: AsyncSession,
    since: datetime,
    until: datetime | None = None,
    *,
    include_deleted: bool,
    raw_conditions: Sequence[ColumnElement[bool]] = (),
    display_bucket_seconds: int | None = None,
) -> CompoundSelect:
    """UNION ALL of ``(bucket_epoch?, cid, request_count)`` rows: the folded
    presence inside ``[since, until)`` plus its exact raw complement.

    ``raw_conditions`` MUST be the caller's legacy row filter minus the
    window and the non-empty-conversation predicate (both owned here);
    ``include_deleted`` mirrors whether that filter keeps soft-deleted rows.
    Callers count ``DISTINCT cid`` (dedup across the fold boundary) and/or
    ``SUM(request_count)`` over the union. ``display_bucket_seconds`` (a
    whole multiple of the rollup hour) adds the display-bucket column for
    per-bucket grouping.
    """
    return union_all(
        _conversation_folded_select(
            session, since, until, include_deleted=include_deleted, display_bucket_seconds=display_bucket_seconds
        ),
        _conversation_raw_select(
            session, since, until, raw_conditions=raw_conditions, display_bucket_seconds=display_bucket_seconds
        ),
    )


def conversation_labeled_presence_union(
    session: AsyncSession,
    windows: Sequence[tuple[str, datetime, datetime]],
    *,
    raw_conditions: Sequence[ColumnElement[bool]] = (),
) -> CompoundSelect:
    """``(label, cid)`` UNION ALL rows over labeled half-open windows (the
    reports per-local-day ranges, whose bounds need not be hour-aligned).

    Soft-deleted rows are included on both sides — the reports conversation
    reads carry no ``deleted_at`` filter. Callers group by label and count
    ``DISTINCT cid``; windows are expected pre-batched below the SQLite
    compound-select limit (the reports repository already batches at 500).
    """
    window_rows = [
        select(
            literal(label).label("label"),
            literal(start).label("window_start"),
            literal(end).label("window_end"),
            literal(ceil_to_grid(start, HOURLY_BUCKET_SECONDS)).label("fold_lo_at"),
            literal(floor_to_grid(end, HOURLY_BUCKET_SECONDS)).label("fold_hi_at"),
            literal(epoch_seconds(ceil_to_grid(start, HOURLY_BUCKET_SECONDS))).label("fold_lo_epoch"),
            literal(epoch_seconds(floor_to_grid(end, HOURLY_BUCKET_SECONDS))).label("fold_hi_epoch"),
        )
        for label, start, end in windows
    ]
    windows_cte = (window_rows[0] if len(window_rows) == 1 else union_all(*window_rows)).cte("conversation_windows")
    rollup = RequestConversationHourlyRollup
    folded = select(windows_cte.c.label, rollup.conversation_id.label("cid")).select_from(
        windows_cte.join(AccountUsageRollupState, AccountUsageRollupState.id == _STATE_ROW_ID).join(
            rollup,
            and_(
                rollup.bucket_epoch >= windows_cte.c.fold_lo_epoch,
                rollup.bucket_epoch < windows_cte.c.fold_hi_epoch,
                rollup.bucket_epoch < _conversation_watermark_epoch_expr(session),
            ),
        )
    )
    watermark = AccountUsageRollupState.conversation_folded_through
    raw = (
        select(windows_cte.c.label, conversation_id_expr().label("cid"))
        .select_from(
            windows_cte.join(
                RequestLog,
                and_(
                    RequestLog.requested_at >= windows_cte.c.window_start,
                    RequestLog.requested_at < windows_cte.c.window_end,
                    conversation_id_expr().is_not(None),
                    *raw_conditions,
                ),
            ).outerjoin(AccountUsageRollupState, AccountUsageRollupState.id == _STATE_ROW_ID)
        )
        .where(
            or_(
                watermark.is_(None),
                RequestLog.requested_at < windows_cte.c.fold_lo_at,
                RequestLog.requested_at >= _least_fn(session)(watermark, windows_cte.c.fold_hi_at),
            )
        )
    )
    return union_all(folded, raw)


async def earliest_hourly_bucket_at(session: AsyncSession) -> datetime | None:
    """Hour-precision earliest countable activity according to the rollups
    (warmup kinds excluded, mirroring the raw earliest-activity filter).
    Used as a fallback when retention has pruned raw below the watermark."""
    stmt = select(func.min(RequestUsageHourlyRollup.bucket_epoch)).where(
        RequestUsageHourlyRollup.request_kind.not_in(WARMUP_REQUEST_KINDS)
    )
    earliest = (await session.execute(stmt)).scalar_one_or_none()
    return None if earliest is None else epoch_to_datetime(int(earliest))

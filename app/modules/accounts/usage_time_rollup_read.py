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
  ``[since, ceil_grid(since))`` (always served from raw — if retention has
  already pruned it, the loss is a deterministic sub-bucket undercount,
  documented as out of parity scope) and the tail ``[hi, until)``.
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
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RequestLog, RequestUsageHourlyRollup
from app.modules.accounts.usage_time_rollup import (
    HOURLY_BUCKET_SECONDS,
    QUARTER_SLOT_SECONDS,
    WARMUP_REQUEST_KINDS,
    HourlyErrorRollupRow,
    HourlyUsageRollupRow,
    QuarterDemandRollupRow,
    RequestUsageTimeRollupRepository,
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


async def earliest_hourly_bucket_at(session: AsyncSession) -> datetime | None:
    """Hour-precision earliest countable activity according to the rollups
    (warmup kinds excluded, mirroring the raw earliest-activity filter).
    Used as a fallback when retention has pruned raw below the watermark."""
    stmt = select(func.min(RequestUsageHourlyRollup.bucket_epoch)).where(
        RequestUsageHourlyRollup.request_kind.not_in(WARMUP_REQUEST_KINDS)
    )
    earliest = (await session.execute(stmt)).scalar_one_or_none()
    return None if earliest is None else epoch_to_datetime(int(earliest))

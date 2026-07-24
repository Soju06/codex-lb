"""Time-axis request-usage rollups: schema primitives and repository.

Three permanent aggregate tables serve the dashboard/statistics read paths
without scanning raw ``request_logs``:

- ``request_usage_hourly_rollups`` — UTC hour buckets x (account_id,
  api_key_id, model, service_tier, request_kind, is_deleted).
- ``request_usage_hourly_error_rollups`` — UTC hour buckets x (account_id,
  error_code); the top-error satellite (unbounded cardinality isolated).
- ``request_demand_quarter_rollups`` — 900s slots x (account_id,
  request_kind, is_deleted) for the quota planner.

Watermark contract: ``account_usage_rollup_state.hourly_folded_through`` (the
same single state row as the lifetime fold, so one ``FOR UPDATE`` row lock
serializes every fold and lifecycle mutation) is ALWAYS aligned to a whole
UTC hour. Raw rows with ``requested_at < watermark`` are fully folded; rows
at or above it are the live tail. Buckets are half-open ``[start, end)``.

NULL-dimension sentinel: nullable raw dimensions (account_id, api_key_id,
service_tier) are stored as ``''`` so they can participate in the primary
key on both dialects (UNIQUE/PK treat NULLs as distinct rows on PostgreSQL
and SQLite). Use :func:`to_dimension` / :func:`from_dimension` at the write
and read boundaries — a missed mapping silently diverges rollups from raw.

History-rewrite discipline (MUST): any code path that mutates
``requested_at``, ``deleted_at``, ``account_id``, ``api_key_id``, or an
aggregated measure column of ``request_logs`` rows BELOW the hourly
watermark must take ``lock_fold_state()`` in the same transaction and mirror
the mutation into all three rollup tables (folded buckets are never
recomputed from raw — raw may already be pruned by retention).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import astuple, dataclass
from datetime import datetime

from sqlalchemy import and_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AccountUsageRollupState,
    RequestDemandQuarterRollup,
    RequestUsageHourlyErrorRollup,
    RequestUsageHourlyRollup,
)
from app.modules.accounts.usage_rollup import _STATE_ROW_ID, _insert_fn

HOURLY_BUCKET_SECONDS = 3600
QUARTER_SLOT_SECONDS = 900

# Stored stand-in for NULL account_id / api_key_id / service_tier (PK columns
# cannot be NULL, and NULLs would be distinct under a unique constraint).
DIMENSION_SENTINEL = ""


def to_dimension(value: str | None) -> str:
    """Map a nullable raw dimension value to its stored PK representation."""
    return DIMENSION_SENTINEL if value is None else value


def from_dimension(value: str) -> str | None:
    """Map a stored dimension value back to the raw (nullable) domain."""
    return None if value == DIMENSION_SENTINEL else value


@dataclass(frozen=True, slots=True)
class HourlyUsageRollupRow:
    bucket_epoch: int
    account_id: str
    api_key_id: str
    model: str
    service_tier: str
    request_kind: str
    is_deleted: bool
    request_count: int = 0
    error_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    output_or_reasoning_tokens: int = 0
    cached_input_tokens: int = 0
    cached_input_tokens_clamped: int = 0
    cost_usd: float = 0.0
    cost_count: int = 0


@dataclass(frozen=True, slots=True)
class HourlyErrorRollupRow:
    bucket_epoch: int
    account_id: str
    error_code: str
    error_count: int = 0


@dataclass(frozen=True, slots=True)
class QuarterDemandRollupRow:
    slot_epoch: int
    account_id: str
    request_kind: str
    is_deleted: bool
    request_count: int = 0
    input_tokens: int = 0
    output_or_reasoning_tokens: int = 0
    cached_input_tokens: int = 0
    cost_usd: float = 0.0


_HOURLY_KEY_COLUMNS = (
    "bucket_epoch",
    "account_id",
    "api_key_id",
    "model",
    "service_tier",
    "request_kind",
    "is_deleted",
)
_HOURLY_MEASURE_COLUMNS = (
    "request_count",
    "error_count",
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "output_or_reasoning_tokens",
    "cached_input_tokens",
    "cached_input_tokens_clamped",
    "cost_usd",
    "cost_count",
)
_ERROR_KEY_COLUMNS = ("bucket_epoch", "account_id", "error_code")
_ERROR_MEASURE_COLUMNS = ("error_count",)
_QUARTER_KEY_COLUMNS = ("slot_epoch", "account_id", "request_kind", "is_deleted")
_QUARTER_MEASURE_COLUMNS = (
    "request_count",
    "input_tokens",
    "output_or_reasoning_tokens",
    "cached_input_tokens",
    "cost_usd",
)


def _merge_rows(rows: Iterable, key_width: int, columns: tuple[str, ...], row_type):
    """Pre-merge rows sharing a PK so one multi-row INSERT never touches the
    same conflict target twice (PostgreSQL rejects that outright)."""
    merged: dict[tuple, list] = {}
    for row in rows:
        values = list(astuple(row))
        key = tuple(values[:key_width])
        existing = merged.get(key)
        if existing is None:
            merged[key] = values
        else:
            for index in range(key_width, len(columns)):
                existing[index] += values[index]
    return [row_type(*values) for values in merged.values()]


def _add_rows_stmt(
    session: AsyncSession, model, rows: Sequence, key_columns: tuple[str, ...], columns: tuple[str, ...]
):
    measure_columns = columns[len(key_columns) :]
    stmt = _insert_fn(session)(model).values([dict(zip(columns, astuple(row), strict=True)) for row in rows])
    return stmt.on_conflict_do_update(
        index_elements=[getattr(model, column) for column in key_columns],
        set_={column: getattr(model, column) + getattr(stmt.excluded, column) for column in measure_columns},
    )


class RequestUsageTimeRollupRepository:
    """Upsert (merge-add) and range reads for the three time-axis rollups.

    Every read returns ``(rows, hourly watermark)`` from ONE statement
    (state LEFT JOIN rollup): a single statement sees a single snapshot even
    under READ COMMITTED, so a fold slice committing concurrently can never
    yield rollup rows from one watermark generation and a watermark from
    another. Callers derive the raw-tail window from the returned watermark.
    Range bounds are half-open ``[since_epoch, until_epoch)``.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_hourly(self, rows: Sequence[HourlyUsageRollupRow]) -> None:
        """Merge-add hourly rows: inserts new (bucket, dimensions) rows and
        adds measures onto existing ones. Used by lifecycle mirrors and
        tests; the fold pass itself writes via DELETE-then-INSERT..SELECT."""
        merged = _merge_rows(
            rows, len(_HOURLY_KEY_COLUMNS), _HOURLY_KEY_COLUMNS + _HOURLY_MEASURE_COLUMNS, HourlyUsageRollupRow
        )
        if not merged:
            return
        await self._session.execute(
            _add_rows_stmt(
                self._session,
                RequestUsageHourlyRollup,
                merged,
                _HOURLY_KEY_COLUMNS,
                _HOURLY_KEY_COLUMNS + _HOURLY_MEASURE_COLUMNS,
            )
        )

    async def add_errors(self, rows: Sequence[HourlyErrorRollupRow]) -> None:
        merged = _merge_rows(
            rows, len(_ERROR_KEY_COLUMNS), _ERROR_KEY_COLUMNS + _ERROR_MEASURE_COLUMNS, HourlyErrorRollupRow
        )
        if not merged:
            return
        await self._session.execute(
            _add_rows_stmt(
                self._session,
                RequestUsageHourlyErrorRollup,
                merged,
                _ERROR_KEY_COLUMNS,
                _ERROR_KEY_COLUMNS + _ERROR_MEASURE_COLUMNS,
            )
        )

    async def add_demand(self, rows: Sequence[QuarterDemandRollupRow]) -> None:
        merged = _merge_rows(
            rows, len(_QUARTER_KEY_COLUMNS), _QUARTER_KEY_COLUMNS + _QUARTER_MEASURE_COLUMNS, QuarterDemandRollupRow
        )
        if not merged:
            return
        await self._session.execute(
            _add_rows_stmt(
                self._session,
                RequestDemandQuarterRollup,
                merged,
                _QUARTER_KEY_COLUMNS,
                _QUARTER_KEY_COLUMNS + _QUARTER_MEASURE_COLUMNS,
            )
        )

    async def read_hourly(
        self, *, since_epoch: int | None = None, until_epoch: int | None = None
    ) -> tuple[list[HourlyUsageRollupRow], datetime | None]:
        return await self._read(
            RequestUsageHourlyRollup,
            RequestUsageHourlyRollup.bucket_epoch,
            _HOURLY_KEY_COLUMNS + _HOURLY_MEASURE_COLUMNS,
            HourlyUsageRollupRow,
            since_epoch,
            until_epoch,
        )

    async def read_errors(
        self, *, since_epoch: int | None = None, until_epoch: int | None = None
    ) -> tuple[list[HourlyErrorRollupRow], datetime | None]:
        return await self._read(
            RequestUsageHourlyErrorRollup,
            RequestUsageHourlyErrorRollup.bucket_epoch,
            _ERROR_KEY_COLUMNS + _ERROR_MEASURE_COLUMNS,
            HourlyErrorRollupRow,
            since_epoch,
            until_epoch,
        )

    async def read_demand(
        self, *, since_epoch: int | None = None, until_epoch: int | None = None
    ) -> tuple[list[QuarterDemandRollupRow], datetime | None]:
        return await self._read(
            RequestDemandQuarterRollup,
            RequestDemandQuarterRollup.slot_epoch,
            _QUARTER_KEY_COLUMNS + _QUARTER_MEASURE_COLUMNS,
            QuarterDemandRollupRow,
            since_epoch,
            until_epoch,
        )

    async def _read(self, model, epoch_column, columns: tuple[str, ...], row_type, since_epoch, until_epoch):
        join_conditions = []
        if since_epoch is not None:
            join_conditions.append(epoch_column >= since_epoch)
        if until_epoch is not None:
            join_conditions.append(epoch_column < until_epoch)
        stmt = (
            select(
                AccountUsageRollupState.hourly_folded_through,
                *(getattr(model, column) for column in columns),
            )
            .select_from(AccountUsageRollupState)
            .outerjoin(model, and_(*join_conditions) if join_conditions else true())
            .where(AccountUsageRollupState.id == _STATE_ROW_ID)
        )
        rows = (await self._session.execute(stmt)).all()
        if not rows:
            return [], None
        watermark = rows[0][0]
        return [row_type(*row[1:]) for row in rows if row[1] is not None], watermark

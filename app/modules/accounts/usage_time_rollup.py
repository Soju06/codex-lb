"""Time-axis request-usage rollups: schema primitives and repository.

Three permanent aggregate tables serve the dashboard/statistics read paths
without scanning raw ``request_logs``:

- ``request_usage_hourly_rollups`` — UTC hour buckets x (account_id,
  api_key_id, model, service_tier, request_kind, is_deleted).
- ``request_usage_hourly_error_rollups`` — UTC hour buckets x (account_id,
  error_code); the top-error satellite (unbounded cardinality isolated).
- ``request_demand_quarter_rollups`` — 900s slots x (account_id, api_key_id,
  model, reasoning_effort, request_kind, status, is_deleted) for the quota
  planner. The full legacy demand grain is preserved on purpose: the
  planner's ``_bin_demand_units`` applies ``max()`` PER BIN before summing
  (nonlinear), so folding to a coarser grain would change forecasts.

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

History-rewrite discipline (MUST): any code path that mutates a folded
dimension (``requested_at``, ``deleted_at``, ``account_id``, ``api_key_id``,
``model``, ``service_tier``, ``reasoning_effort``, ``request_kind``,
``status``, ``error_code``) or an aggregated measure column of
``request_logs`` rows BELOW the hourly watermark must take
``lock_fold_state()`` in the same transaction and either mirror the mutation
into all three rollup tables or skip the pre-watermark rows (folded buckets
are never recomputed from raw — raw may already be pruned by retention).
``RequestLogsRepository.update_model_for_request`` takes the skip route.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import astuple, dataclass, replace
from datetime import datetime, timedelta

from sqlalchemy import BigInteger, ColumnElement, Integer, and_, case, cast, delete, func, insert, select, true, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time import utcnow
from app.db.models import (
    AccountUsageRollupState,
    RequestDemandQuarterRollup,
    RequestLog,
    RequestUsageHourlyErrorRollup,
    RequestUsageHourlyRollup,
)
from app.db.session import get_background_session, sqlite_writer_section
from app.modules.accounts.usage_rollup import (
    _STATE_ROW_ID,
    FOLD_LAG,
    _FoldStatus,
    _insert_fn,
    _locked_state,
    _state_bootstrap_stmt,
)

logger = logging.getLogger(__name__)

HOURLY_BUCKET_SECONDS = 3600
QUARTER_SLOT_SECONDS = 900

# Historical backfill folds at most this much history per slice transaction
# and at most TS_MAX_SLICES_PER_PASS slices per pass, so the initial backfill
# (millions of raw rows) spreads its I/O bursts and fold-state row-lock
# occupancy across scheduler ticks instead of one giant catch-up.
TS_FOLD_SLICE = timedelta(hours=48)
TS_MAX_SLICES_PER_PASS = 20

_EPOCH = datetime(1970, 1, 1)

# Synthetic request kinds every statistics read path filters out. Folded
# verbatim as a dimension; readers exclude them bucket-side exactly as the
# legacy raw queries do row-side.
WARMUP_REQUEST_KINDS = ("warmup", "limit_warmup")
_EXCLUDED_REQUEST_KINDS = WARMUP_REQUEST_KINDS

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
    api_key_id: str
    model: str
    reasoning_effort: str
    request_kind: str
    status: str
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
_QUARTER_KEY_COLUMNS = (
    "slot_epoch",
    "account_id",
    "api_key_id",
    "model",
    "reasoning_effort",
    "request_kind",
    "status",
    "is_deleted",
)
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


# Rows per multi-VALUES upsert statement. asyncpg rejects statements with
# more than 32,767 bind parameters; at 17 columns (the widest table) 1,000
# rows binds 17,000 — comfortable margin on both dialects. Lifecycle mirrors
# rekey an account's ENTIRE folded history in one call (thousands of rows
# for a long-lived account), so unchunked upserts would abort the whole
# lifecycle transaction.
_UPSERT_CHUNK_ROWS = 1_000


def _add_rows_stmt(
    session: AsyncSession, model, rows: Sequence, key_columns: tuple[str, ...], columns: tuple[str, ...]
):
    measure_columns = columns[len(key_columns) :]
    stmt = _insert_fn(session)(model).values([dict(zip(columns, astuple(row), strict=True)) for row in rows])
    return stmt.on_conflict_do_update(
        index_elements=[getattr(model, column) for column in key_columns],
        set_={column: getattr(model, column) + getattr(stmt.excluded, column) for column in measure_columns},
    )


async def _merge_add(
    session: AsyncSession, model, rows: Sequence, key_columns: tuple[str, ...], columns: tuple[str, ...], row_type
) -> None:
    merged = _merge_rows(rows, len(key_columns), columns, row_type)
    for start in range(0, len(merged), _UPSERT_CHUNK_ROWS):
        chunk = merged[start : start + _UPSERT_CHUNK_ROWS]
        await session.execute(_add_rows_stmt(session, model, chunk, key_columns, columns))


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
        adds measures onto existing ones, in bounded statement chunks. Used
        by lifecycle mirrors and tests; the fold pass itself writes via
        DELETE-then-INSERT..SELECT."""
        await _merge_add(
            self._session,
            RequestUsageHourlyRollup,
            rows,
            _HOURLY_KEY_COLUMNS,
            _HOURLY_KEY_COLUMNS + _HOURLY_MEASURE_COLUMNS,
            HourlyUsageRollupRow,
        )

    async def add_errors(self, rows: Sequence[HourlyErrorRollupRow]) -> None:
        await _merge_add(
            self._session,
            RequestUsageHourlyErrorRollup,
            rows,
            _ERROR_KEY_COLUMNS,
            _ERROR_KEY_COLUMNS + _ERROR_MEASURE_COLUMNS,
            HourlyErrorRollupRow,
        )

    async def add_demand(self, rows: Sequence[QuarterDemandRollupRow]) -> None:
        await _merge_add(
            self._session,
            RequestDemandQuarterRollup,
            rows,
            _QUARTER_KEY_COLUMNS,
            _QUARTER_KEY_COLUMNS + _QUARTER_MEASURE_COLUMNS,
            QuarterDemandRollupRow,
        )

    async def read_hourly(
        self,
        *,
        since_epoch: int | None = None,
        until_epoch: int | None = None,
        filters: Sequence[ColumnElement[bool]] = (),
    ) -> tuple[list[HourlyUsageRollupRow], datetime | None]:
        return await self._read(
            RequestUsageHourlyRollup,
            RequestUsageHourlyRollup.bucket_epoch,
            _HOURLY_KEY_COLUMNS + _HOURLY_MEASURE_COLUMNS,
            HourlyUsageRollupRow,
            since_epoch,
            until_epoch,
            filters,
        )

    async def read_errors(
        self,
        *,
        since_epoch: int | None = None,
        until_epoch: int | None = None,
        filters: Sequence[ColumnElement[bool]] = (),
    ) -> tuple[list[HourlyErrorRollupRow], datetime | None]:
        return await self._read(
            RequestUsageHourlyErrorRollup,
            RequestUsageHourlyErrorRollup.bucket_epoch,
            _ERROR_KEY_COLUMNS + _ERROR_MEASURE_COLUMNS,
            HourlyErrorRollupRow,
            since_epoch,
            until_epoch,
            filters,
        )

    async def read_demand(
        self,
        *,
        since_epoch: int | None = None,
        until_epoch: int | None = None,
        filters: Sequence[ColumnElement[bool]] = (),
    ) -> tuple[list[QuarterDemandRollupRow], datetime | None]:
        return await self._read(
            RequestDemandQuarterRollup,
            RequestDemandQuarterRollup.slot_epoch,
            _QUARTER_KEY_COLUMNS + _QUARTER_MEASURE_COLUMNS,
            QuarterDemandRollupRow,
            since_epoch,
            until_epoch,
            filters,
        )

    async def _read(
        self, model, epoch_column, columns: tuple[str, ...], row_type, since_epoch, until_epoch, filters=()
    ):
        join_conditions = list(filters)
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


def epoch_seconds(value: datetime) -> int:
    """Naive-UTC datetime to Unix epoch seconds (truncating sub-seconds)."""
    return int((value - _EPOCH).total_seconds())


def floor_to_hour(value: datetime) -> datetime:
    """Floor a naive-UTC datetime to its whole UTC hour."""
    return _EPOCH + timedelta(seconds=(epoch_seconds(value) // HOURLY_BUCKET_SECONDS) * HOURLY_BUCKET_SECONDS)


def _requested_at_epoch_bucket_expr(session: AsyncSession, bucket_seconds: int) -> ColumnElement:
    """Dialect-split bucket expression, identical arithmetic to the runtime
    bucketing the read paths use (`RequestLogsRepository._bucket_epoch_expr`),
    so folded buckets and legacy raw bucketing can never disagree."""
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        return cast(
            func.floor(func.extract("epoch", RequestLog.requested_at) / bucket_seconds) * bucket_seconds,
            BigInteger,
        )
    epoch_col = cast(func.strftime("%s", RequestLog.requested_at), Integer)
    return cast(epoch_col / bucket_seconds, Integer) * bucket_seconds


def _hourly_fold_insert(session: AsyncSession, window: tuple[ColumnElement, ...]):
    bucket = _requested_at_epoch_bucket_expr(session, HOURLY_BUCKET_SECONDS).label("bucket_epoch")
    account_id = func.coalesce(RequestLog.account_id, DIMENSION_SENTINEL).label("account_id")
    api_key_id = func.coalesce(RequestLog.api_key_id, DIMENSION_SENTINEL).label("api_key_id")
    service_tier = func.coalesce(RequestLog.service_tier, DIMENSION_SENTINEL).label("service_tier")
    is_deleted = RequestLog.deleted_at.is_not(None).label("is_deleted")
    output_or_reasoning = func.coalesce(RequestLog.output_tokens, RequestLog.reasoning_tokens, 0)
    # Exact mirror of the usage-summary reader's per-row clamp
    # (`aggregate_usage_metrics_since` / `cached_input_tokens_from_log`):
    # NULL cached counts 0, a NULL input keeps the (non-negative) cached
    # value UNclamped, otherwise clamp to [0, input]. SQLite's two-argument
    # min()/max() scalar functions are its least()/greatest().
    dialect = session.get_bind().dialect.name
    least = func.least if dialect == "postgresql" else func.min
    greatest = func.greatest if dialect == "postgresql" else func.max
    cached_clamped = case(
        (RequestLog.cached_input_tokens.is_(None), 0),
        (RequestLog.input_tokens.is_(None), greatest(0, RequestLog.cached_input_tokens)),
        else_=greatest(0, least(RequestLog.cached_input_tokens, RequestLog.input_tokens)),
    )
    stmt = (
        select(
            bucket,
            account_id,
            api_key_id,
            RequestLog.model,
            service_tier,
            RequestLog.request_kind,
            is_deleted,
            func.count(RequestLog.id),
            func.coalesce(func.sum(case((RequestLog.status != "success", 1), else_=0)), 0),
            func.coalesce(func.sum(RequestLog.input_tokens), 0),
            func.coalesce(func.sum(RequestLog.output_tokens), 0),
            func.coalesce(func.sum(RequestLog.reasoning_tokens), 0),
            func.coalesce(func.sum(output_or_reasoning), 0),
            func.coalesce(func.sum(RequestLog.cached_input_tokens), 0),
            func.coalesce(func.sum(cached_clamped), 0),
            func.coalesce(func.sum(RequestLog.cost_usd), 0.0),
            func.coalesce(func.sum(case((RequestLog.cost_usd.is_not(None), 1), else_=0)), 0),
        )
        .where(*window)
        .group_by(bucket, account_id, api_key_id, RequestLog.model, service_tier, RequestLog.request_kind, is_deleted)
    )
    return insert(RequestUsageHourlyRollup).from_select(list(_HOURLY_KEY_COLUMNS + _HOURLY_MEASURE_COLUMNS), stmt)


def _error_fold_insert(session: AsyncSession, window: tuple[ColumnElement, ...]):
    bucket = _requested_at_epoch_bucket_expr(session, HOURLY_BUCKET_SECONDS).label("bucket_epoch")
    account_id = func.coalesce(RequestLog.account_id, DIMENSION_SENTINEL).label("account_id")
    # Exact reproduction of the top-error read filter:
    # warmup kinds excluded, soft-deleted rows INCLUDED.
    stmt = (
        select(bucket, account_id, RequestLog.error_code, func.count(RequestLog.id))
        .where(
            *window,
            RequestLog.request_kind.not_in(_EXCLUDED_REQUEST_KINDS),
            RequestLog.status != "success",
            RequestLog.error_code.is_not(None),
        )
        .group_by(bucket, account_id, RequestLog.error_code)
    )
    return insert(RequestUsageHourlyErrorRollup).from_select(list(_ERROR_KEY_COLUMNS + _ERROR_MEASURE_COLUMNS), stmt)


def _demand_fold_insert(session: AsyncSession, window: tuple[ColumnElement, ...]):
    # Full legacy demand grain (slot, account, api_key, model,
    # reasoning_effort, kind, status): the planner's `_bin_demand_units`
    # takes max(token, cost, request units) PER BIN before summing, so
    # folding to a coarser grain would shrink forecasts wherever one slot
    # mixes groups with different dominant components.
    slot = _requested_at_epoch_bucket_expr(session, QUARTER_SLOT_SECONDS).label("slot_epoch")
    account_id = func.coalesce(RequestLog.account_id, DIMENSION_SENTINEL).label("account_id")
    api_key_id = func.coalesce(RequestLog.api_key_id, DIMENSION_SENTINEL).label("api_key_id")
    reasoning_effort = func.coalesce(RequestLog.reasoning_effort, DIMENSION_SENTINEL).label("reasoning_effort")
    is_deleted = RequestLog.deleted_at.is_not(None).label("is_deleted")
    output_or_reasoning = func.coalesce(RequestLog.output_tokens, RequestLog.reasoning_tokens, 0)
    stmt = (
        select(
            slot,
            account_id,
            api_key_id,
            RequestLog.model,
            reasoning_effort,
            RequestLog.request_kind,
            RequestLog.status,
            is_deleted,
            func.count(RequestLog.id),
            func.coalesce(func.sum(RequestLog.input_tokens), 0),
            func.coalesce(func.sum(output_or_reasoning), 0),
            func.coalesce(func.sum(RequestLog.cached_input_tokens), 0),
            func.coalesce(func.sum(RequestLog.cost_usd), 0.0),
        )
        .where(*window)
        .group_by(
            slot,
            account_id,
            api_key_id,
            RequestLog.model,
            reasoning_effort,
            RequestLog.request_kind,
            RequestLog.status,
            is_deleted,
        )
    )
    return insert(RequestDemandQuarterRollup).from_select(list(_QUARTER_KEY_COLUMNS + _QUARTER_MEASURE_COLUMNS), stmt)


async def run_hourly_fold_pass(*, now: datetime | None = None) -> int:
    """Advance the hourly watermark toward `floor_hour(now - FOLD_LAG)`.

    Bounded slices, each committed in its own transaction; at most
    `TS_MAX_SLICES_PER_PASS` slices per pass, so the initial backfill resumes
    across scheduler ticks instead of monopolizing one. Returns the number of
    committed slices. Crash-safe: the defensive DELETE, the three
    INSERT..SELECTs and the watermark advance commit atomically, so a crash
    rolls the whole slice back and the retry recomputes it from scratch —
    re-folding always converges to the same values (no add-fold double
    counting is possible).
    """
    target = floor_to_hour((now or utcnow()) - FOLD_LAG)
    committed = 0
    while committed < TS_MAX_SLICES_PER_PASS:
        async with get_background_session() as session:
            status, wrote = await _fold_next_hourly_slice(session, target)
        if wrote:
            committed += 1
        if status is _FoldStatus.DONE:
            break
    return committed


async def _fold_next_hourly_slice(session: AsyncSession, target: datetime) -> tuple[_FoldStatus, bool]:
    async with sqlite_writer_section():
        # Same state row (id=1) as the lifetime fold: one FOR UPDATE row lock
        # serializes concurrent hourly passes, lifetime passes, and lifecycle
        # mirrors. Re-read the watermark AFTER taking the lock — a concurrent
        # pass may have advanced it while we waited.
        state = await _locked_state(session)
        if state is None:
            await session.execute(_state_bootstrap_stmt(session))
            await session.commit()
            state = await _locked_state(session)
        if state is None:
            logger.warning("account_usage_rollup_state row missing; skipping hourly fold pass")
            return _FoldStatus.DONE, False
        watermark = state.hourly_folded_through
        if watermark >= target:
            return _FoldStatus.DONE, False

        # Next populated instant in [watermark, target): jumps the empty
        # prefix on first backfill and any mid-history gap on later slices,
        # and guarantees the slice's first hour actually holds rows. Every
        # row counts for at least one of the three aggregates (the hourly
        # fold has no filter), so no filtered variant is needed.
        next_populated = (
            await session.execute(
                select(func.min(RequestLog.requested_at)).where(
                    RequestLog.requested_at >= watermark,
                    RequestLog.requested_at < target,
                )
            )
        ).scalar_one_or_none()
        if next_populated is None:
            # Nothing left below the target. Advancing (rather than leaving
            # the watermark behind) keeps the readers' raw-tail window and
            # the retention min-gate current; FOLD_LAG guarantees no insert
            # can land below `now - FOLD_LAG`, so nothing can appear behind
            # the advanced watermark later.
            await _advance_hourly_watermark(session, target)
            await session.commit()
            logger.info("Folded hourly usage rollups through %s (no rows below target)", target.isoformat())
            return _FoldStatus.DONE, True

        start = max(watermark, floor_to_hour(next_populated))
        slice_end = min(start + TS_FOLD_SLICE, target)
        start_epoch, end_epoch = epoch_seconds(start), epoch_seconds(slice_end)

        # Defensive DELETE: zero rows on the normal path (the watermark only
        # moves forward), but makes an operator watermark reset (escape
        # hatch) converge — a re-fold can never double-count or leave rows
        # from a previous fold generation behind. Convergence is guaranteed
        # under the escape hatch's documented precondition (raw below the
        # target still present, or the reset truncated the rollups in the
        # same transaction): every previously-folded hour then either has
        # raw rows (re-covered by a slice window and its DELETE) or no
        # rollup rows. Hours the min()-jump skips are deliberately NOT
        # cleared — after a rewind-only reset over retention-pruned history
        # (the documented forbidden state) the skipped rollup rows are the
        # ONLY surviving copy of those statistics, and deleting them would
        # turn an operator mistake into permanent data loss.
        await session.execute(
            delete(RequestUsageHourlyRollup).where(
                RequestUsageHourlyRollup.bucket_epoch >= start_epoch,
                RequestUsageHourlyRollup.bucket_epoch < end_epoch,
            )
        )
        await session.execute(
            delete(RequestUsageHourlyErrorRollup).where(
                RequestUsageHourlyErrorRollup.bucket_epoch >= start_epoch,
                RequestUsageHourlyErrorRollup.bucket_epoch < end_epoch,
            )
        )
        await session.execute(
            delete(RequestDemandQuarterRollup).where(
                RequestDemandQuarterRollup.slot_epoch >= start_epoch,
                RequestDemandQuarterRollup.slot_epoch < end_epoch,
            )
        )

        # Half-open [start, slice_end): hour-aligned bounds, so a display
        # bucket is never split between the folded side and the raw tail.
        window = (RequestLog.requested_at >= start, RequestLog.requested_at < slice_end)
        await session.execute(_hourly_fold_insert(session, window))
        await session.execute(_error_fold_insert(session, window))
        await session.execute(_demand_fold_insert(session, window))
        await _advance_hourly_watermark(session, slice_end)
        await session.commit()
        logger.info("Folded hourly usage rollups through %s", slice_end.isoformat())
        return (_FoldStatus.DONE if slice_end >= target else _FoldStatus.CONTINUE), True


async def _advance_hourly_watermark(session: AsyncSession, value: datetime) -> None:
    await session.execute(
        update(AccountUsageRollupState)
        .where(AccountUsageRollupState.id == _STATE_ROW_ID)
        .values(hourly_folded_through=value)
    )


# --- Account lifecycle mirrors -------------------------------------------
#
# The ONLY code paths allowed to touch folded buckets after the watermark
# passed them. They mirror the raw request_logs mutation exactly (a dimension
# move, never a recompute — raw below the watermark may already be pruned).
# Callers MUST hold the fold-state lock (`lock_fold_state`) in the same
# transaction, so a mirror can never interleave with an in-flight fold slice.

_ROLLUP_TABLES = (
    (RequestUsageHourlyRollup, _HOURLY_KEY_COLUMNS + _HOURLY_MEASURE_COLUMNS, HourlyUsageRollupRow, "add_hourly"),
    (RequestUsageHourlyErrorRollup, _ERROR_KEY_COLUMNS + _ERROR_MEASURE_COLUMNS, HourlyErrorRollupRow, "add_errors"),
    (RequestDemandQuarterRollup, _QUARTER_KEY_COLUMNS + _QUARTER_MEASURE_COLUMNS, QuarterDemandRollupRow, "add_demand"),
)


async def _rekey_account_rows(session: AsyncSession, account_ids: list[str], rekey) -> None:
    repo = RequestUsageTimeRollupRepository(session)
    for model, columns, row_type, adder in _ROLLUP_TABLES:
        stmt = select(*(getattr(model, column) for column in columns)).where(model.account_id.in_(account_ids))
        rows = [row_type(*row) for row in (await session.execute(stmt)).all()]
        if not rows:
            continue
        await session.execute(delete(model).where(model.account_id.in_(account_ids)))
        await getattr(repo, adder)([rekey(row) for row in rows])


async def mirror_account_soft_delete_into_time_rollups(session: AsyncSession, account_id: str) -> None:
    """Mirror `AccountsRepository.delete()`'s soft path, which retroactively
    detaches the account's ENTIRE raw history (`account_id=NULL,
    deleted_at=now`): folded buckets move to the `('', is_deleted=true)`
    dimension (merge-added — an orphaned-deleted bucket may already exist).
    The error satellite has no `is_deleted` dimension (its read includes
    soft-deleted rows), so only `account_id` is re-keyed there.
    """

    def _rekey(row):
        if isinstance(row, HourlyErrorRollupRow):
            return replace(row, account_id=DIMENSION_SENTINEL)
        return replace(row, account_id=DIMENSION_SENTINEL, is_deleted=True)

    await _rekey_account_rows(session, [account_id], _rekey)


async def mirror_account_hard_delete_into_time_rollups(session: AsyncSession, account_id: str) -> None:
    """Mirror the history-deleting path (raw rows physically removed)."""
    for model, *_rest in _ROLLUP_TABLES:
        await session.execute(delete(model).where(model.account_id == account_id))


async def merge_time_rollups_into(session: AsyncSession, canonical_account_id: str, duplicate_ids: list[str]) -> None:
    """Mirror duplicate-account consolidation, which reassigns the
    duplicates' raw logs to the canonical account: folded buckets follow
    bucket-wise (merge-add onto the canonical dimension, then the duplicate
    rows are removed). Must run in the consolidation transaction, under the
    fold-state lock the caller already holds.
    """
    if not duplicate_ids:
        return
    await _rekey_account_rows(session, duplicate_ids, lambda row: replace(row, account_id=canonical_account_id))

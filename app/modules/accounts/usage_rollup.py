from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from sqlalchemy import Select, func, select, true, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time import utcnow
from app.db.models import Account, AccountUsageRollup, AccountUsageRollupState, ApiKey, ApiKeyUsageRollup, RequestLog
from app.db.session import get_background_session, sqlite_writer_section

logger = logging.getLogger(__name__)

# Rows younger than the lag stay on the live side of the fold boundary.
# The lag MUST exceed the maximum possible distance between a log row's
# requested_at and the moment its insert becomes visible: a row landing below
# an already-advanced watermark is neither folded nor in the live tail and
# vanishes from totals. Every insert path stamps requested_at inside
# ``RequestLogsRepository.add_log`` at write time (``requested_at or
# utcnow()``; no live caller passes an explicit value — the parameter exists
# for tests), so the distance is only clock skew between replicas plus the
# single-row insert transaction's commit latency, NOT the request duration:
# a stream-end log write is dated at the write, not at the request start.
# Measured over one full production history (6.0M rows) the worst insert
# landed 7.9s below the requested_at frontier (p99.9 = 30ms). Post-insert
# mutators need no allowance here either: ``update_model_for_request``
# skips rows at/below the watermarks, and consolidation/deletion reassign
# logs under the fold-state lock while mirroring the folded sums.
#
# 2h keeps a ~900x margin over the observed worst case (covering scheduler
# stalls, NTP drift, paused VMs) while bounding the raw tail every account
# and API-key summary read must dedupe and re-aggregate; the previous 24h
# lag — sized for a stream-start dating scheme this codebase never actually
# had — left an always-rescanned tail of ~660k rows (11% of the table) on
# the reference deployment and degraded the listing aggregate to a full
# seq-scan hash join (measured 60s cold / 2.1s warm vs 63ms at 2h).
#
# This lag is a shared visibility contract: the hourly/conversation fold
# targets and the retention min-gate (``now - 2 * FOLD_LAG`` freshness,
# ``watermark - FOLD_LAG`` prune floor) derive from the same constant.
# A fold pass after an upgrade from the 24h lag absorbs the watermark jump
# as one ordinary backfill slice (FOLD_SLICE bounds it).
FOLD_LAG = timedelta(hours=2)
# Historical backfill folds at most this much history per transaction.
FOLD_SLICE = timedelta(days=7)

_STATE_ROW_ID = 1
_EPOCH = datetime(1970, 1, 1)
_EXCLUDED_REQUEST_KINDS = ("warmup", "limit_warmup")


@dataclass(frozen=True, slots=True)
class UsageRollupSums:
    request_count: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    total_cost_usd: float


def deduped_usage_aggregate_stmt(
    *,
    account_ids: list[str] | None = None,
    after_exclusive: datetime | None = None,
    until_inclusive: datetime | None = None,
) -> Select[tuple[str | None, int, int | None, int, int | None, float | None]]:
    """Per-account usage aggregate collapsing duplicate rows to the latest id.

    Duplicate request-log rows share the exact `(account_id, request_id,
    requested_at)` key (see #904); only the `max(id)` row of each group
    contributes. Warmup kinds and soft-deleted rows are excluded. Bounds are
    `requested_at`-based, so a duplicate group can never straddle them.
    """
    output_tokens_expr = func.coalesce(RequestLog.output_tokens, RequestLog.reasoning_tokens, 0)
    conditions: list = [
        RequestLog.request_kind.not_in(_EXCLUDED_REQUEST_KINDS),
        RequestLog.deleted_at.is_(None),
        RequestLog.account_id.is_not(None),
    ]
    if account_ids:
        conditions.append(RequestLog.account_id.in_(account_ids))
    if after_exclusive is not None:
        conditions.append(RequestLog.requested_at > after_exclusive)
    if until_inclusive is not None:
        conditions.append(RequestLog.requested_at <= until_inclusive)

    latest_request_log_ids = (
        select(func.max(RequestLog.id).label("request_log_id"))
        .where(*conditions)
        .group_by(
            RequestLog.account_id,
            RequestLog.request_id,
            RequestLog.requested_at,
        )
        .subquery("latest_request_log_ids")
    )
    return (
        select(
            RequestLog.account_id,
            func.count(RequestLog.id).label("request_count"),
            func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(output_tokens_expr), 0).label("output_tokens"),
            func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
            func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("total_cost_usd"),
        )
        .join(latest_request_log_ids, RequestLog.id == latest_request_log_ids.c.request_log_id)
        .group_by(RequestLog.account_id)
    )


def api_key_usage_aggregate_stmt(
    *,
    api_key_ids: list[str] | None = None,
    after_exclusive: datetime | None = None,
    until_inclusive: datetime | None = None,
) -> Select[tuple[str | None, int, int | None, int, int | None, float | None]]:
    """Per-API-key usage aggregate matching the API-key summary semantics:
    no duplicate collapsing, soft-deleted rows included, warmup kinds
    excluded. Bounds are `requested_at`-based, governed by the same fold
    watermark as the account rollup."""
    output_tokens_expr = func.coalesce(RequestLog.output_tokens, RequestLog.reasoning_tokens, 0)
    conditions: list = [
        RequestLog.api_key_id.is_not(None),
        RequestLog.request_kind.not_in(_EXCLUDED_REQUEST_KINDS),
    ]
    if api_key_ids:
        conditions.append(RequestLog.api_key_id.in_(api_key_ids))
    if after_exclusive is not None:
        conditions.append(RequestLog.requested_at > after_exclusive)
    if until_inclusive is not None:
        conditions.append(RequestLog.requested_at <= until_inclusive)
    return (
        select(
            RequestLog.api_key_id,
            func.count(RequestLog.id).label("request_count"),
            func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
            func.coalesce(func.sum(output_tokens_expr), 0).label("output_tokens"),
            func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
            func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("total_cost_usd"),
        )
        .where(*conditions)
        .group_by(RequestLog.api_key_id)
    )


_SUM_COLUMNS = ("request_count", "input_tokens", "output_tokens", "cached_input_tokens", "total_cost_usd")


def _add_rollup_sums_stmt(session: AsyncSession, model, key_field: str, key_value: str, sums: UsageRollupSums):
    stmt = _insert_fn(session)(model).values(
        **{key_field: key_value},
        request_count=sums.request_count,
        input_tokens=sums.input_tokens,
        output_tokens=sums.output_tokens,
        cached_input_tokens=sums.cached_input_tokens,
        total_cost_usd=sums.total_cost_usd,
    )
    return stmt.on_conflict_do_update(
        index_elements=[getattr(model, key_field)],
        set_={column: getattr(model, column) + getattr(stmt.excluded, column) for column in _SUM_COLUMNS},
    )


def _add_sums_stmt(session: AsyncSession, account_id: str, sums: UsageRollupSums):
    return _add_rollup_sums_stmt(session, AccountUsageRollup, "account_id", account_id, sums)


async def lock_fold_state(session: AsyncSession) -> None:
    """Serialize the caller's transaction against fold passes.

    Bootstraps the state row if missing (no commit — the caller's
    transaction owns the insert) and takes its row lock, so a transaction
    that reassigns request-log ownership cannot interleave with a fold
    slice: an in-flight fold commits first, or the fold waits and then
    aggregates the post-commit attribution. Without this, a fold running
    concurrently with duplicate-account consolidation can attribute the
    duplicates' logs to a row that consolidation is about to delete,
    leaving those logs behind the watermark and counted nowhere.
    """
    await session.execute(_state_bootstrap_stmt(session))
    await _locked_state(session)


async def merge_rollups_into(session: AsyncSession, canonical_account_id: str, duplicate_ids: list[str]) -> None:
    """Fold duplicate accounts' rollup sums into the canonical account.

    Must run in the same transaction that reassigns the duplicates' request
    logs, so folded history follows the logs to the canonical account. The
    caller must hold the fold-state lock (`lock_fold_state`) before its
    first request-log reassignment.
    """
    if not duplicate_ids:
        return
    duplicates = (
        (await session.execute(select(AccountUsageRollup).where(AccountUsageRollup.account_id.in_(duplicate_ids))))
        .scalars()
        .all()
    )
    if not duplicates:
        return
    merged = UsageRollupSums(
        request_count=sum(row.request_count for row in duplicates),
        input_tokens=sum(row.input_tokens for row in duplicates),
        output_tokens=sum(row.output_tokens for row in duplicates),
        cached_input_tokens=sum(row.cached_input_tokens for row in duplicates),
        total_cost_usd=sum(row.total_cost_usd for row in duplicates),
    )
    for row in duplicates:
        await session.delete(row)
    await session.flush()
    await session.execute(_add_sums_stmt(session, canonical_account_id, merged))


class AccountUsageRollupRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def read_state(
        self, account_ids: list[str] | None = None
    ) -> tuple[dict[str, UsageRollupSums], datetime | None]:
        """Read rollup sums and the fold watermark in ONE statement.

        A single statement sees a single snapshot even under READ COMMITTED,
        so a fold slice committing concurrently can never yield sums from one
        watermark generation and a watermark from another (which would drop
        the just-folded window from the totals).
        """
        join_on = AccountUsageRollup.account_id.in_(account_ids) if account_ids else true()
        stmt = (
            select(
                AccountUsageRollupState.folded_through,
                AccountUsageRollup.account_id,
                AccountUsageRollup.request_count,
                AccountUsageRollup.input_tokens,
                AccountUsageRollup.output_tokens,
                AccountUsageRollup.cached_input_tokens,
                AccountUsageRollup.total_cost_usd,
            )
            .select_from(AccountUsageRollupState)
            .outerjoin(AccountUsageRollup, join_on)
            .where(AccountUsageRollupState.id == _STATE_ROW_ID)
        )
        rows = (await self._session.execute(stmt)).all()
        if not rows:
            return {}, None
        watermark = rows[0][0]
        sums = {
            account_id: UsageRollupSums(
                request_count=request_count,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_input_tokens,
                total_cost_usd=total_cost_usd,
            )
            for (_, account_id, request_count, input_tokens, output_tokens, cached_input_tokens, total_cost_usd) in rows
            if account_id is not None
        }
        return sums, watermark


async def read_api_key_rollup_state(
    session: AsyncSession, api_key_ids: list[str] | None = None
) -> tuple[dict[str, UsageRollupSums], datetime | None]:
    """Read API-key rollup sums and the fold watermark in ONE statement.

    Same snapshot-consistency reasoning as
    `AccountUsageRollupRepository.read_state`.
    """
    join_on = ApiKeyUsageRollup.api_key_id.in_(api_key_ids) if api_key_ids else true()
    stmt = (
        select(
            AccountUsageRollupState.folded_through,
            ApiKeyUsageRollup.api_key_id,
            ApiKeyUsageRollup.request_count,
            ApiKeyUsageRollup.input_tokens,
            ApiKeyUsageRollup.output_tokens,
            ApiKeyUsageRollup.cached_input_tokens,
            ApiKeyUsageRollup.total_cost_usd,
        )
        .select_from(AccountUsageRollupState)
        .outerjoin(ApiKeyUsageRollup, join_on)
        .where(AccountUsageRollupState.id == _STATE_ROW_ID)
    )
    rows = (await session.execute(stmt)).all()
    if not rows:
        return {}, None
    watermark = rows[0][0]
    sums = {
        api_key_id: UsageRollupSums(
            request_count=request_count,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            total_cost_usd=total_cost_usd,
        )
        for (_, api_key_id, request_count, input_tokens, output_tokens, cached_input_tokens, total_cost_usd) in rows
        if api_key_id is not None
    }
    return sums, watermark


class _FoldStatus(Enum):
    DONE = "done"
    CONTINUE = "continue"


def _insert_fn(session: AsyncSession):
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        return pg_insert
    if dialect == "sqlite":
        return sqlite_insert
    raise RuntimeError(f"AccountUsageRollup upsert unsupported for dialect={dialect!r}")


def _state_bootstrap_stmt(session: AsyncSession):
    # `upgrade_repair_from` is explicitly NULL: a state row bootstrapped by
    # THIS (post-cancelled_count) code is never legacy-suspect. Old code's
    # bootstrap omits the column and gets the epoch server default, which is
    # exactly what marks its post-migration backfill for repair (#1552).
    stmt = _insert_fn(session)(AccountUsageRollupState).values(
        id=_STATE_ROW_ID,
        folded_through=_EPOCH,
        upgrade_repair_from=None,
    )
    return stmt.on_conflict_do_nothing(index_elements=[AccountUsageRollupState.id])


async def _locked_state(session: AsyncSession) -> AccountUsageRollupState | None:
    return (
        await session.execute(
            select(AccountUsageRollupState).where(AccountUsageRollupState.id == _STATE_ROW_ID).with_for_update()
        )
    ).scalar_one_or_none()


async def run_fold_pass(*, now: datetime | None = None) -> int:
    """Advance the rollup watermark to `now - FOLD_LAG` in bounded slices.

    Each slice commits in its own transaction so historical backfill never
    holds one giant transaction. Returns the number of committed slices.
    """
    target = (now or utcnow()) - FOLD_LAG
    committed = 0
    while True:
        async with get_background_session() as session:
            status, wrote = await _fold_next_slice(session, target)
        if wrote:
            committed += 1
        if status is _FoldStatus.DONE:
            return committed


async def _fold_next_slice(session: AsyncSession, target: datetime) -> tuple[_FoldStatus, bool]:
    async with sqlite_writer_section():
        # The state row is seeded by the migration, so FOR UPDATE always has a
        # row to lock: concurrent fold passes (second replica, expired leader
        # lease, operator-triggered re-backfill) serialize here and re-read
        # the advanced watermark instead of folding the same window twice.
        state = await _locked_state(session)
        if state is None:
            # Databases created via metadata.create_all (tests, recovery
            # paths) have the table but not the migration-seeded row;
            # bootstrap it. ON CONFLICT DO NOTHING keeps concurrent
            # bootstrappers serialized on the unique id.
            await session.execute(_state_bootstrap_stmt(session))
            await session.commit()
            state = await _locked_state(session)
        if state is None:
            logger.warning("account_usage_rollup_state row missing; skipping fold pass")
            return _FoldStatus.DONE, False
        watermark = state.folded_through
        if watermark >= target:
            return _FoldStatus.DONE, False

        start = watermark
        # Earliest COUNTABLE row: an old prefix of rows neither aggregate can
        # count would otherwise anchor the backfill start and make passes walk
        # empty 7-day slices while holding the fold-state lock. A row counts
        # if the ACCOUNT aggregate sees it (non-warmup, live, account
        # attached) or the API-KEY aggregate sees it (non-warmup, key
        # attached — soft-deleted rows included), so take the least of both.
        account_earliest = (
            await session.execute(
                select(func.min(RequestLog.requested_at)).where(
                    RequestLog.request_kind.not_in(_EXCLUDED_REQUEST_KINDS),
                    RequestLog.deleted_at.is_(None),
                    RequestLog.account_id.is_not(None),
                )
            )
        ).scalar_one_or_none()
        key_earliest = (
            await session.execute(
                select(func.min(RequestLog.requested_at)).where(
                    RequestLog.request_kind.not_in(_EXCLUDED_REQUEST_KINDS),
                    RequestLog.api_key_id.is_not(None),
                )
            )
        ).scalar_one_or_none()
        candidates = [value for value in (account_earliest, key_earliest) if value is not None]
        earliest = min(candidates) if candidates else None
        if earliest is None:
            # Nothing to fold and nothing to skip past; leave the watermark so
            # backdated inserts (if any ever appear) still fold later.
            return _FoldStatus.DONE, False
        if earliest > start:
            start = earliest - timedelta(seconds=1)
        if start >= target:
            return _FoldStatus.DONE, False

        async def _window_aggregates(window_start: datetime, window_end: datetime):
            account_rows = (
                await session.execute(
                    deduped_usage_aggregate_stmt(after_exclusive=window_start, until_inclusive=window_end)
                )
            ).all()
            key_rows = (
                await session.execute(
                    api_key_usage_aggregate_stmt(after_exclusive=window_start, until_inclusive=window_end)
                )
            ).all()
            return account_rows, key_rows

        slice_end = min(start + FOLD_SLICE, target)
        rows, key_rows = await _window_aggregates(start, slice_end)
        # Skip forward over empty stretches of history within this transaction.
        # Both aggregates must be empty: a window may hold only soft-deleted
        # rows, which the API-key sums still count.
        while not rows and not key_rows and slice_end < target:
            start = slice_end
            slice_end = min(start + FOLD_SLICE, target)
            rows, key_rows = await _window_aggregates(start, slice_end)

        candidate_ids = [account_id for (account_id, *_rest) in rows if account_id]
        existing_ids: set[str] = set()
        if candidate_ids:
            # An account deleted after the aggregate would fail the rollup FK;
            # re-check existence in this transaction. A delete committing in
            # the remaining window still aborts this slice, which is safe: the
            # watermark has not advanced, and the retry re-aggregates without
            # the (now account_id=NULL or removed) rows.
            existing_ids = set(
                (await session.execute(select(Account.id).where(Account.id.in_(candidate_ids)))).scalars().all()
            )
        for account_id, request_count, input_tokens, output_tokens, cached_input_tokens, total_cost_usd in rows:
            if not account_id or account_id not in existing_ids:
                continue
            sums = UsageRollupSums(
                request_count=int(request_count or 0),
                input_tokens=int(input_tokens or 0),
                output_tokens=int(output_tokens or 0),
                cached_input_tokens=int(cached_input_tokens or 0),
                total_cost_usd=float(total_cost_usd or 0.0),
            )
            await session.execute(_add_sums_stmt(session, account_id, sums))

        candidate_key_ids = [key_id for (key_id, *_rest) in key_rows if key_id]
        existing_key_ids: set[str] = set()
        if candidate_key_ids:
            existing_key_ids = set(
                (await session.execute(select(ApiKey.id).where(ApiKey.id.in_(candidate_key_ids)))).scalars().all()
            )
        for key_id, request_count, input_tokens, output_tokens, cached_input_tokens, total_cost_usd in key_rows:
            if not key_id or key_id not in existing_key_ids:
                continue
            sums = UsageRollupSums(
                request_count=int(request_count or 0),
                input_tokens=int(input_tokens or 0),
                output_tokens=int(output_tokens or 0),
                cached_input_tokens=int(cached_input_tokens or 0),
                total_cost_usd=float(total_cost_usd or 0.0),
            )
            await session.execute(_add_rollup_sums_stmt(session, ApiKeyUsageRollup, "api_key_id", key_id, sums))
        await session.execute(
            update(AccountUsageRollupState)
            .where(AccountUsageRollupState.id == _STATE_ROW_ID)
            .values(folded_through=slice_end)
        )
        await session.commit()
        logger.info("Folded account usage rollups through %s", slice_end.isoformat())
        return (_FoldStatus.DONE if slice_end >= target else _FoldStatus.CONTINUE), True

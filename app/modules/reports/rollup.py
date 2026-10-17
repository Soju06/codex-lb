"""Report history: paced folds and account rewrites under the shared state lock."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import case, delete, func, insert, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.usage.logs import CANCELLED_STATUS, NON_ERROR_STATUSES
from app.core.utils.time import utcnow
from app.db.models import AccountUsageRollupState, RequestLog, RequestReportHourlyRollup
from app.db.session import get_background_session, sqlite_writer_section
from app.modules.accounts.usage_rollup import FOLD_LAG, _insert_fn, _locked_state, _state_bootstrap_stmt
from app.modules.accounts.usage_time_rollup import (
    _dimension_expr,
    _requested_at_epoch_bucket_expr,
    conversation_id_expr,
    epoch_seconds,
    floor_to_hour,
    to_dimension,
)
from app.modules.reports.filters import _normal_traffic_clause

logger = logging.getLogger(__name__)
REPORT_FOLD_SLICE = timedelta(hours=6)
REPORT_MAX_SLICES = 8
KEYS = ("bucket_epoch", "account_id", "api_key_id", "model", "useragent_group", "conversation_id")
MEASURES = (
    "request_count",
    "error_count",
    "cancelled_count",
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "reasoning_usage_known_requests",
    "cached_input_tokens",
    "cost_usd",
)


def raw_measures() -> list:
    return [
        func.count().label("request_count"),
        func.sum(case((RequestLog.status.not_in(NON_ERROR_STATUSES), 1), else_=0)).label("error_count"),
        func.sum(case((RequestLog.status == CANCELLED_STATUS, 1), else_=0)).label("cancelled_count"),
        func.coalesce(func.sum(RequestLog.input_tokens), 0).label("input_tokens"),
        func.sum(func.coalesce(RequestLog.output_tokens, RequestLog.reasoning_tokens, 0)).label("output_tokens"),
        func.coalesce(func.sum(RequestLog.reasoning_tokens), 0).label("reasoning_tokens"),
        func.count(RequestLog.reasoning_tokens).label("reasoning_usage_known_requests"),
        func.coalesce(func.sum(RequestLog.cached_input_tokens), 0).label("cached_input_tokens"),
        func.coalesce(func.sum(RequestLog.cost_usd), 0.0).label("cost_usd"),
    ]


def report_fold_insert(session: AsyncSession, start: datetime, end: datetime):
    dimensions = [
        _requested_at_epoch_bucket_expr(session, 3600),
        _dimension_expr(RequestLog.account_id),
        _dimension_expr(RequestLog.api_key_id),
        RequestLog.model,
        _dimension_expr(RequestLog.useragent_group),
        _dimension_expr(conversation_id_expr()),
    ]
    stmt = (
        select(*dimensions, func.min(RequestLog.requested_at), *raw_measures())
        .where(
            RequestLog.requested_at >= start,
            RequestLog.requested_at < end,
            _normal_traffic_clause(),
        )
        .group_by(*dimensions)
    )
    return insert(RequestReportHourlyRollup).from_select([*KEYS, "first_requested_at", *MEASURES], stmt)


async def fold_next_report_slice(session: AsyncSession, target: datetime) -> bool:
    """Commit one bounded slice. A failed insert rolls back with its watermark."""
    async with sqlite_writer_section():
        state = await _locked_state(session)
        if state is None:
            await session.execute(_state_bootstrap_stmt(session))
            await session.commit()
            state = await _locked_state(session)
        if state is None or state.reports_folded_through >= target:
            return False
        watermark = state.reports_folded_through
        next_at = (
            await session.execute(
                select(func.min(RequestLog.requested_at)).where(
                    RequestLog.requested_at >= watermark,
                    RequestLog.requested_at < target,
                )
            )
        ).scalar_one_or_none()
        end = target
        if next_at is not None:
            start = max(watermark, floor_to_hour(next_at))
            end = min(start + REPORT_FOLD_SLICE, target)
            await session.execute(
                delete(RequestReportHourlyRollup).where(
                    RequestReportHourlyRollup.bucket_epoch >= epoch_seconds(start),
                    RequestReportHourlyRollup.bucket_epoch < epoch_seconds(end),
                )
            )
            await session.execute(report_fold_insert(session, start, end))
        await session.execute(
            update(AccountUsageRollupState).where(AccountUsageRollupState.id == 1).values(reports_folded_through=end)
        )
        await session.commit()
        logger.info("Folded report history through %s", end.isoformat())
        return True


async def run_report_fold_pass(*, now: datetime | None = None) -> int:
    target = floor_to_hour((now or utcnow()) - FOLD_LAG)
    committed = 0
    for _ in range(REPORT_MAX_SLICES):
        async with get_background_session() as session:
            if not await fold_next_report_slice(session, target):
                break
            committed += 1
    return committed


async def rekey_report_accounts(session: AsyncSession, account_ids: list[str], target_id: str | None) -> None:
    """Merge-add in SQL, including first activity; caller owns the fold lock.

    Reports include deleted logs, so soft deletion only detaches account_id.
    """
    table = RequestReportHourlyRollup
    source = table.account_id.in_([to_dimension(value) for value in account_ids])
    target = to_dimension(target_id)
    # Aggregate collisions among multiple source accounts before ON CONFLICT.
    keys = [getattr(table, name) for name in KEYS if name != "account_id"]
    columns = [literal(target) if name == "account_id" else getattr(table, name) for name in KEYS]
    stmt = (
        select(*columns, func.min(table.first_requested_at), *(func.sum(getattr(table, name)) for name in MEASURES))
        .where(source)
        .group_by(*keys)
    )
    upsert = _insert_fn(session)(table).from_select([*KEYS, "first_requested_at", *MEASURES], stmt)
    updates = {name: getattr(table, name) + getattr(upsert.excluded, name) for name in MEASURES}
    updates["first_requested_at"] = case(
        (table.first_requested_at < upsert.excluded.first_requested_at, table.first_requested_at),
        else_=upsert.excluded.first_requested_at,
    )
    await session.execute(upsert.on_conflict_do_update(index_elements=list(KEYS), set_=updates))
    await session.execute(delete(table).where(source))

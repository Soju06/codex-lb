from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select

from app.core.config.settings import get_settings
from app.core.utils.time import utcnow
from app.db.models import AccountUsageRollupState, AdditionalUsageHistory, RequestLog, UsageHistory
from app.db.session import get_background_session, sqlite_writer_section
from app.modules.usage.repository import _clear_bulk_history_since_sqlite_cache

logger = logging.getLogger(__name__)

# Rows deleted per transaction; a large backlog drains across many short
# transactions instead of holding one long one.
BATCH_SIZE = 10_000


async def run_retention_pass(*, now: datetime | None = None) -> dict[str, int]:
    """Prune aged rows per the retention settings. Returns rows deleted per table."""
    settings = get_settings()
    now = now or utcnow()
    deleted = {"request_logs": 0, "usage_history": 0, "additional_usage_history": 0}
    if settings.request_log_retention_days:
        cutoff = now - timedelta(days=settings.request_log_retention_days)
        deleted["request_logs"] = await _prune_request_logs(cutoff)
    if settings.usage_history_retention_days:
        cutoff = now - timedelta(days=settings.usage_history_retention_days)
        deleted["usage_history"] = await _prune_usage_history(cutoff)
        deleted["additional_usage_history"] = await _prune_additional_usage_history(cutoff)
    total = sum(deleted.values())
    if total:
        logger.info(
            "Retention pruned rows request_logs=%s usage_history=%s additional_usage_history=%s",
            deleted["request_logs"],
            deleted["usage_history"],
            deleted["additional_usage_history"],
        )
    return deleted


async def _prune_request_logs(cutoff: datetime) -> int:
    """Delete folded request-log rows older than the cutoff.

    Rows above the rollup watermark are never deleted: their contribution
    exists only in the live table, so pruning them would silently shrink
    lifetime account totals. No watermark (fold never ran) means skip.
    """
    total = 0
    while True:
        async with get_background_session() as session:
            async with sqlite_writer_section():
                watermark = (
                    await session.execute(
                        select(AccountUsageRollupState.folded_through).where(AccountUsageRollupState.id == 1)
                    )
                ).scalar_one_or_none()
                if watermark is None:
                    if total == 0:
                        logger.info("Retention: skipping request_logs pruning (no rollup watermark yet)")
                    return total
                effective_cutoff = min(cutoff, watermark)
                batch_ids = (
                    select(RequestLog.id).where(RequestLog.requested_at < effective_cutoff).limit(BATCH_SIZE)
                ).scalar_subquery()
                result = await session.execute(
                    delete(RequestLog).where(RequestLog.id.in_(batch_ids)).returning(RequestLog.id)
                )
                await session.commit()
        deleted = len(result.scalars().all())
        total += deleted
        if deleted < BATCH_SIZE:
            return total


async def _prune_usage_history(cutoff: datetime) -> int:
    # Materialize the protected max-id set once per pass instead of embedding
    # the GROUP BY subquery in every batch statement (which would rescan the
    # whole table per 10k batch, under the SQLite writer lock). New rows
    # arriving mid-pass are newer than the cutoff and survive on age; their
    # identity's previously-latest row also surviving is merely conservative.
    protected_stmt = select(func.max(UsageHistory.id)).group_by(
        UsageHistory.account_id, func.coalesce(UsageHistory.window, "primary")
    )
    deleted = await _batched_prune(
        UsageHistory,
        cutoff_condition=UsageHistory.recorded_at < cutoff,
        protected_stmt=protected_stmt,
    )
    if deleted:
        _clear_bulk_history_since_sqlite_cache()
    return deleted


async def _prune_additional_usage_history(cutoff: datetime) -> int:
    protected_stmt = select(func.max(AdditionalUsageHistory.id)).group_by(
        AdditionalUsageHistory.account_id,
        AdditionalUsageHistory.quota_key,
        AdditionalUsageHistory.window,
    )
    return await _batched_prune(
        AdditionalUsageHistory,
        cutoff_condition=AdditionalUsageHistory.recorded_at < cutoff,
        protected_stmt=protected_stmt,
    )


async def _batched_prune(model, *, cutoff_condition, protected_stmt) -> int:
    async with get_background_session() as session:
        protected_ids = list((await session.execute(protected_stmt)).scalars().all())

    total = 0
    while True:
        async with get_background_session() as session:
            async with sqlite_writer_section():
                conditions = [cutoff_condition]
                if protected_ids:
                    conditions.append(model.id.not_in(protected_ids))
                batch_ids = select(model.id).where(*conditions).limit(BATCH_SIZE).scalar_subquery()
                result = await session.execute(delete(model).where(model.id.in_(batch_ids)).returning(model.id))
                await session.commit()
        deleted = len(result.scalars().all())
        total += deleted
        if deleted < BATCH_SIZE:
            return total

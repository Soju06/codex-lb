from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select

from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.utils.time import utcnow
from app.db.models import AccountUsageRollupState, AdditionalUsageHistory, RequestLog, UsageHistory
from app.db.session import get_background_session, sqlite_writer_section
from app.modules.accounts.usage_rollup import FOLD_LAG
from app.modules.usage.repository import _clear_bulk_history_since_sqlite_cache

logger = logging.getLogger(__name__)

# Rows deleted per transaction; a large backlog drains across many short
# transactions instead of holding one long one.
BATCH_SIZE = 10_000


@dataclass(frozen=True, slots=True)
class EffectiveRetention:
    request_log_days: int
    usage_history_days: int

    @property
    def enabled(self) -> bool:
        return bool(self.request_log_days or self.usage_history_days)


async def get_effective_retention() -> EffectiveRetention:
    """Resolve the retention windows with dashboard-first precedence.

    A non-NULL dashboard value (SettingsCache-backed, so a dashboard change
    takes effect without restart) wins; while the dashboard value is unset the
    deprecated env alias applies; 0 means disabled at either layer.
    """
    env = get_settings()
    dashboard = await get_settings_cache().get()
    return EffectiveRetention(
        request_log_days=(
            env.request_log_retention_days
            if dashboard.request_log_retention_days is None
            else dashboard.request_log_retention_days
        ),
        usage_history_days=(
            env.usage_history_retention_days
            if dashboard.usage_history_retention_days is None
            else dashboard.usage_history_retention_days
        ),
    )


async def run_retention_pass(*, now: datetime | None = None) -> dict[str, int]:
    """Prune aged rows per the effective retention settings. Returns rows deleted per table."""
    retention = await get_effective_retention()
    now = now or utcnow()
    deleted = {"request_logs": 0, "usage_history": 0, "additional_usage_history": 0}
    if retention.request_log_days:
        cutoff = now - timedelta(days=retention.request_log_days)
        deleted["request_logs"] = await _prune_request_logs(cutoff, now=now)
    if retention.usage_history_days:
        cutoff = now - timedelta(days=retention.usage_history_days)
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


async def _prune_request_logs(cutoff: datetime, *, now: datetime) -> int:
    """Delete folded request-log rows older than the cutoff.

    Rows above the rollup watermark are never deleted: their contribution
    exists only in the live table, so pruning them would silently shrink
    lifetime account totals. No watermark (fold never ran) means skip.

    Pruning also requires the fold to be CURRENT (watermark within two fold
    lags of now) and stays a full fold lag below it. Summary reads load the
    watermark and the live tail in separate statements; a fold committing
    between them can advance the watermark, and deleting rows from that
    just-folded window would make the reader's tail miss rows its (older)
    folded sums never contained. A reader's loaded watermark is at most one
    steady-state fold advance behind the current one - far less than the
    fold lag - so rows a lag below a current watermark are safe; while the
    fold is catching up (initial backfill, stalled scheduler), skip
    entirely.
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
                if watermark < now - 2 * FOLD_LAG:
                    if total == 0:
                        logger.info(
                            "Retention: skipping request_logs pruning (fold watermark %s not current)",
                            watermark.isoformat(),
                        )
                    return total
                effective_cutoff = min(cutoff, watermark - FOLD_LAG)
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
    # Materialize the protected id set once per pass instead of embedding
    # the GROUP BY subquery in every batch statement (which would rescan the
    # whole table per 10k batch, under the SQLite writer lock). New rows
    # arriving mid-pass are newer than the cutoff and survive on age; their
    # identity's previously-latest row also surviving is merely conservative.
    #
    # "Latest" follows the product's ordering (recorded_at first, not id:
    # latest_by_account orders by recorded_at desc), so out-of-chronology
    # inserts cannot lose the last-known sample; every row tied at the
    # identity's max recorded_at is protected, a safe superset of any
    # tie-break the readers apply.
    window_expr = func.coalesce(UsageHistory.window, "primary")
    latest_ts = (
        select(
            UsageHistory.account_id.label("aid"),
            window_expr.label("win"),
            func.max(UsageHistory.recorded_at).label("ts"),
        )
        .group_by(UsageHistory.account_id, window_expr)
        .subquery("latest_ts")
    )
    protected_stmt = select(UsageHistory.id).join(
        latest_ts,
        (UsageHistory.account_id == latest_ts.c.aid)
        & (window_expr == latest_ts.c.win)
        & (UsageHistory.recorded_at == latest_ts.c.ts),
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
    latest_ts = (
        select(
            AdditionalUsageHistory.account_id.label("aid"),
            AdditionalUsageHistory.quota_key.label("qk"),
            AdditionalUsageHistory.window.label("win"),
            func.max(AdditionalUsageHistory.recorded_at).label("ts"),
        )
        .group_by(
            AdditionalUsageHistory.account_id,
            AdditionalUsageHistory.quota_key,
            AdditionalUsageHistory.window,
        )
        .subquery("latest_additional_ts")
    )
    protected_stmt = select(AdditionalUsageHistory.id).join(
        latest_ts,
        (AdditionalUsageHistory.account_id == latest_ts.c.aid)
        & (AdditionalUsageHistory.quota_key == latest_ts.c.qk)
        & (AdditionalUsageHistory.window == latest_ts.c.win)
        & (AdditionalUsageHistory.recorded_at == latest_ts.c.ts),
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

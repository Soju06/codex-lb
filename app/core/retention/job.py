from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select

from app.core.config.settings_cache import get_settings_cache
from app.core.metrics.prometheus import model_source_live_pins
from app.core.utils.time import utcnow
from app.db.models import AccountUsageRollupState, AdditionalUsageHistory, RequestLog, UsageHistory
from app.db.session import get_background_session, sqlite_writer_section
from app.modules.accounts.usage_rollup import FOLD_LAG
from app.modules.proxy.model_source_pins import (
    PIN_KIND_ANCHOR,
    PIN_KIND_BOUNCE,
    PIN_KIND_THREAD,
    ModelSourcePinRepository,
    drain_deadline_from_settings,
)
from app.modules.usage.repository import _clear_bulk_history_since_sqlite_cache

logger = logging.getLogger(__name__)

# Rows deleted per transaction; a large backlog drains across many short
# transactions instead of holding one long one.
BATCH_SIZE = 10_000

# Every ``kind`` label of ``codex_lb_model_source_live_pins`` is published on each
# sample (``0`` when absent) so a kind that emptied never lingers at its last value.
LIVE_PIN_KINDS: tuple[str, ...] = (PIN_KIND_THREAD, PIN_KIND_ANCHOR, PIN_KIND_BOUNCE)


@dataclass(frozen=True, slots=True)
class EffectiveRetention:
    request_log_days: int
    usage_history_days: int

    @property
    def enabled(self) -> bool:
        return bool(self.request_log_days or self.usage_history_days)


async def get_effective_retention() -> EffectiveRetention:
    """Resolve the retention windows from the dashboard runtime settings.

    The dashboard value (SettingsCache-backed, so a change takes effect
    without restart) is the only source: NULL means never configured, which
    is disabled, and 0 means explicitly disabled.
    """
    dashboard = await get_settings_cache().get()
    return EffectiveRetention(
        request_log_days=dashboard.request_log_retention_days or 0,
        usage_history_days=dashboard.usage_history_retention_days or 0,
    )


async def run_retention_pass(*, now: datetime | None = None) -> dict[str, int]:
    """Prune aged rows per the effective retention settings. Returns rows deleted per table.

    The request-log and usage-history windows are opt-in; the model-source pin
    purge is not (every pin row carries its own ``purge_at``, design §8.8), so
    it runs on every pass whatever the windows say. The live-pin gauge is
    sampled right after the purge on the same (leader-gated, hourly) tick.
    """
    retention = await get_effective_retention()
    now = now or utcnow()
    deleted = {"request_logs": 0, "usage_history": 0, "additional_usage_history": 0, "model_source_pins": 0}
    if retention.request_log_days:
        cutoff = now - timedelta(days=retention.request_log_days)
        deleted["request_logs"] = await _prune_request_logs(cutoff, now=now)
    if retention.usage_history_days:
        cutoff = now - timedelta(days=retention.usage_history_days)
        deleted["usage_history"] = await _prune_usage_history(cutoff)
        deleted["additional_usage_history"] = await _prune_additional_usage_history(cutoff)
    deleted["model_source_pins"] = await prune_model_source_pins()
    await sample_model_source_live_pins(now=now)
    total = sum(deleted.values())
    if total:
        logger.info(
            "Retention pruned rows request_logs=%s usage_history=%s additional_usage_history=%s model_source_pins=%s",
            deleted["request_logs"],
            deleted["usage_history"],
            deleted["additional_usage_history"],
            deleted["model_source_pins"],
        )
    return deleted


async def prune_model_source_pins(*, batch_size: int = BATCH_SIZE) -> int:
    """Delete model-source pin rows whose ``purge_at`` has passed on the database clock.

    ``pin_key``-keyed batches, one short transaction each under the SQLite
    writer section, until a batch comes back short. Independent of the
    retention opt-in: a purged pin answers no lookup any more (its tombstone
    grace is over), so keeping the row buys nothing. Afterwards the drain
    invariant is checked: while a drain deadline is armed every row must have
    ``purge_at < drain_until`` (the drain cap), so a later ``purge_at`` is a bug
    worth a WARN, never something to repair here.
    """
    total = 0
    while True:
        async with get_background_session() as session:
            async with sqlite_writer_section():
                deleted = await ModelSourcePinRepository(session).prune_purged(batch_size=batch_size)
                await session.commit()
        total += deleted
        if deleted < batch_size:
            break
    await _warn_on_model_source_pin_drain_invariant()
    return total


async def sample_model_source_live_pins(*, now: datetime) -> dict[str, int]:
    """Publish ``codex_lb_model_source_live_pins{kind}`` from one ``COUNT ... GROUP BY kind``.

    Rides the hourly retention tick instead of a dedicated task (owner decision
    2026-09-09): the pass already runs leader-gated once an hour, so the gauge
    is an hourly sample scraped from whichever replica holds the lease. Every
    kind in ``LIVE_PIN_KINDS`` is published; a failure is logged and never
    fails the pass. Returns the sample (empty on failure).
    """
    try:
        async with get_background_session() as session:
            counts = await ModelSourcePinRepository(session).count_live_by_kind(now=now)
    except Exception:
        logger.exception("Retention: model-source live-pin sample failed")
        return {}
    sample = {kind: counts.get(kind, 0) for kind in LIVE_PIN_KINDS}
    if model_source_live_pins is not None:
        for kind, count in sample.items():
            model_source_live_pins.labels(kind=kind).set(count)
    return sample


async def _warn_on_model_source_pin_drain_invariant() -> None:
    try:
        drain_until = drain_deadline_from_settings(await get_settings_cache().get())
        if drain_until is None:
            return
        async with get_background_session() as session:
            latest_purge_at = await ModelSourcePinRepository(session).max_purge_at()
    except Exception:
        logger.exception("Retention: model-source pin drain invariant check failed")
        return
    if latest_purge_at is not None and latest_purge_at >= drain_until:
        logger.warning(
            "model_source_pins_drain_invariant_violated max_purge_at=%s drain_until=%s",
            latest_purge_at.isoformat(),
            drain_until.isoformat(),
        )


async def _prune_request_logs(cutoff: datetime, *, now: datetime) -> int:
    """Delete folded request-log rows older than the cutoff.

    Rows above the rollup watermark are never deleted: their contribution
    exists only in the live table, so pruning them would silently shrink
    lifetime account totals. No watermark (fold never ran) means skip.

    The effective watermark is the MIN of the lifetime fold watermark and the
    hourly, conversation and report time-axis watermarks: a raw row is only prunable
    once EVERY rollup that must outlive it has folded it. While any
    time-axis backfill is catching up (each watermark starts at the epoch),
    the min fails the currency check below and pruning pauses entirely — the
    pre-existing "never delete what is not folded" invariant extended to the
    time-axis rollups.

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
                # FOR UPDATE: the batch's prune decision must serialize with
                # anything mutating the fold state in another transaction —
                # in particular the operator escape hatch (rollup truncate +
                # watermark reset). An unlocked read could capture the old
                # watermark, the reset could commit, and this batch would
                # then prune raw rows whose folded statistics were just
                # truncated — recoverable from neither side. With the row
                # lock held through the batch, the reset either commits
                # first (this read sees the epoch watermark and pruning
                # pauses) or waits until the batch's delete has committed.
                watermarks = (
                    await session.execute(
                        select(
                            AccountUsageRollupState.folded_through,
                            AccountUsageRollupState.hourly_folded_through,
                            AccountUsageRollupState.conversation_folded_through,
                            AccountUsageRollupState.reports_folded_through,
                        )
                        .where(AccountUsageRollupState.id == 1)
                        .with_for_update()
                    )
                ).first()
                watermark = min(watermarks) if watermarks is not None else None
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
                # Oldest-first batches keep min(requested_at) a contiguous
                # retention frontier: an interrupted pass never leaves
                # deleted holes above the oldest surviving row, which the
                # rollup-served listing count relies on when clamping its
                # folded window to listable history.
                batch_ids = (
                    select(RequestLog.id)
                    .where(RequestLog.requested_at < effective_cutoff)
                    .order_by(RequestLog.requested_at.asc(), RequestLog.id.asc())
                    .limit(BATCH_SIZE)
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

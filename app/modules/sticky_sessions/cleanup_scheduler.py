from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import startup as startup_module
from app.core.config.settings import get_settings
from app.core.config.spool_retention import (
    bridge_session_reuse_window_seconds,
    resolve_operation_spool_retention_seconds,
)
from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    http_bridge_spool_cleanup_backlog_likely,
    http_bridge_spool_cleanup_deleted_operations_total,
    http_bridge_spool_cleanup_duration_seconds,
    http_bridge_spool_cleanup_runs_total,
)
from app.core.rate_limiter.db_rate_limiter import get_rate_limit_attempt_sweeper
from app.core.scheduling.leader_election_handle import get_leader_election as _get_leader_election
from app.core.utils.time import utcnow
from app.db.models import DashboardSettings
from app.db.session import SessionLocal, get_background_session
from app.modules.proxy.durable_bridge_repository import (
    DURABLE_BRIDGE_OPERATION_SPOOL_PURGE_BATCH_SIZE,
    DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL_SECONDS,
    DurableBridgeRepository,
    missing_durable_bridge_tables,
)
from app.modules.proxy.ring_membership import RING_MEMBER_RETENTION_SECONDS, RingMembershipService
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.settings.repository import SettingsRepository

logger = logging.getLogger(__name__)

# Cleanup poll cadence (fixed; issue #1340 / PRINCIPLES.md P2). The scheduler
# keeps ``interval_seconds`` as a constructor field so tests can exercise the
# loop with a short interval.
_CLEANUP_INTERVAL_SECONDS = 300

# A hard codex_session mapping is never rebound while its owner is merely
# rate-limited/quota-exceeded/paused (see load_balancer.py's hard_sticky
# branch and openspec/specs/sticky-session-operations/spec.md). This is
# deliberately far longer than any ordinary quota-reset window (typically
# minutes to a few hours) so a transient blip never loses its mapping; only
# an owner stuck unavailable well past when it should have recovered gets
# its mapping dropped (never rebound) so the next request re-resolves fresh.
_STALE_HARD_CODEX_SESSION_UNAVAILABLE_SECONDS = 6 * 3600

# Transcript cleanup shares SQLite with request traffic and leader renewal.
# Keep each pass large enough to outpace steady-state expiry, but small enough
# that a historical backlog is resumed across scheduler ticks instead of
# monopolizing the database in one drain-all loop.
# No key-prefix sweep of the `sticky_thread` kind exists, deliberately.
#
# A prompt-cache key derived by the proxy -- the retired content-hash shape
# (`{model_class}-{api_key_id[:12]}-{hash}`) and the current thread-anchored
# `v2t-` shape alike -- is only ever produced when `openai_cache_affinity` is
# enabled, and that is exactly the branch that classifies the mapping as
# PROMPT_CACHE (see `_sticky_key_for_responses_request` /
# `_sticky_key_for_compact_request`: the STICKY_THREAD branch is `elif
# sticky_threads_enabled`, reachable only with cache affinity off, where the
# derivation supplies no sticky key at all). So a derived key can never be
# written as a `sticky_thread` row, and `purge_prompt_cache_before` already
# retires every derived row of either shape at the freshness window.
#
# A `sticky_thread` row whose key starts with `std-`/`codex-`/`mini-` is
# therefore necessarily *client-supplied*, and `sticky_thread` has no TTL by
# design. Deleting those rows by key prefix silently drops a client's soft
# locality because its key text happens to look like a retired proxy shape.
_OPERATION_RETENTION_BATCH_SIZE = DURABLE_BRIDGE_OPERATION_SPOOL_PURGE_BATCH_SIZE
_OPERATION_RETENTION_MAX_BATCHES = 4
_OPERATION_RETENTION_TIME_BUDGET_SECONDS = 5.0
_OPERATION_RETENTION_BACKLOG_RETRY_SECONDS = 5.0


OperationRetentionOutcome = Literal[
    "completed",
    "batch_budget_exhausted",
    "time_budget_exhausted",
    "failed",
]


@dataclass(frozen=True, slots=True)
class OperationRetentionCleanupResult:
    deleted_operations: int
    batches: int
    backlog_likely: bool
    outcome: OperationRetentionOutcome
    duration_seconds: float


class OperationRetentionCleanupError(RuntimeError):
    def __init__(self, result: OperationRetentionCleanupResult, *, error_type: str) -> None:
        super().__init__("durable operation retention batch failed")
        self.result = result
        self.error_type = error_type


class OperationRetentionCleanupCancelledError(asyncio.CancelledError):
    def __init__(self, result: OperationRetentionCleanupResult) -> None:
        super().__init__("durable operation retention batch cancelled")
        self.result = result


async def _purge_operation_spool_with_budget(
    bridge_repo: DurableBridgeRepository,
    *,
    cutoff: datetime,
) -> OperationRetentionCleanupResult:
    started_at = time.monotonic()
    deleted_operations = 0
    batches = 0
    outcome: OperationRetentionOutcome = "completed"

    try:
        while True:
            batch_result = await bridge_repo.purge_operation_spool_batch(
                cutoff=cutoff,
                batch_size=_OPERATION_RETENTION_BATCH_SIZE,
            )
            deleted_operations += batch_result.deleted_operations
            batches += 1
            if batch_result.selected_operations < _OPERATION_RETENTION_BATCH_SIZE:
                break
            if time.monotonic() - started_at >= _OPERATION_RETENTION_TIME_BUDGET_SECONDS:
                outcome = "time_budget_exhausted"
                break
            if batches >= _OPERATION_RETENTION_MAX_BATCHES:
                outcome = "batch_budget_exhausted"
                break
            await asyncio.sleep(0)
    except asyncio.CancelledError:
        raise OperationRetentionCleanupCancelledError(
            OperationRetentionCleanupResult(
                deleted_operations=deleted_operations,
                batches=batches,
                backlog_likely=True,
                outcome="failed",
                duration_seconds=max(time.monotonic() - started_at, 0.0),
            )
        ) from None
    except Exception as exc:
        raise OperationRetentionCleanupError(
            OperationRetentionCleanupResult(
                deleted_operations=deleted_operations,
                batches=batches,
                backlog_likely=True,
                outcome="failed",
                duration_seconds=max(time.monotonic() - started_at, 0.0),
            ),
            error_type=type(exc).__name__,
        ) from None

    return OperationRetentionCleanupResult(
        deleted_operations=deleted_operations,
        batches=batches,
        backlog_likely=outcome != "completed",
        outcome=outcome,
        duration_seconds=max(time.monotonic() - started_at, 0.0),
    )


def _record_operation_retention_cleanup(result: OperationRetentionCleanupResult) -> None:
    if not PROMETHEUS_AVAILABLE:
        return
    assert http_bridge_spool_cleanup_runs_total is not None
    assert http_bridge_spool_cleanup_deleted_operations_total is not None
    assert http_bridge_spool_cleanup_duration_seconds is not None
    assert http_bridge_spool_cleanup_backlog_likely is not None
    http_bridge_spool_cleanup_runs_total.labels(outcome=result.outcome).inc()
    http_bridge_spool_cleanup_deleted_operations_total.inc(result.deleted_operations)
    http_bridge_spool_cleanup_duration_seconds.observe(result.duration_seconds)
    http_bridge_spool_cleanup_backlog_likely.set(1.0 if result.backlog_likely else 0.0)


def operation_retention_metrics_enabled() -> bool:
    return PROMETHEUS_AVAILABLE and bool(getattr(get_settings(), "metrics_enabled", False))


def _next_cleanup_delay_seconds(
    delay_to_full_cleanup: float,
    *,
    backlog_likely: bool,
    retry_immediately: bool,
) -> float:
    if backlog_likely and retry_immediately:
        return 0.0
    if backlog_likely:
        return min(delay_to_full_cleanup, _OPERATION_RETENTION_BACKLOG_RETRY_SECONDS)
    return delay_to_full_cleanup


def _merge_backlog_signal(previous: bool, attempted: bool | None) -> bool:
    return previous if attempted is None else attempted


async def _purge_expired_rate_limit_attempts(session: AsyncSession) -> None:
    """Age out ``rate_limit_attempts`` on every leader pass.

    ``clear_for_key`` only runs on a *successful* sign-in, so failed attempts
    have no other way out of the table, and the failed-login keys now carry a
    caller-supplied username: without this the row count is the attacker's to
    choose. It runs whatever the sticky-mapping toggle says, for the same
    reason the operation retention sweep does, and its failure is contained so
    it can never cost the rest of the pass.
    """

    try:
        await get_rate_limit_attempt_sweeper().cleanup(session)
    except Exception:
        logger.exception("Rate-limit attempt retention failed")
        # The rest of the pass must not inherit a half-finished transaction,
        # and a rollback that fails on an already-broken session says nothing
        # the log above has not already said.
        with contextlib.suppress(Exception):
            await session.rollback()


def _abandoned_bridge_retention_seconds(dashboard_settings: DashboardSettings) -> float:
    """Retention for abandoned durable bridge rows.

    An idle local bridge session stays reusable until its effective idle TTL —
    up to the prompt-cache reuse TTL for prompt-cache sessions — which can
    exceed the prompt-cache affinity max age. Purging the ACTIVE durable row
    earlier would strip a still-reusable session of its durable ownership and
    continuity aliases, so retention must cover the longest reuse window.

    That window is also the first term of the operation spool retention floor,
    so both read it from one definition (``app.core.config.spool_retention``).
    """

    return bridge_session_reuse_window_seconds(dashboard_settings)


@dataclass(slots=True)
class StickySessionCleanupScheduler:
    interval_seconds: int
    enabled: bool
    # Durable bridge transcript retention is a data-safety obligation and must
    # continue even when operators disable sticky-session mapping cleanup.
    operation_retention_enabled: bool = True
    _task: asyncio.Task[None] | None = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _operation_retention_attempt_failed: bool = False
    _operation_retention_cancelled_backlog_likely: bool | None = None

    async def start(self) -> None:
        if not self.enabled and not self.operation_retention_enabled:
            return
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop.set()
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run_loop(self) -> None:
        loop = asyncio.get_running_loop()
        next_full_cleanup_at = loop.time()
        backlog_likely = False
        while not self._stop.is_set():
            retention_attempted: bool | None = None
            if loop.time() >= next_full_cleanup_at:
                retention_attempted = await self._cleanup_once()
                backlog_likely = _merge_backlog_signal(
                    backlog_likely,
                    retention_attempted,
                )
                next_full_cleanup_at = loop.time() + float(self.interval_seconds)
            elif backlog_likely:
                retention_attempted = await self._cleanup_operation_retention_once()
                backlog_likely = _merge_backlog_signal(
                    backlog_likely,
                    retention_attempted,
                )
            delay_to_full_cleanup = max(next_full_cleanup_at - loop.time(), 0.0)
            delay_seconds = _next_cleanup_delay_seconds(
                delay_to_full_cleanup,
                backlog_likely=backlog_likely,
                retry_immediately=retention_attempted is True and not self._operation_retention_attempt_failed,
            )
            try:
                await asyncio.wait_for(
                    self._stop.wait(),
                    timeout=delay_seconds,
                )
            except asyncio.TimeoutError:
                continue

    async def _cleanup_once(self) -> bool | None:
        self._operation_retention_cancelled_backlog_likely = None
        result = await _get_leader_election().run_if_leader(self._cleanup_as_leader)
        return result if result is not None else self._operation_retention_cancelled_backlog_likely

    async def _cleanup_operation_retention_once(self) -> bool | None:
        self._operation_retention_cancelled_backlog_likely = None
        result = await _get_leader_election().run_if_leader(self._cleanup_operation_retention_as_leader)
        return result if result is not None else self._operation_retention_cancelled_backlog_likely

    async def _run_operation_retention(
        self,
        bridge_repo: DurableBridgeRepository,
        dashboard_settings: DashboardSettings,
    ) -> bool | None:
        # R2 spool retention: one resolve per pass from the snapshot this pass
        # already loaded, so the retention window and the reuse windows the
        # floor is derived from can never disagree inside one pass.
        operation_cutoff = utcnow() - timedelta(seconds=resolve_operation_spool_retention_seconds(dashboard_settings))
        retention_started_at = time.monotonic()
        error_type: str | None = None
        cancellation: OperationRetentionCleanupCancelledError | None = None
        try:
            result = await _purge_operation_spool_with_budget(bridge_repo, cutoff=operation_cutoff)
        except OperationRetentionCleanupCancelledError as exc:
            result = exc.result
            error_type = "CancelledError"
            cancellation = exc
        except OperationRetentionCleanupError as exc:
            result = exc.result
            error_type = exc.error_type
        except Exception as exc:
            result = OperationRetentionCleanupResult(
                deleted_operations=0,
                batches=0,
                backlog_likely=True,
                outcome="failed",
                duration_seconds=max(time.monotonic() - retention_started_at, 0.0),
            )
            error_type = type(exc).__name__
        _record_operation_retention_cleanup(result)
        if not operation_retention_metrics_enabled() or result.deleted_operations > 0 or result.backlog_likely:
            logger.info(
                "HTTP bridge operation transcript retention "
                "deleted_operations=%s batches=%s outcome=%s "
                "backlog_likely=%s duration_seconds=%.3f error_type=%s",
                result.deleted_operations,
                result.batches,
                result.outcome,
                result.backlog_likely,
                result.duration_seconds,
                error_type or "none",
            )
        self._operation_retention_attempt_failed = result.outcome == "failed"
        self._operation_retention_cancelled_backlog_likely = result.backlog_likely
        if cancellation is not None:
            raise cancellation
        return result.backlog_likely

    async def _cleanup_operation_retention_as_leader(self) -> bool | None:
        async with self._lock:
            started_at = time.monotonic()
            try:
                async with get_background_session() as session:
                    if not startup_module._bridge_durable_schema_ready and await missing_durable_bridge_tables(session):
                        return False
                    dashboard_settings = await SettingsRepository(session).get_or_create()
                    return await self._run_operation_retention(DurableBridgeRepository(session), dashboard_settings)
            except Exception as exc:
                result = OperationRetentionCleanupResult(
                    deleted_operations=0,
                    batches=0,
                    backlog_likely=True,
                    outcome="failed",
                    duration_seconds=max(time.monotonic() - started_at, 0.0),
                )
                _record_operation_retention_cleanup(result)
                logger.warning(
                    "HTTP bridge operation transcript retention failed before batching "
                    "deleted_operations=0 batches=0 outcome=failed backlog_likely=true "
                    "duration_seconds=%.3f error_type=%s",
                    result.duration_seconds,
                    type(exc).__name__,
                )
                self._operation_retention_attempt_failed = True
                return True

    async def _cleanup_as_leader(self) -> bool | None:
        async with self._lock:
            backlog_likely = False
            retention_attempted = False
            try:
                async with get_background_session() as session:
                    await _purge_expired_rate_limit_attempts(session)
                    settings_repo = SettingsRepository(session)
                    bridge_repo = DurableBridgeRepository(session)
                    sticky_repo = StickySessionsRepository(session)
                    # R2 spool retention: operation retention needs the row
                    # too, so it is loaded for every pass, not only when
                    # sticky-mapping cleanup is enabled.
                    settings = await settings_repo.get_or_create()

                    if self.enabled:
                        cutoff = utcnow() - timedelta(seconds=settings.openai_cache_affinity_max_age_seconds)
                        # Retires every proxy-derived prompt-cache mapping, of
                        # either key shape: see the module note above on why no
                        # `sticky_thread` key-prefix sweep belongs here.
                        deleted_count = await sticky_repo.purge_prompt_cache_before(cutoff)
                        if deleted_count > 0:
                            logger.info("Purged stale prompt-cache sticky sessions deleted_count=%s", deleted_count)
                        cleanup_now = utcnow()
                        stale_hard_codex_session_cutoff = cleanup_now - timedelta(
                            seconds=_STALE_HARD_CODEX_SESSION_UNAVAILABLE_SECONDS
                        )
                        stale_hard_codex_session_deleted_count = (
                            await sticky_repo.purge_stale_hard_codex_session_mappings(
                                stale_hard_codex_session_cutoff, now=cleanup_now
                            )
                        )
                        if stale_hard_codex_session_deleted_count > 0:
                            logger.info(
                                "Purged stale hard codex_session sticky mappings pinned to a durably unavailable "
                                "owner deleted_count=%s",
                                stale_hard_codex_session_deleted_count,
                            )
                    if startup_module._bridge_durable_schema_ready or not await missing_durable_bridge_tables(session):
                        if self.enabled:
                            # Same grace window as the sticky sweep above, and
                            # for the same reason: an owner that has been
                            # unroutable across it is not coming back on its
                            # own, so its threads must be free to rebind.
                            retire_now = utcnow()
                            retired_owner_count = await bridge_repo.retire_stale_unavailable_bridge_owners(
                                retire_now - timedelta(seconds=_STALE_HARD_CODEX_SESSION_UNAVAILABLE_SECONDS),
                                now=retire_now,
                            )
                            if retired_owner_count > 0:
                                logger.info(
                                    "Retired durable HTTP bridge continuity owners that stayed unroutable "
                                    "retired_count=%s",
                                    retired_owner_count,
                                )
                            bridge_deleted_count = await bridge_repo.purge_closed_before(cutoff)
                            if bridge_deleted_count > 0:
                                logger.info("Purged closed HTTP bridge sessions deleted_count=%s", bridge_deleted_count)
                            abandoned_cutoff = utcnow() - timedelta(
                                seconds=_abandoned_bridge_retention_seconds(settings)
                            )
                            abandoned_deleted_count = await bridge_repo.purge_abandoned_before(abandoned_cutoff)
                            if abandoned_deleted_count > 0:
                                logger.info(
                                    "Purged abandoned HTTP bridge sessions deleted_count=%s", abandoned_deleted_count
                                )
                            retry_circuit_deleted_count = await bridge_repo.purge_retry_circuits_before(
                                time.time() - DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL_SECONDS,
                                # Abandonment tombstones guard continuity for
                                # the bridge-retention window, not the circuit
                                # TTL.
                                tombstone_cutoff_epoch=time.time() - _abandoned_bridge_retention_seconds(settings),
                            )
                            if retry_circuit_deleted_count > 0:
                                logger.info(
                                    "Purged expired HTTP bridge retry circuits deleted_count=%s",
                                    retry_circuit_deleted_count,
                                )
                        if self.operation_retention_enabled:
                            retention_attempted = True
                            backlog_likely = await self._run_operation_retention(bridge_repo, settings)
                if self.enabled:
                    ring_cutoff = utcnow() - timedelta(seconds=RING_MEMBER_RETENTION_SECONDS)
                    ring_deleted_count = await RingMembershipService(SessionLocal).purge_stale_before(ring_cutoff)
                    if ring_deleted_count > 0:
                        logger.info("Purged stale bridge ring members deleted_count=%s", ring_deleted_count)
            except Exception:
                logger.exception("Sticky session cleanup loop failed")
                return backlog_likely if retention_attempted else None
            return backlog_likely


def build_sticky_session_cleanup_scheduler() -> StickySessionCleanupScheduler:
    return StickySessionCleanupScheduler(interval_seconds=_CLEANUP_INTERVAL_SECONDS, enabled=True)

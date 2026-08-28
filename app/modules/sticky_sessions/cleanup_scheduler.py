from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal, Protocol, TypeVar, cast

from app.core import startup as startup_module
from app.core.config.settings import Settings, get_settings
from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    http_bridge_spool_cleanup_backlog_likely,
    http_bridge_spool_cleanup_deleted_operations_total,
    http_bridge_spool_cleanup_duration_seconds,
    http_bridge_spool_cleanup_runs_total,
)
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


_T = TypeVar("_T")


class _LeaderElectionLike(Protocol):
    async def run_if_leader(self, fn: Callable[[], Awaitable[_T]]) -> _T | None: ...


def _get_leader_election() -> _LeaderElectionLike:
    module = importlib.import_module("app.core.scheduling.leader_election")
    return cast(_LeaderElectionLike, module.get_leader_election())


def _abandoned_bridge_retention_seconds(
    dashboard_settings: DashboardSettings,
    app_settings: Settings,
) -> float:
    """Retention for abandoned durable bridge rows.

    An idle local bridge session stays reusable until its effective idle TTL —
    up to the prompt-cache reuse TTL for prompt-cache sessions — which can
    exceed the prompt-cache affinity max age. Purging the ACTIVE durable row
    earlier would strip a still-reusable session of its durable ownership and
    continuity aliases, so retention must cover the longest reuse window.
    """

    return max(
        float(dashboard_settings.openai_cache_affinity_max_age_seconds),
        float(dashboard_settings.http_responses_session_bridge_prompt_cache_idle_ttl_seconds),
        float(app_settings.http_responses_session_bridge_idle_ttl_seconds),
        float(app_settings.http_responses_session_bridge_codex_idle_ttl_seconds),
    )


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

    async def _run_operation_retention(self, bridge_repo: DurableBridgeRepository) -> bool | None:
        operation_cutoff = utcnow() - timedelta(
            seconds=get_settings().http_responses_session_bridge_operation_spool_retention_seconds
        )
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
                    return await self._run_operation_retention(DurableBridgeRepository(session))
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
                    settings_repo = SettingsRepository(session)
                    bridge_repo = DurableBridgeRepository(session)
                    sticky_repo = StickySessionsRepository(session)
                    settings = await settings_repo.get_or_create() if self.enabled else None

                    if self.enabled:
                        assert settings is not None
                        cutoff = utcnow() - timedelta(seconds=settings.openai_cache_affinity_max_age_seconds)
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
                            assert settings is not None
                            bridge_deleted_count = await bridge_repo.purge_closed_before(cutoff)
                            if bridge_deleted_count > 0:
                                logger.info("Purged closed HTTP bridge sessions deleted_count=%s", bridge_deleted_count)
                            abandoned_cutoff = utcnow() - timedelta(
                                seconds=_abandoned_bridge_retention_seconds(settings, get_settings())
                            )
                            abandoned_deleted_count = await bridge_repo.purge_abandoned_before(abandoned_cutoff)
                            if abandoned_deleted_count > 0:
                                logger.info(
                                    "Purged abandoned HTTP bridge sessions deleted_count=%s", abandoned_deleted_count
                                )
                            retry_circuit_deleted_count = await bridge_repo.purge_retry_circuits_before(
                                time.time() - DURABLE_BRIDGE_RETRY_CIRCUIT_STATE_TTL_SECONDS
                            )
                            if retry_circuit_deleted_count > 0:
                                logger.info(
                                    "Purged expired HTTP bridge retry circuits deleted_count=%s",
                                    retry_circuit_deleted_count,
                                )
                        if self.operation_retention_enabled:
                            retention_attempted = True
                            backlog_likely = await self._run_operation_retention(bridge_repo)
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
    settings = get_settings()
    return StickySessionCleanupScheduler(
        interval_seconds=_CLEANUP_INTERVAL_SECONDS,
        enabled=settings.sticky_session_cleanup_enabled,
    )

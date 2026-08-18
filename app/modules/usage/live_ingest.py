from __future__ import annotations

import asyncio
import logging
import time
import weakref
from dataclasses import dataclass

from app.core import usage as usage_core
from app.core.config.settings import get_settings
from app.core.usage.live_hub import register_live_usage_publisher
from app.core.usage.live_snapshots import LiveRateLimitSnapshot, LiveUsageWindow
from app.db.session import get_background_session
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.proxy.rate_limit_cache import get_rate_limit_headers_cache
from app.modules.usage.repository import UsageRepository, UsageWindowWrite

logger = logging.getLogger(__name__)

# Write-coalescing tuning (fixed; issue #1340 / PRINCIPLES.md P2). The
# ingestor keeps both as constructor fields so tests can exercise queue
# overflow and coalescing with small values.
_QUEUE_SIZE = 512
_WRITE_MIN_INTERVAL_SECONDS = 5.0
_CACHE_INVALIDATION_MIN_INTERVAL_SECONDS = 5.0

# Ownership accounting for every task any ingestor instance creates (consumer
# and trailing cache invalidation), so a task an owner lost track of (a stop
# cancelled mid-await) can never end in a silently dropped exception:
#
# - `_owned_tasks` holds weak references (they never extend task lifetime) so
#   the test suite's leak fence can cancel pending tasks and settle completed
#   ones that some reference chain kept alive across a test boundary.
# - `_record_owned_task_result` runs as each task's done callback: it
#   retrieves the exception (so the loop's unobserved-task warning can never
#   fire at garbage-collection time), logs it, and records it in the bounded
#   `_owned_task_failures` strong handoff for the fence to drain (#1755).
#
# `_settled_owned_tasks` (also weak) marks tasks whose result was already
# recorded, so the done callback and the fence's sweep of completed tasks
# settle each task exactly once even when both observe it.
_owned_tasks: weakref.WeakSet[asyncio.Task[None]] = weakref.WeakSet()
_settled_owned_tasks: weakref.WeakSet[asyncio.Task[None]] = weakref.WeakSet()
# (task name, exception repr) pairs. Reprs, not exception objects: a stored
# exception's traceback would keep the failed ingestor's whole object graph
# (task, queue, cached state) alive for the process lifetime, since production
# never drains this record.
_owned_task_failures: list[tuple[str, str]] = []
_MAX_OWNED_TASK_FAILURES = 16


def _record_owned_task_result(task: asyncio.Task[None]) -> None:
    if task in _settled_owned_tasks:
        return
    _settled_owned_tasks.add(task)
    _owned_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        return
    logger.error("Live usage ingestor task %r died unexpectedly", task.get_name(), exc_info=exc)
    if len(_owned_task_failures) < _MAX_OWNED_TASK_FAILURES:
        _owned_task_failures.append((task.get_name(), repr(exc)))


def _enroll_owned_task(task: asyncio.Task[None]) -> None:
    _owned_tasks.add(task)
    task.add_done_callback(_record_owned_task_result)


@dataclass(frozen=True, slots=True)
class _QueuedSnapshot:
    account_id: str | None
    chatgpt_account_id: str | None
    snapshot: LiveRateLimitSnapshot


def _fingerprint(snapshot: LiveRateLimitSnapshot) -> tuple[object, ...]:
    def window_key(window: LiveUsageWindow | None) -> tuple[object, ...] | None:
        if window is None:
            return None
        return (round(window.used_percent, 2), window.window_minutes, window.reset_at)

    return (
        window_key(snapshot.primary),
        window_key(snapshot.secondary),
        snapshot.credits_has,
        snapshot.credits_unlimited,
        snapshot.credits_balance,
    )


class LiveUsageIngestor:
    """Fire-and-forget sink for per-turn rate-limit snapshots.

    Snapshots ride the serving path, so enqueueing must stay O(1) and never
    raise; a single consumer task owns its own background sessions and writes
    usage-history rows with the same shape the background poller produces.
    """

    def __init__(
        self,
        *,
        queue_size: int,
        write_min_interval_seconds: float,
    ) -> None:
        self._queue: asyncio.Queue[_QueuedSnapshot] = asyncio.Queue(maxsize=max(1, queue_size))
        self._write_min_interval_seconds = write_min_interval_seconds
        self._last_write: dict[str, tuple[tuple[object, ...], float]] = {}
        self._consumer: asyncio.Task[None] | None = None
        self._dropped = 0
        self._last_cache_invalidation = 0.0
        self._trailing_invalidation: asyncio.Task[None] | None = None

    def publish(
        self,
        snapshot: LiveRateLimitSnapshot,
        *,
        account_id: str | None = None,
        chatgpt_account_id: str | None = None,
    ) -> None:
        item = _QueuedSnapshot(account_id=account_id, chatgpt_account_id=chatgpt_account_id, snapshot=snapshot)
        if account_id is not None and self._should_skip(account_id, snapshot):
            return
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self._dropped += 1
            if self._dropped % 100 == 1:
                logger.warning("Live usage ingest queue full; dropped_total=%d", self._dropped)
            try:
                self._queue.put_nowait(item)
            except asyncio.QueueFull:
                pass

    def start(self) -> None:
        if self._consumer is None or self._consumer.done():
            self._consumer = asyncio.create_task(self._run(), name="live-usage-ingestor")
            _enroll_owned_task(self._consumer)

    def is_running(self) -> bool:
        consumer = self._consumer
        return consumer is not None and not consumer.done()

    async def stop(self) -> None:
        consumer = self._consumer
        self._consumer = None
        trailing = self._trailing_invalidation
        self._trailing_invalidation = None
        for task in (consumer, trailing):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    def _should_skip(self, account_id: str, snapshot: LiveRateLimitSnapshot) -> bool:
        last = self._last_write.get(account_id)
        if last is None:
            return False
        fingerprint, written_at = last
        if fingerprint != _fingerprint(snapshot):
            return False
        return time.monotonic() - written_at < self._write_min_interval_seconds

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                await self._ingest(item)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning(
                    "Live usage ingest failed account_id=%s chatgpt_account_id=%s",
                    item.account_id,
                    item.chatgpt_account_id,
                    exc_info=True,
                )

    async def _ingest(self, item: _QueuedSnapshot) -> None:
        snapshot = item.snapshot
        primary = snapshot.primary
        secondary = snapshot.secondary
        monthly: LiveUsageWindow | None = None
        # Mirror the poller's write-time normalization: a lone primary window
        # with the monthly duration is the monthly-only free-plan shape and
        # belongs in the monthly slot, not the primary one.
        if (
            primary is not None
            and secondary is None
            and primary.window_minutes == usage_core.DEFAULT_WINDOW_MINUTES_MONTHLY
        ):
            monthly, primary = primary, None
        windows: list[UsageWindowWrite] = []
        if primary is not None:
            windows.append(
                UsageWindowWrite(
                    window="primary",
                    used_percent=float(primary.used_percent),
                    reset_at=primary.reset_at,
                    window_minutes=primary.window_minutes,
                    credits_has=snapshot.credits_has,
                    credits_unlimited=snapshot.credits_unlimited,
                    credits_balance=snapshot.credits_balance,
                )
            )
        if secondary is not None:
            # Mirror the poller: credits normally ride the primary row. A
            # secondary-only snapshot must still carry fresh credit state.
            secondary_carries_credits = primary is None
            windows.append(
                UsageWindowWrite(
                    window="secondary",
                    used_percent=float(secondary.used_percent),
                    reset_at=secondary.reset_at,
                    window_minutes=secondary.window_minutes,
                    credits_has=snapshot.credits_has if secondary_carries_credits else None,
                    credits_unlimited=snapshot.credits_unlimited if secondary_carries_credits else None,
                    credits_balance=snapshot.credits_balance if secondary_carries_credits else None,
                )
            )
        if monthly is not None:
            windows.append(
                UsageWindowWrite(
                    window="monthly",
                    used_percent=float(monthly.used_percent),
                    reset_at=monthly.reset_at,
                    window_minutes=monthly.window_minutes,
                    credits_has=snapshot.credits_has,
                    credits_unlimited=snapshot.credits_unlimited,
                    credits_balance=snapshot.credits_balance,
                )
            )

        async with get_background_session() as session:
            account_id = await UsageRepository(session).settle_live_account_snapshot(
                account_id=item.account_id,
                chatgpt_account_id=item.chatgpt_account_id,
                windows=windows,
                should_skip=lambda resolved: self._should_skip(resolved, snapshot),
            )
        if account_id is None:
            return
        self._last_write[account_id] = (_fingerprint(snapshot), time.monotonic())
        await self._invalidate_caches_throttled()

    async def _invalidate_caches_throttled(self) -> None:
        # Invalidations are throttled, but every write must still be covered:
        # a write inside the throttle window schedules one trailing
        # invalidation at window expiry, so cached selection inputs and
        # downstream x-codex-* headers are stale for at most the throttle
        # interval rather than the header cache TTL.
        now = time.monotonic()
        remaining = _CACHE_INVALIDATION_MIN_INTERVAL_SECONDS - (now - self._last_cache_invalidation)
        if remaining <= 0:
            await self._invalidate_caches_now()
            return
        if self._trailing_invalidation is None or self._trailing_invalidation.done():
            self._trailing_invalidation = asyncio.create_task(
                self._trailing_invalidate(remaining),
                name="live-usage-trailing-invalidation",
            )
            _enroll_owned_task(self._trailing_invalidation)

    async def _trailing_invalidate(self, delay_seconds: float) -> None:
        await asyncio.sleep(delay_seconds)
        await self._invalidate_caches_now()

    async def _invalidate_caches_now(self) -> None:
        self._last_cache_invalidation = time.monotonic()
        get_account_selection_cache().invalidate()
        # Downstream x-codex-* headers are served from a TTL cache that only
        # the poller invalidates otherwise; drop it so clients see the live
        # values before the TTL expires.
        await get_rate_limit_headers_cache().invalidate()


_ingestor: LiveUsageIngestor | None = None
# Registrations a nested startup displaced, innermost-last. A stack rather
# than a single prior slot: lifespans can nest more than one level deep (each
# portal-loop ``TestClient`` adds one), and a stack restores each displaced
# outer lifespan in LIFO order while an out-of-order stop simply removes its
# instance from wherever it sits — a single slot would forget everything below
# the most recent displacement.
_displaced_ingestors: list[LiveUsageIngestor] = []


def start_live_usage_ingestor() -> LiveUsageIngestor | None:
    """Create, start, and register a fresh ingestor as the current singleton.

    The caller (the app lifespan) MUST hold the returned instance and pass it
    back to ``stop_live_usage_ingestor`` at shutdown. Two lifespans can be
    live in one process (the test suite nests a portal-loop ``TestClient``
    inside an app already running on the session loop); each owns its own
    instance, and the module global only tracks whichever registered last. A
    started ingestor whose only strong root is the module global would become
    an unreferenced reference cycle (task -> coroutine frame -> ingestor ->
    queue -> getter future -> task) the moment a nested startup overwrites the
    global, and the cyclic GC would then destroy its consumer task mid-await.
    """
    global _ingestor
    settings = get_settings()
    if not getattr(settings, "live_usage_ingestion_enabled", True):
        register_live_usage_publisher(None)
        return None
    ingestor = LiveUsageIngestor(
        queue_size=_QUEUE_SIZE,
        write_min_interval_seconds=_WRITE_MIN_INTERVAL_SECONDS,
    )
    ingestor.start()
    if _ingestor is not None and _ingestor.is_running():
        # A nested startup displaces a still-running outer registration;
        # remember it so the nested shutdown can restore it (a dead instance
        # is never worth remembering).
        _displaced_ingestors.append(_ingestor)
    register_live_usage_publisher(ingestor.publish)
    _ingestor = ingestor
    return ingestor


async def stop_live_usage_ingestor(ingestor: LiveUsageIngestor | None = None) -> None:
    """Stop ``ingestor``, or the current singleton when omitted.

    The module global and the publisher registration are touched only when
    the stopped instance still owns them, so a lifespan shutting down cannot
    orphan or unregister a nested lifespan's newer instance — and a nested
    lifespan's shutdown cannot leave the outer instance dangling with no
    stop path (the leak behind issue #1755's cross-test poisoning). When the
    stopped instance is the current registration, the most recent displaced
    ingestor that is still running is restored (registration and publisher
    wiring), so a still-live outer lifespan resumes receiving publications
    instead of going silently deaf after a nested shutdown.
    """
    global _ingestor
    if ingestor is None:
        ingestor = _ingestor
    if ingestor is not None:
        # Whatever happens next, a stopped instance must never be restorable.
        try:
            _displaced_ingestors.remove(ingestor)
        except ValueError:
            pass
    if ingestor is None or _ingestor is ingestor:
        restored: LiveUsageIngestor | None = None
        while _displaced_ingestors:
            candidate = _displaced_ingestors.pop()
            if candidate.is_running():
                restored = candidate
                break
            # Stopped or dead in the meantime — never restore a dead instance.
        _ingestor = restored
        register_live_usage_publisher(restored.publish if restored is not None else None)
    if ingestor is not None:
        await ingestor.stop()

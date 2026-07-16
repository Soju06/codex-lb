from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from inspect import isawaitable

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    cache_invalidation_bump_failures_total,
    cache_invalidation_poll_failures_total,
)
from app.db.models import CacheInvalidation
from app.db.session import close_session

logger = logging.getLogger(__name__)

NAMESPACE_API_KEY = "api_key"
NAMESPACE_FIREWALL = "firewall"
NAMESPACE_ACCOUNT_ROUTING = "account_routing"
NAMESPACE_ACCOUNT_SELECTION = "account_selection"
NAMESPACE_SETTINGS = "settings"
NAMESPACE_RESET_CREDITS = "reset_credits"
NAMESPACE_MODEL_REGISTRY = "model_registry"
# Callback return values are ignored; awaitables are awaited for their side
# effects only, so callbacks may return a status (e.g. bool) for other callers.
type InvalidationCallback = Callable[[], object | Awaitable[object]]

# Log-safe labels for namespace values. Static analyzers (CodeQL) classify the
# NAMESPACE_API_KEY constant as credential-like from its name alone, so log
# statements must not take dataflow from the constants. These literals mirror
# the namespace values; test_namespace_log_labels_cover_all_namespaces keeps
# them in sync.
_NAMESPACE_LOG_LABELS: dict[str, str] = {
    "api_key": "api_key",
    "firewall": "firewall",
    "account_routing": "account_routing",
    "account_selection": "account_selection",
    "settings": "settings",
    "reset_credits": "reset_credits",
    "model_registry": "model_registry",
}

_BUMP_RETRY_ATTEMPTS = 3
_BUMP_RETRY_BASE_SECONDS = 0.05
_POLL_FAILURES_WARNING_THRESHOLD = 3
_POLL_FAILURES_ERROR_THRESHOLD = 10


class CacheInvalidationPoller:
    def __init__(
        self,
        session_factory: Callable[[], AsyncSession],
        poll_interval_seconds: float = 0.5,
    ) -> None:
        self._session_factory = session_factory
        self._poll_interval = poll_interval_seconds
        self._known_versions: dict[str, int] = {}
        self._callbacks: dict[str, list[InvalidationCallback]] = {}
        self._pending_bumps: set[str] = set()
        self._consecutive_poll_failures = 0
        self._poll_initialized = False
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def on_invalidation(self, namespace: str, callback: InvalidationCallback) -> None:
        self._callbacks.setdefault(namespace, []).append(callback)

    async def initialize(self) -> None:
        """Seed baseline namespace versions before the process serves traffic.

        MUST run at lifespan startup before local caches / routing snapshots are
        loaded and before the first poll cycle. It records the current version of
        every ``cache_invalidation`` row as the baseline and marks the poller
        initialized, so that any bump a peer commits after this point is observed
        as a change (fires callbacks) rather than acknowledged as pre-existing
        state. Without this seed the first poll would treat every existing version
        row as a baseline and silently drop a peer bump that landed between local
        cache load and the first poll, leaving security/selection/routing state
        stale until the fallback TTL or a later bump.

        On success ``_poll_initialized`` is set to ``True``. If the read fails the
        method raises with state unchanged (baseline empty, ``_poll_initialized``
        still ``False``), so the caller can degrade to the first-poll-baselines
        behavior.
        """
        session = self._session_factory()
        try:
            result = await session.execute(select(CacheInvalidation.namespace, CacheInvalidation.version))
            rows = result.all()
        finally:
            await close_session(session)
        for namespace, version in rows:
            self._known_versions[namespace] = version
        self._poll_initialized = True

    async def prime(self) -> None:
        """Record the current version baseline without firing callbacks.

        Runs a single poll so that any bump landing *after* this call is
        delivered as an invalidation callback rather than absorbed as the
        poller's initial (callback-less) baseline. Call before a one-shot
        startup reconcile: a leader that persists-and-bumps in the window
        between that reconcile's read and the poller's first background tick
        would otherwise be silently missed until the next full backstop. The
        first poll only records versions (``_poll_initialized`` is still
        ``False``), so priming never invokes a callback. Unlike ``initialize``
        this also flushes any pending bumps and seeds baselines for every
        namespace observed in the ``cache_invalidation`` table.

        Mirrors ``initialize``'s error contract: if the baseline read fails the
        poller stays uninitialized (``_poll_initialized`` still ``False``) and
        this method raises so the caller can retry or explicitly degrade.
        ``_poll_once`` swallows the read error, so a silent success here would
        let the first *background* poll absorb a peer bump as the initial
        baseline, voiding the delivery guarantee priming exists to provide.
        """
        if not await self._poll_once():
            raise RuntimeError("cache invalidation prime failed: baseline version read did not complete")

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if not self._task:
            return
        self._stop.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def request_bump(self, namespace: str) -> None:
        """Enqueue a coalesced bump flushed at the start of the next poll cycle.

        Multiple requests for the same namespace within one cycle collapse into a
        single version bump. A failed flush keeps the namespace pending so it is
        retried on subsequent cycles. A request arriving while a flush is already
        awaiting the bump for this namespace re-queues it for the next cycle.
        """
        self._pending_bumps.add(namespace)

    async def bump(self, namespace: str) -> bool:
        for attempt in range(_BUMP_RETRY_ATTEMPTS):
            try:
                await self._bump_once(namespace)
                return True
            except OperationalError:
                if attempt == _BUMP_RETRY_ATTEMPTS - 1:
                    self._record_bump_failure(namespace)
                    return False
                await asyncio.sleep(_BUMP_RETRY_BASE_SECONDS * (2**attempt))
            except Exception:
                self._record_bump_failure(namespace)
                return False
        return False

    async def bump_local(self, namespace: str) -> bool:
        """Bump a namespace this replica has ALREADY invalidated locally.

        For callers that both mutate their own in-memory state AND want peers
        to react (e.g. the reset-credit redeem path evicts the affected
        account's snapshot locally, then bumps so peers clear their stores).
        After the shared bump succeeds this records the resulting version as
        already-observed on THIS poller, so the originating replica does NOT
        re-run its (possibly whole-store) callback for a bump it already
        accounted for locally. Peer replicas still observe the bump and fire.

        A peer bump that lands between our commit and the acknowledging read is
        acknowledged here without firing on this replica; that degrades to the
        per-replica refresh fallback the reset-credits design already documents
        for a lost bump, and never affects peers. ``_known_versions`` is only
        advanced (``max``), never rewound, so a concurrent poll cannot be forced
        to re-fire by this method.
        """
        if not await self.bump(namespace):
            return False
        session = self._session_factory()
        try:
            version = await session.scalar(
                select(CacheInvalidation.version).where(CacheInvalidation.namespace == namespace)
            )
        except Exception:
            # Failing to acknowledge only risks one redundant self-invalidation
            # on the next poll; the bump itself already succeeded for peers.
            logger.debug("cache_invalidation bump_local acknowledge read failed", exc_info=True)
            return True
        finally:
            await close_session(session)
        if version is not None:
            self._known_versions[namespace] = max(self._known_versions.get(namespace, 0), version)
        return True

    async def _bump_once(self, namespace: str) -> None:
        session = self._session_factory()
        try:
            dialect = session.get_bind().dialect.name
            if dialect == "postgresql":
                stmt = (
                    pg_insert(CacheInvalidation)
                    .values(namespace=namespace, version=1)
                    .on_conflict_do_update(
                        index_elements=[CacheInvalidation.namespace],
                        set_={"version": CacheInvalidation.version + 1},
                    )
                )
                await session.execute(stmt)
            elif dialect == "sqlite":
                stmt = (
                    sqlite_insert(CacheInvalidation)
                    .values(namespace=namespace, version=1)
                    .on_conflict_do_update(
                        index_elements=[CacheInvalidation.namespace],
                        set_={"version": CacheInvalidation.version + 1},
                    )
                )
                await session.execute(stmt)
            else:
                existing = await session.scalar(
                    select(CacheInvalidation).where(CacheInvalidation.namespace == namespace)
                )
                if existing is None:
                    session.add(CacheInvalidation(namespace=namespace, version=1))
                else:
                    await session.execute(
                        update(CacheInvalidation)
                        .where(CacheInvalidation.namespace == namespace)
                        .values(version=CacheInvalidation.version + 1)
                    )
            await session.commit()
        finally:
            await close_session(session)

    def _record_bump_failure(self, namespace: str) -> None:
        logger.error(
            "cache_invalidation bump failed for namespace %s",
            _NAMESPACE_LOG_LABELS.get(namespace, "unknown"),
            exc_info=True,
        )
        if PROMETHEUS_AVAILABLE and cache_invalidation_bump_failures_total is not None:
            cache_invalidation_bump_failures_total.labels(namespace=namespace).inc()

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._poll_once()
            except Exception:
                logger.debug("cache_invalidation poll failed", exc_info=True)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval)
            except asyncio.TimeoutError:
                continue

    async def _flush_pending_bumps(self) -> None:
        for namespace in sorted(self._pending_bumps):
            # Clear the pending marker BEFORE awaiting the bump: a request_bump()
            # arriving while the bump write is in flight must re-queue the
            # namespace so a mutation committing mid-flush still produces a
            # later bump instead of being coalesced into the version already
            # being written.
            self._pending_bumps.discard(namespace)
            if not await self.bump(namespace):
                self._pending_bumps.add(namespace)

    async def _poll_once(self) -> bool:
        """Flush pending bumps and reconcile observed versions once.

        Returns ``True`` when the baseline/version read completed (callbacks may
        still have failed individually and left their namespace unacknowledged)
        and ``False`` when the read itself failed, so ``prime`` can surface a
        failed startup seed instead of silently leaving the poller uninitialized.
        """
        await self._flush_pending_bumps()
        session: AsyncSession | None = None
        try:
            session = self._session_factory()
            result = await session.execute(select(CacheInvalidation.namespace, CacheInvalidation.version))
            rows = result.all()
        except Exception:
            self._record_poll_failure()
            return False
        finally:
            if session is not None:
                await close_session(session)
        self._consecutive_poll_failures = 0

        for namespace, version in rows:
            # Re-read the acknowledged version for THIS namespace immediately
            # before comparing: a concurrent bump_local() may have advanced it
            # (recording a local self-suppression) after this cycle's snapshot
            # read but before we reach this row. Using the freshly-read value
            # keeps the local ack authoritative.
            prev = self._known_versions.get(namespace)
            # Only a STRICTLY newer version is a change. A stale snapshot whose
            # version is <= the acknowledged version (e.g. read before a
            # concurrent local bump advanced _known_versions) must neither fire
            # the callback nor rewind the acknowledged version.
            changed = (prev is not None and version > prev) or (prev is None and self._poll_initialized and version > 0)
            if changed and not await self._run_callbacks(namespace):
                # Do not acknowledge the observed version: a failed callback
                # (e.g. a transient DB error during a snapshot refresh) must be
                # retried on the next cycle, or a replica would permanently
                # miss the invalidation.
                continue
            # Never lower an acknowledged version: a concurrent bump_local()
            # advance must survive an older in-flight poll observation.
            self._known_versions[namespace] = max(self._known_versions.get(namespace, 0), version)
        self._poll_initialized = True
        return True

    async def _run_callbacks(self, namespace: str) -> bool:
        succeeded = True
        for cb in self._callbacks.get(namespace, []):
            try:
                result = cb()
                if isawaitable(result):
                    await result
            except Exception:
                succeeded = False
                logger.warning(
                    "cache_invalidation callback failed for namespace %s; retrying next poll cycle",
                    _NAMESPACE_LOG_LABELS.get(namespace, "unknown"),
                    exc_info=True,
                )
        return succeeded

    def _record_poll_failure(self) -> None:
        self._consecutive_poll_failures += 1
        if PROMETHEUS_AVAILABLE and cache_invalidation_poll_failures_total is not None:
            cache_invalidation_poll_failures_total.inc()
        if self._consecutive_poll_failures >= _POLL_FAILURES_ERROR_THRESHOLD:
            logger.error(
                "cache_invalidation poll failed %d consecutive times",
                self._consecutive_poll_failures,
                exc_info=True,
            )
        elif self._consecutive_poll_failures >= _POLL_FAILURES_WARNING_THRESHOLD:
            logger.warning(
                "cache_invalidation poll failed %d consecutive times",
                self._consecutive_poll_failures,
                exc_info=True,
            )
        else:
            logger.debug("cache_invalidation poll failed", exc_info=True)


_poller: CacheInvalidationPoller | None = None


def get_cache_invalidation_poller() -> CacheInvalidationPoller | None:
    return _poller


def set_cache_invalidation_poller(poller: CacheInvalidationPoller | None) -> None:
    global _poller
    _poller = poller


async def bump_cache_invalidation(namespace: str) -> None:
    """Best-effort version bump; a no-op outside the lifespan poller's lifetime."""
    poller = _poller
    if poller is None:
        return
    await poller.bump(namespace)


async def bump_cache_invalidation_local(namespace: str) -> None:
    """Best-effort bump for a namespace already invalidated locally by the caller.

    Notifies peers while suppressing this replica's own re-invalidation for the
    bump it just issued. A no-op outside the lifespan poller's lifetime.
    """
    poller = _poller
    if poller is None:
        return
    await poller.bump_local(namespace)

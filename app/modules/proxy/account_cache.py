from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import anyio
from sqlalchemy import select

from app.core.cache.invalidation import (
    NAMESPACE_ACCOUNT_ROUTING,
    NAMESPACE_ACCOUNT_SELECTION,
    get_cache_invalidation_poller,
)
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal, close_session

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.modules.proxy.load_balancer import SelectionInputs

_AssignedAccountsKey = tuple[str, ...] | None
_CacheKey = tuple[str | None, str | None, str | None, str, _AssignedAccountsKey]


@dataclass(slots=True)
class _CachedSelectionInputs:
    data: SelectionInputs
    expires_at: float


class AccountSelectionCache:
    def __init__(self, ttl_seconds: int | None = None) -> None:
        if ttl_seconds is None:
            import sys

            ttl_seconds = 0 if "pytest" in sys.modules else 5
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds must be non-negative")
        self._ttl_seconds = ttl_seconds
        self._cache: dict[_CacheKey, _CachedSelectionInputs] = {}
        self._lock = anyio.Lock()
        self._generation: int = 0

    @property
    def generation(self) -> int:
        return self._generation

    async def get(self, key: _CacheKey = (None, None, None, "", None)) -> SelectionInputs | None:
        if self._ttl_seconds == 0:
            return None
        entry = self._cache.get(key)
        if entry is None:
            return None
        if time.monotonic() >= entry.expires_at:
            return None
        return entry.data

    async def set(
        self,
        data: SelectionInputs,
        key: _CacheKey = (None, None, None, "", None),
        *,
        generation: int | None = None,
    ) -> None:
        async with self._lock:
            if generation is not None and generation != self._generation:
                return
            self._cache[key] = _CachedSelectionInputs(
                data=data,
                expires_at=time.monotonic() + self._ttl_seconds,
            )

    def invalidate(self, *, propagate: bool = True) -> None:
        """Invalidate the local cache and, unless ``propagate`` is False, enqueue a
        coalesced cross-replica ``account_selection`` bump.

        The cache-invalidation poller callback registers ``propagate=False`` so a
        remote bump never re-bumps (feedback-loop prevention).
        """
        self._generation += 1
        self._cache.clear()
        if propagate:
            poller = get_cache_invalidation_poller()
            if poller is not None:
                poller.request_bump(NAMESPACE_ACCOUNT_SELECTION)


_ROUTING_UNAVAILABLE_STATUSES = frozenset(
    {
        AccountStatus.PAUSED,
        AccountStatus.REAUTH_REQUIRED,
        AccountStatus.DEACTIVATED,
    }
)


class RoutingAvailabilityCache:
    """Cluster-coherent view of which accounts are unavailable for routing.

    The cache keeps a snapshot of committed account statuses (``{account_id: status}``)
    seeded at poller start and rebuilt on every ``account_routing`` bump. An account is
    routing-unavailable when its committed status is PAUSED / REAUTH_REQUIRED /
    DEACTIVATED, or the id is absent from the snapshot (deleted), or a local mark
    overlay entry exists (covering the same-replica window between a mark and the
    snapshot rebuild). RATE_LIMITED and QUOTA_EXCEEDED deliberately do NOT map to
    unavailable, preserving cooldown-state bridge-session reuse.

    When the snapshot is unseeded (unit tests, poller not running) the cache degrades
    to the historical process-local set semantics.
    """

    def __init__(self, session_factory: Callable[[], AsyncSession] | None = None) -> None:
        self._session_factory = session_factory
        self._snapshot: dict[str, AccountStatus] | None = None
        self._local_marks: set[str] = set()

    @property
    def seeded(self) -> bool:
        return self._snapshot is not None

    def mark_unavailable(self, account_id: str) -> None:
        self._local_marks.add(account_id)
        _request_account_routing_bump()

    def clear_unavailable(self, account_id: str) -> None:
        self._local_marks.discard(account_id)
        if self._snapshot is not None:
            self._snapshot[account_id] = AccountStatus.ACTIVE
        _request_account_routing_bump()

    def is_unavailable(self, account_id: str) -> bool:
        if account_id in self._local_marks:
            return True
        snapshot = self._snapshot
        if snapshot is None:
            return False
        status = snapshot.get(account_id)
        return status is None or status in _ROUTING_UNAVAILABLE_STATUSES

    async def refresh_from_db(self) -> None:
        """Rebuild the snapshot from committed account statuses.

        Local overlay marks whose committed status became routable again are dropped —
        this is what lets a reactivation or re-authentication served by another replica
        clear this replica's marker without a restart. Only marks that already existed
        when this refresh started are eligible to be dropped: a mark added while the
        SELECT is in flight may not be reflected in the rows it read (the status commit
        can land after the read), so filtering it against that snapshot would silently
        lose the mark. Such marks are preserved and re-evaluated by the next refresh,
        which the mark's own queued ``account_routing`` bump guarantees.

        Database errors propagate to the caller: when invoked as an
        ``account_routing`` invalidation callback the poller then leaves the
        namespace version unacknowledged and retries on the next poll cycle, so
        a transient failure cannot make a replica permanently miss a pause,
        deletion, or re-authentication.
        """
        marks_before_refresh = frozenset(self._local_marks)
        factory = self._session_factory or SessionLocal
        session = factory()
        try:
            result = await session.execute(select(Account.id, Account.status))
            snapshot: dict[str, AccountStatus] = {account_id: status for account_id, status in result.all()}
        finally:
            await close_session(session)
        self._snapshot = snapshot
        self._local_marks = {
            account_id
            for account_id in self._local_marks
            if account_id not in marks_before_refresh
            or (status := snapshot.get(account_id)) is None
            or status in _ROUTING_UNAVAILABLE_STATUSES
        }

    def reset(self) -> None:
        """Drop all state (snapshot back to unseeded). Test isolation helper."""
        self._snapshot = None
        self._local_marks.clear()


_account_selection_cache = AccountSelectionCache()
_routing_availability_cache = RoutingAvailabilityCache()


def get_account_selection_cache() -> AccountSelectionCache:
    return _account_selection_cache


def get_routing_availability_cache() -> RoutingAvailabilityCache:
    return _routing_availability_cache


def _request_account_routing_bump() -> None:
    poller = get_cache_invalidation_poller()
    if poller is not None:
        poller.request_bump(NAMESPACE_ACCOUNT_ROUTING)


def mark_account_routing_unavailable(account_id: str) -> None:
    _routing_availability_cache.mark_unavailable(account_id)


def clear_account_routing_unavailable(account_id: str) -> None:
    _routing_availability_cache.clear_unavailable(account_id)


def clear_all_account_routing_unavailable() -> None:
    _routing_availability_cache.reset()


def is_account_routing_unavailable(account_id: str) -> bool:
    return _routing_availability_cache.is_unavailable(account_id)


async def propagate_account_routing_change() -> bool:
    """Durably bump the ``account_routing`` namespace before returning.

    Used by API-endpoint mutation paths (pause/reactivate/delete, OAuth re-auth,
    proxy-binding reactivation) so the cross-replica signal is written before the
    HTTP response returns. Returns False when no poller is wired or the bump failed
    after retries; the coalesced bump enqueued by ``mark_``/``clear_`` remains the
    fallback path.
    """
    poller = get_cache_invalidation_poller()
    if poller is None:
        return False
    return await poller.bump(NAMESPACE_ACCOUNT_ROUTING)

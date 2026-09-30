from __future__ import annotations

import time
from dataclasses import dataclass

from app.core.cache.invalidation import NAMESPACE_UPSTREAM_ROUTE, get_cache_invalidation_poller
from app.core.config.settings import get_settings
from app.core.upstream_proxy.resolver import UpstreamProxyRouteError
from app.core.upstream_proxy.types import ResolvedUpstreamRoute


@dataclass(frozen=True, slots=True)
class CachedRouteOutcome:
    """Terminal resolver outcome for one account, reproduced verbatim on a hit.

    Exactly one of the resolver's three outcomes is held: a resolved route, a
    permitted direct-egress ``None`` (``route is None`` with no error), or a
    fail-closed error reason. ``unwrap`` re-raises errors with the original
    reason and pool so a cache hit can never change the degradation path the
    resolver chose.
    """

    route: ResolvedUpstreamRoute | None
    error_reason: str | None
    error_pool_id: str | None
    expires_at: float

    def unwrap(self, account_id: str | None) -> ResolvedUpstreamRoute | None:
        if self.error_reason is not None:
            raise UpstreamProxyRouteError(self.error_reason, account_id=account_id, pool_id=self.error_pool_id)
        return self.route


class UpstreamRouteCache:
    """Per-account cache of upstream proxy route resolution outcomes.

    Admin mutations of resolver inputs (account bindings, pool members, the
    upstream-proxy dashboard settings) invalidate durably through the
    ``upstream_route`` / ``settings`` cache-invalidation namespaces; the TTL is
    only a backstop for out-of-band database edits. A TTL of 0 disables the
    cache entirely (the test-suite default).
    """

    def __init__(self) -> None:
        self._entries: dict[str, CachedRouteOutcome] = {}
        self._generation = 0

    @property
    def generation(self) -> int:
        return self._generation

    @staticmethod
    def _ttl_seconds() -> float:
        return get_settings().upstream_route_cache_ttl_seconds

    def get(self, account_id: str) -> CachedRouteOutcome | None:
        if self._ttl_seconds() <= 0:
            return None
        entry = self._entries.get(account_id)
        if entry is None:
            return None
        if time.monotonic() >= entry.expires_at:
            self._entries.pop(account_id, None)
            return None
        return entry

    def store_route(self, account_id: str, route: ResolvedUpstreamRoute | None, *, generation: int) -> None:
        self._store(account_id, route=route, error_reason=None, error_pool_id=None, generation=generation)

    def store_error(self, account_id: str, error: UpstreamProxyRouteError, *, generation: int) -> None:
        self._store(
            account_id,
            route=None,
            error_reason=error.reason,
            error_pool_id=error.pool_id,
            generation=generation,
        )

    def _store(
        self,
        account_id: str,
        *,
        route: ResolvedUpstreamRoute | None,
        error_reason: str | None,
        error_pool_id: str | None,
        generation: int,
    ) -> None:
        ttl = self._ttl_seconds()
        if ttl <= 0:
            return
        if generation != self._generation:
            # An invalidation landed while this outcome was being resolved; the
            # outcome may predate the mutation, so drop it.
            return
        self._entries[account_id] = CachedRouteOutcome(
            route=route,
            error_reason=error_reason,
            error_pool_id=error_pool_id,
            expires_at=time.monotonic() + ttl,
        )

    def clear(self) -> None:
        self._generation += 1
        self._entries.clear()

    async def invalidate(self, *, propagate: bool = True) -> None:
        """Clear locally and, unless ``propagate`` is False, durably bump the
        cross-replica ``upstream_route`` namespace before returning.

        Route mutations are security-bearing (a stale entry could keep an
        account on direct egress after a binding lands), so mutation paths
        await the bump rather than coalescing it. Poller callbacks use
        ``clear`` directly, so a remote bump never re-bumps.
        """
        self.clear()
        if propagate:
            poller = get_cache_invalidation_poller()
            if poller is not None and not await poller.bump(NAMESPACE_UPSTREAM_ROUTE):
                # bump() never raises; on failure the coalesced pending-set
                # retries on every poll cycle until the write lands, so peers
                # still converge once the database recovers. The TTL backstop
                # bounds staleness in the interim.
                poller.request_bump(NAMESPACE_UPSTREAM_ROUTE)


_upstream_route_cache = UpstreamRouteCache()


def get_upstream_route_cache() -> UpstreamRouteCache:
    return _upstream_route_cache

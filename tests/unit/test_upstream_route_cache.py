from __future__ import annotations

from typing import cast

import pytest

from app.core.cache.invalidation import (
    NAMESPACE_UPSTREAM_ROUTE,
    CacheInvalidationPoller,
    set_cache_invalidation_poller,
)
from app.core.config.settings import get_settings
from app.core.upstream_proxy.cache import UpstreamRouteCache
from app.core.upstream_proxy.resolver import UpstreamProxyRouteError
from app.core.upstream_proxy.types import ResolvedProxyEndpoint, ResolvedUpstreamRoute

pytestmark = pytest.mark.unit


@pytest.fixture
def route_cache_ttl(monkeypatch):
    monkeypatch.setenv("CODEX_LB_UPSTREAM_ROUTE_CACHE_TTL_SECONDS", "60")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _route(endpoint_id: str = "ep-1") -> ResolvedUpstreamRoute:
    return ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool-1",
        endpoint=ResolvedProxyEndpoint(id=endpoint_id, scheme="http", host="proxy.internal", port=8080),
    )


def test_store_and_get_roundtrip(route_cache_ttl) -> None:
    cache = UpstreamRouteCache()
    route = _route()
    cache.store_route("acct-1", route, generation=cache.generation)
    cache.store_route("acct-2", None, generation=cache.generation)

    hit = cache.get("acct-1")
    assert hit is not None
    assert hit.unwrap("acct-1") is route
    direct = cache.get("acct-2")
    assert direct is not None
    assert direct.unwrap("acct-2") is None
    assert cache.get("acct-3") is None


def test_ttl_zero_disables_cache() -> None:
    cache = UpstreamRouteCache()
    cache.store_route("acct-1", _route(), generation=cache.generation)
    assert cache.get("acct-1") is None


def test_entry_expires_after_ttl(route_cache_ttl, monkeypatch) -> None:
    import app.core.upstream_proxy.cache as cache_module

    now = 1000.0
    monkeypatch.setattr(cache_module.time, "monotonic", lambda: now)
    cache = UpstreamRouteCache()
    cache.store_route("acct-1", _route(), generation=cache.generation)
    assert cache.get("acct-1") is not None

    now = 1061.0
    assert cache.get("acct-1") is None


def test_generation_guard_drops_stale_repopulation(route_cache_ttl) -> None:
    cache = UpstreamRouteCache()
    generation = cache.generation
    cache.clear()
    cache.store_route("acct-1", _route(), generation=generation)
    assert cache.get("acct-1") is None


def test_cached_error_reraises_same_reason(route_cache_ttl) -> None:
    cache = UpstreamRouteCache()
    error = UpstreamProxyRouteError("pool_has_no_active_endpoints", account_id="acct-1", pool_id="pool-1")
    cache.store_error("acct-1", error, generation=cache.generation)

    entry = cache.get("acct-1")
    assert entry is not None
    for _ in range(2):
        with pytest.raises(UpstreamProxyRouteError) as excinfo:
            entry.unwrap("acct-1")
        assert excinfo.value.reason == "pool_has_no_active_endpoints"
        assert excinfo.value.account_id == "acct-1"
        assert excinfo.value.pool_id == "pool-1"
        assert excinfo.value is not error


async def test_invalidate_falls_back_to_coalesced_bump_on_failure(route_cache_ttl) -> None:
    class _FakePoller:
        def __init__(self) -> None:
            self.bumped: list[str] = []
            self.requested: list[str] = []

        async def bump(self, namespace: str) -> bool:
            self.bumped.append(namespace)
            return False

        def request_bump(self, namespace: str) -> None:
            self.requested.append(namespace)

    poller = _FakePoller()
    set_cache_invalidation_poller(cast(CacheInvalidationPoller, poller))
    try:
        cache = UpstreamRouteCache()
        cache.store_route("acct-1", _route(), generation=cache.generation)
        await cache.invalidate()
        assert cache.get("acct-1") is None
        assert poller.bumped == [NAMESPACE_UPSTREAM_ROUTE]
        # bump() never raises; a failed durable bump must enqueue the coalesced
        # retry so peers still converge once the write path recovers.
        assert poller.requested == [NAMESPACE_UPSTREAM_ROUTE]
    finally:
        set_cache_invalidation_poller(None)


def test_clear_empties_and_advances_generation(route_cache_ttl) -> None:
    cache = UpstreamRouteCache()
    cache.store_route("acct-1", _route(), generation=cache.generation)
    generation = cache.generation
    cache.clear()
    assert cache.get("acct-1") is None
    assert cache.generation == generation + 1

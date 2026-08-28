from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

import app.modules.proxy.service as proxy_service
from app.core.config.settings import get_settings
from app.core.upstream_proxy.cache import get_upstream_route_cache
from app.core.upstream_proxy.resolver import UpstreamProxyRouteError
from app.core.upstream_proxy.types import ResolvedProxyEndpoint, ResolvedUpstreamRoute
from app.db.models import Account
from app.modules.proxy._service.streaming import helpers

pytestmark = pytest.mark.unit


@pytest.fixture
def route_cache_ttl(monkeypatch):
    monkeypatch.setenv("CODEX_LB_UPSTREAM_ROUTE_CACHE_TTL_SECONDS", "60")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> bool:
        return False


def _route() -> ResolvedUpstreamRoute:
    return ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool-1",
        endpoint=ResolvedProxyEndpoint(id="ep-1", scheme="http", host="proxy.internal", port=8080),
    )


def _wire_resolver(monkeypatch, outcome):
    calls = {"count": 0}

    async def fake_resolve(session, *, account_id, operation, scope, encryptor):
        calls["count"] += 1
        assert scope == "account"
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(proxy_service, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(proxy_service, "resolve_upstream_route", fake_resolve)
    return calls


@pytest.mark.asyncio
async def test_second_resolution_served_from_cache(route_cache_ttl, monkeypatch) -> None:
    route = _route()
    calls = _wire_resolver(monkeypatch, route)
    proxy = SimpleNamespace(_encryptor=None)
    account = cast(Account, SimpleNamespace(id="acct-1"))

    first = await helpers._resolve_upstream_route_for_account(proxy, account, operation="responses")
    second = await helpers._resolve_upstream_route_for_account(proxy, account, operation="responses")

    assert first is route
    assert second is route
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_direct_egress_none_is_cached(route_cache_ttl, monkeypatch) -> None:
    calls = _wire_resolver(monkeypatch, None)
    proxy = SimpleNamespace(_encryptor=None)
    account = cast(Account, SimpleNamespace(id="acct-1"))

    assert await helpers._resolve_upstream_route_for_account(proxy, account, operation="responses") is None
    assert await helpers._resolve_upstream_route_for_account(proxy, account, operation="responses") is None
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_cached_fail_closed_error_keeps_failing_closed(route_cache_ttl, monkeypatch) -> None:
    error = UpstreamProxyRouteError("pool_has_no_active_endpoints", account_id="acct-1", pool_id="pool-1")
    calls = _wire_resolver(monkeypatch, error)
    proxy = SimpleNamespace(_encryptor=None)
    account = cast(Account, SimpleNamespace(id="acct-1"))

    for _ in range(2):
        with pytest.raises(UpstreamProxyRouteError) as excinfo:
            await helpers._resolve_upstream_route_for_account(proxy, account, operation="responses")
        assert excinfo.value.reason == "pool_has_no_active_endpoints"
        assert excinfo.value.pool_id == "pool-1"
    # The second failure must come from the cache, not a re-resolution — and
    # it must never degrade to the default pool or direct egress (no further
    # resolver calls that could take a different branch).
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_invalidation_forces_re_resolution(route_cache_ttl, monkeypatch) -> None:
    route = _route()
    calls = _wire_resolver(monkeypatch, route)
    proxy = SimpleNamespace(_encryptor=None)
    account = cast(Account, SimpleNamespace(id="acct-1"))

    await helpers._resolve_upstream_route_for_account(proxy, account, operation="responses")
    get_upstream_route_cache().clear()
    await helpers._resolve_upstream_route_for_account(proxy, account, operation="responses")

    assert calls["count"] == 2

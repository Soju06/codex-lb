from __future__ import annotations

import pytest

import app.modules.proxy.rate_limit_cache as rate_limit_cache_module
from app.modules.proxy.rate_limit_cache import RateLimitHeadersCache

pytestmark = pytest.mark.unit


class _FakeSettings:
    usage_refresh_interval_seconds = 60


@pytest.mark.asyncio
async def test_cache_hit_within_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"now": 100.0, "calls": 0}
    monkeypatch.setattr(rate_limit_cache_module.time, "monotonic", lambda: state["now"])
    monkeypatch.setattr(rate_limit_cache_module, "get_settings", lambda: _FakeSettings())

    cache = RateLimitHeadersCache()

    async def compute() -> dict[str, str]:
        state["calls"] += 1
        return {"x-header": f"v{state['calls']}"}

    first = await cache.get(compute)
    second = await cache.get(compute)
    assert first is second
    assert state["calls"] == 1
    assert first == {"x-header": "v1"}


@pytest.mark.asyncio
async def test_cache_recompute_after_ttl_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"now": 100.0, "calls": 0}
    monkeypatch.setattr(rate_limit_cache_module.time, "monotonic", lambda: state["now"])
    monkeypatch.setattr(rate_limit_cache_module, "get_settings", lambda: _FakeSettings())

    cache = RateLimitHeadersCache()

    async def compute() -> dict[str, str]:
        state["calls"] += 1
        return {"x-header": f"v{state['calls']}"}

    first = await cache.get(compute)
    assert state["calls"] == 1

    state["now"] = 161.0  # past 60s TTL
    second = await cache.get(compute)
    assert second is not first
    assert state["calls"] == 2
    assert second == {"x-header": "v2"}


@pytest.mark.asyncio
async def test_cache_recompute_after_invalidate(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {"now": 100.0, "calls": 0}
    monkeypatch.setattr(rate_limit_cache_module.time, "monotonic", lambda: state["now"])
    monkeypatch.setattr(rate_limit_cache_module, "get_settings", lambda: _FakeSettings())

    cache = RateLimitHeadersCache()

    async def compute() -> dict[str, str]:
        state["calls"] += 1
        return {"x-header": f"v{state['calls']}"}

    first = await cache.get(compute)
    assert state["calls"] == 1

    await cache.invalidate()
    second = await cache.get(compute)
    assert second is not first
    assert state["calls"] == 2
    assert second == {"x-header": "v2"}

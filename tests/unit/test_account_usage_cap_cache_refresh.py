from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core.cache.invalidation import NAMESPACE_ACCOUNT_ROUTING
from app.modules.proxy import account_cache


@pytest.mark.asyncio
async def test_usage_cap_cache_refresh_is_best_effort_after_persisted_write(monkeypatch: pytest.MonkeyPatch) -> None:
    invalidations: list[bool] = []
    bumps: list[str] = []

    class _RoutingCache:
        async def refresh_usage_caps_from_db(self) -> None:
            raise RuntimeError("refresh failed")

    monkeypatch.setattr(
        account_cache, "_account_selection_cache", SimpleNamespace(invalidate=lambda: invalidations.append(True))
    )
    monkeypatch.setattr(account_cache, "_routing_availability_cache", _RoutingCache())
    monkeypatch.setattr(
        account_cache,
        "get_cache_invalidation_poller",
        lambda: SimpleNamespace(request_bump=lambda namespace: bumps.append(namespace)),
    )

    await account_cache.refresh_usage_cap_caches_after_write()

    assert invalidations == [True]
    assert bumps == [NAMESPACE_ACCOUNT_ROUTING]

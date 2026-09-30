from __future__ import annotations

from collections.abc import Iterator
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

from app.core.auth.api_key_cache import get_api_key_cache
from app.core.cache.invalidation import (
    NAMESPACE_ACCOUNT_ROUTING,
    NAMESPACE_API_KEY,
    CacheInvalidationPoller,
)
from app.modules.proxy import account_cache

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def clear_policy_cache() -> Iterator[None]:
    get_api_key_cache().clear()
    yield
    get_api_key_cache().clear()


async def test_coalesced_pool_change_publishes_api_key_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    poller = Mock()
    monkeypatch.setattr(account_cache, "get_cache_invalidation_poller", lambda: cast(CacheInvalidationPoller, poller))
    cache = get_api_key_cache()
    version = cache.version
    await cache.set("key", "old-policy")

    account_cache.request_account_routing_change()

    assert await cache.get("key") is None
    assert cache.version > version
    assert [call.args[0] for call in poller.request_bump.call_args_list] == [
        NAMESPACE_ACCOUNT_ROUTING,
        NAMESPACE_API_KEY,
    ]
    await cache.set("key", "stale-inflight-policy", if_version=version)
    assert await cache.get("key") is None


@pytest.mark.parametrize("routing_ok, policy_ok", [(True, True), (False, True), (True, False), (False, False)])
async def test_awaited_pool_change_retries_only_failed_namespaces(
    monkeypatch: pytest.MonkeyPatch,
    routing_ok: bool,
    policy_ok: bool,
) -> None:
    cache = get_api_key_cache()
    await cache.set("key", "old-policy")

    async def bump(namespace: str) -> bool:
        assert await cache.get("key") is None
        return routing_ok if namespace == NAMESPACE_ACCOUNT_ROUTING else policy_ok

    poller = Mock()
    poller.bump = AsyncMock(side_effect=bump)
    monkeypatch.setattr(account_cache, "get_cache_invalidation_poller", lambda: cast(CacheInvalidationPoller, poller))

    assert await account_cache.propagate_account_routing_change() is (routing_ok and policy_ok)
    assert [call.args[0] for call in poller.bump.await_args_list] == [NAMESPACE_ACCOUNT_ROUTING, NAMESPACE_API_KEY]
    assert [call.args[0] for call in poller.request_bump.call_args_list] == [
        namespace
        for namespace, succeeded in ((NAMESPACE_ACCOUNT_ROUTING, routing_ok), (NAMESPACE_API_KEY, policy_ok))
        if not succeeded
    ]

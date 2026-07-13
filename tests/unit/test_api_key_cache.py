from __future__ import annotations

import pytest

from app.core.auth.api_key_cache import ApiKeyCache, get_api_key_cache

pytestmark = pytest.mark.unit


def test_auth_cache_ttl_is_invalidation_backstop_not_per_turn_expiry() -> None:
    """Key mutations clear the cache via the invalidation poller; the TTL is
    only a backstop and must exceed typical interactive turn gaps so
    unchanged keys are not re-read from the database every turn."""
    assert get_api_key_cache()._ttl >= 60


@pytest.mark.asyncio
async def test_cache_hit_within_ttl_and_invalidation_clears() -> None:
    cache: ApiKeyCache[str] = ApiKeyCache(ttl_seconds=60)
    await cache.set("hash_a", "data_a")
    assert await cache.get("hash_a") == "data_a"

    await cache.invalidate("hash_a")
    assert await cache.get("hash_a") is None


@pytest.mark.asyncio
async def test_clear_bumps_version_and_blocks_stale_set() -> None:
    """The poller's clear() must prevent a concurrent stale validation result
    (read before the mutation) from repopulating the cache."""
    cache: ApiKeyCache[str] = ApiKeyCache(ttl_seconds=60)
    version_before_read = cache.version
    cache.clear()
    await cache.set("hash_a", "stale", if_version=version_before_read)
    assert await cache.get("hash_a") is None

from __future__ import annotations

from types import SimpleNamespace

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
    await cache.set("hash_a", "stale", if_version=version_before_read, ttl_seconds=5)
    assert await cache.get("hash_a") is None


@pytest.mark.asyncio
async def test_short_entry_ttl_expires_without_shortening_complete_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    now = {"value": 100.0}
    monkeypatch.setattr("app.core.auth.api_key_cache.time", SimpleNamespace(monotonic=lambda: now["value"]))
    cache: ApiKeyCache[str] = ApiKeyCache(ttl_seconds=60)
    await cache.set("incomplete", "short", ttl_seconds=5)
    await cache.set("complete", "ordinary")
    await cache.set("bounded", "ordinary", ttl_seconds=120)

    now["value"] = 104.9
    assert await cache.get("incomplete") == "short"
    now["value"] = 105.0
    assert await cache.get("incomplete") is None
    assert await cache.get("complete") == "ordinary"
    now["value"] = 160.0
    assert await cache.get("complete") is None
    assert await cache.get("bounded") is None


@pytest.mark.asyncio
async def test_short_entry_ttl_rejects_negative_duration() -> None:
    cache: ApiKeyCache[str] = ApiKeyCache(ttl_seconds=60)
    with pytest.raises(ValueError, match="ttl_seconds must be non-negative"):
        await cache.set("hash", "data", ttl_seconds=-1)
    assert await cache.get("hash") is None

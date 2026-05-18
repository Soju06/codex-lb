from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Generic, TypeVar

import anyio

_CacheValueT = TypeVar("_CacheValueT")


@dataclass(slots=True)
class CachedApiKey(Generic[_CacheValueT]):
    data: _CacheValueT
    expires_at: float


class ApiKeyCache(Generic[_CacheValueT]):
    def __init__(self, ttl_seconds: int = 5, max_entries: int = 10_000) -> None:
        self._cache: dict[str, CachedApiKey[_CacheValueT]] = {}
        self._lock = anyio.Lock()
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._version = 0

    @property
    def version(self) -> int:
        return self._version

    async def get(self, key_hash: str) -> _CacheValueT | None:
        entry = self._cache.get(key_hash)
        if entry and time.monotonic() < entry.expires_at:
            return entry.data
        return None

    async def set(self, key_hash: str, data: _CacheValueT, *, if_version: int | None = None) -> None:
        async with self._lock:
            if if_version is not None and if_version != self._version:
                return
            if len(self._cache) >= self._max_entries:
                oldest = min(self._cache.keys(), key=lambda key: self._cache[key].expires_at)
                del self._cache[oldest]
            self._cache[key_hash] = CachedApiKey(data=data, expires_at=time.monotonic() + self._ttl)

    async def invalidate(self, key_hash: str) -> None:
        async with self._lock:
            self._cache.pop(key_hash, None)
            self._version += 1

    def clear(self) -> None:
        self._cache.clear()
        self._version += 1


_api_key_cache: ApiKeyCache[object] = ApiKeyCache(ttl_seconds=2)


def get_api_key_cache() -> ApiKeyCache[object]:
    return _api_key_cache

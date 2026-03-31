from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import anyio


@dataclass(slots=True)
class CachedApiKey:
    data: Any
    expires_at: float


class ApiKeyCache:
    def __init__(self, ttl_seconds: int = 5, max_entries: int = 10_000) -> None:
        self._cache: dict[str, CachedApiKey] = {}
        self._lock = anyio.Lock()
        self._ttl = ttl_seconds
        self._max_entries = max_entries

    async def get(self, key_hash: str) -> Any | None:
        entry = self._cache.get(key_hash)
        if entry and time.monotonic() < entry.expires_at:
            return entry.data
        return None

    async def set(self, key_hash: str, data: Any) -> None:
        async with self._lock:
            if len(self._cache) >= self._max_entries:
                oldest = min(self._cache.keys(), key=lambda key: self._cache[key].expires_at)
                del self._cache[oldest]
            self._cache[key_hash] = CachedApiKey(data=data, expires_at=time.monotonic() + self._ttl)

    async def invalidate(self, key_hash: str) -> None:
        async with self._lock:
            self._cache.pop(key_hash, None)

    def clear(self) -> None:
        self._cache.clear()


_api_key_cache = ApiKeyCache()


def get_api_key_cache() -> ApiKeyCache:
    return _api_key_cache

from __future__ import annotations

import time
from collections.abc import Callable, Coroutine
from typing import Any

import anyio

from app.core.config.settings import get_settings


class RateLimitHeadersCache:
    def __init__(self) -> None:
        self._cached_headers: dict[str, str] | None = None
        self._cached_at = 0.0
        self._lock = anyio.Lock()

    async def get(
        self,
        compute: Callable[[], Coroutine[Any, Any, dict[str, str]]],
    ) -> dict[str, str]:
        ttl = get_settings().usage_refresh_interval_seconds
        now = time.monotonic()
        if self._cached_headers is not None and now - self._cached_at < ttl:
            return self._cached_headers

        async with self._lock:
            now = time.monotonic()
            if self._cached_headers is not None and now - self._cached_at < ttl:
                return self._cached_headers

            headers = await compute()
            self._cached_headers = headers
            self._cached_at = now
            return headers

    async def invalidate(self) -> None:
        async with self._lock:
            self._cached_headers = None
            self._cached_at = 0.0


_rate_limit_headers_cache = RateLimitHeadersCache()


def get_rate_limit_headers_cache() -> RateLimitHeadersCache:
    return _rate_limit_headers_cache

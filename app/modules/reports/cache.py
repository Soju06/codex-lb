"""Small process-local report caches; one owned computation per cache at a time."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from time import monotonic

from app.modules.reports.schemas import ReportsOptionsResponse, ReportsResponse


@dataclass(frozen=True)
class ReportCacheKey:
    start: date
    end: date
    timezone: str
    accounts: tuple[str, ...]
    api_keys: tuple[str, ...]
    model: str
    useragent: str


class ReportCache[T]:
    def __init__(self, *, ttl_seconds: float = 60, max_entries: int = 64) -> None:
        self._ttl = ttl_seconds
        self._capacity = max_entries
        self._entries: OrderedDict[ReportCacheKey, tuple[float, T]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def get(self, key: ReportCacheKey, compute: Callable[[], Awaitable[T]]) -> T:
        # No detached tasks or shared AsyncSession. Cancellation releases the
        # lock; exceptions never enter the cache. Serial misses bound report
        # DB concurrency even when callers choose many different filters.
        async with self._lock:
            now = monotonic()
            for expired in [key for key, (expires, _) in self._entries.items() if expires <= now]:
                del self._entries[expired]
            if key in self._entries:
                self._entries.move_to_end(key)
                return self._entries[key][1]
            value = await compute()
            self._entries[key] = (monotonic() + self._ttl, value)
            while len(self._entries) > self._capacity:
                self._entries.popitem(last=False)
            return value


class ReportsCaches:
    def __init__(self) -> None:
        self.reports = ReportCache[ReportsResponse]()
        self.options = ReportCache[ReportsOptionsResponse]()

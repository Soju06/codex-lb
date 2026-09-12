from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date
from unittest.mock import AsyncMock

import pytest

from app.modules.reports.cache import ReportCache, ReportCacheKey

pytestmark = pytest.mark.unit
KEY = ReportCacheKey(date(2026, 1, 1), date(2026, 1, 7), "UTC", (), (), "", "")


async def test_cache_coalesces_identical_loads_and_isolates_filter_keys():
    cache = ReportCache[ReportCacheKey, int]()
    compute = AsyncMock(return_value=42)
    assert await asyncio.gather(*(cache.get(KEY, compute) for _ in range(10))) == [42] * 10
    compute.assert_awaited_once()
    await cache.get(replace(KEY, useragent="CLI"), compute)
    assert compute.await_count == 2


async def test_cache_expiry_and_capacity(monkeypatch):
    import app.modules.reports.cache as module

    clock = 1.0
    monkeypatch.setattr(module, "monotonic", lambda: clock)
    cache = ReportCache[ReportCacheKey, int](max_entries=1, ttl_seconds=60)
    compute = AsyncMock(return_value=1)
    await cache.get(KEY, compute)
    clock = 61
    await cache.get(KEY, compute)
    await cache.get(replace(KEY, model="m"), compute)
    await cache.get(KEY, compute)
    assert compute.await_count == 4


async def test_cache_failure_and_cancellation_release_waiters():
    cache = ReportCache[ReportCacheKey, int]()
    with pytest.raises(ValueError):
        await cache.get(KEY, AsyncMock(side_effect=ValueError("failed")))
    entered = asyncio.Event()

    async def pending():
        entered.set()
        await asyncio.Event().wait()
        return 0

    task = asyncio.create_task(cache.get(KEY, pending))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.wait_for(cache.get(KEY, AsyncMock(return_value=7)), timeout=1) == 7


async def test_cache_hits_bypass_slow_misses_while_distinct_computations_stay_bounded():
    cache = ReportCache[ReportCacheKey, int]()
    await cache.get(KEY, AsyncMock(return_value=42))
    entered = asyncio.Event()
    release = asyncio.Event()
    queued = asyncio.Event()
    next_compute = AsyncMock(return_value=9)

    async def slow():
        entered.set()
        await release.wait()
        return 7

    async def next_request():
        queued.set()
        return await cache.get(replace(KEY, model="next"), next_compute)

    async with asyncio.timeout(5), asyncio.TaskGroup() as tasks:
        slow_task = tasks.create_task(cache.get(replace(KEY, model="slow"), slow))
        await entered.wait()
        next_task = tasks.create_task(next_request())
        await queued.wait()
        next_compute.assert_not_awaited()
        assert await cache.get(KEY, AsyncMock(side_effect=AssertionError("cache miss"))) == 42
        # Cancelling a queued request must not cancel the active computation.
        next_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await next_task
        assert not slow_task.done()
        next_task = tasks.create_task(next_request())
        release.set()
    assert slow_task.result() == 7
    assert next_task.result() == 9
    next_compute.assert_awaited_once()

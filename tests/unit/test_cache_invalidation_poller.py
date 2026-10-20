from __future__ import annotations

import asyncio
from typing import cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache.invalidation import NAMESPACE_UPSTREAM_ROUTE, CacheInvalidationPoller


class _VersionRows:
    @staticmethod
    def all() -> list[tuple[str, int]]:
        return [(NAMESPACE_UPSTREAM_ROUTE, 2)]


class _ReadableSession:
    def in_transaction(self) -> bool:
        return False

    async def execute(self, *_args: object, **_kwargs: object) -> _VersionRows:
        return _VersionRows()

    async def close(self) -> None:
        return None


class _FailFirstVersionSessionFactory:
    def __init__(self) -> None:
        self._failed = False

    def __call__(self) -> AsyncSession:
        if not self._failed:
            self._failed = True
            raise RuntimeError("peer-version read failed")
        return cast(AsyncSession, _ReadableSession())


@pytest.mark.asyncio
async def test_background_start_reconciles_first_version_after_failed_prime(monkeypatch) -> None:
    calls: list[str] = []
    poller = CacheInvalidationPoller(_FailFirstVersionSessionFactory())
    poller.on_invalidation(NAMESPACE_UPSTREAM_ROUTE, lambda: calls.append("clear"))

    with pytest.raises(RuntimeError, match="baseline version read did not complete"):
        await poller.prime()

    parked = asyncio.Event()

    async def park_background_loop() -> None:
        await parked.wait()

    monkeypatch.setattr(poller, "_run", park_background_loop)
    await poller.start()
    try:
        await poller._poll_once()
        await poller._poll_once()
    finally:
        await poller.stop()

    # The first successful poll reconciles and acknowledges version 2; the
    # unchanged second observation must not invoke the callback again.
    assert calls == ["clear"]


@pytest.mark.parametrize(
    "error",
    [OperationalError("stmt", {}, Exception("database is locked")), RuntimeError("driver failed")],
    ids=["exhausted-lock-retries", "unexpected-driver-error"],
)
async def test_failed_direct_bump_is_retained_and_retried(monkeypatch, error: Exception) -> None:
    poller = CacheInvalidationPoller(lambda: cast(AsyncSession, _ReadableSession()))
    failed_write = AsyncMock(side_effect=error)
    monkeypatch.setattr(poller, "_bump_once", failed_write)

    assert await poller.bump(NAMESPACE_UPSTREAM_ROUTE) is False
    assert NAMESPACE_UPSTREAM_ROUTE in poller._pending_bumps
    assert failed_write.await_count == (3 if isinstance(error, OperationalError) else 1)

    recovered_write = AsyncMock()
    monkeypatch.setattr(poller, "_bump_once", recovered_write)
    await poller._flush_pending_bumps()
    recovered_write.assert_awaited_once_with(NAMESPACE_UPSTREAM_ROUTE)
    assert NAMESPACE_UPSTREAM_ROUTE not in poller._pending_bumps


async def test_direct_bump_consumes_earlier_pending_work(monkeypatch) -> None:
    poller = CacheInvalidationPoller(lambda: cast(AsyncSession, _ReadableSession()))
    write = AsyncMock()
    monkeypatch.setattr(poller, "_bump_once", write)
    poller.request_bump(NAMESPACE_UPSTREAM_ROUTE)

    assert await poller.bump(NAMESPACE_UPSTREAM_ROUTE) is True
    await poller._flush_pending_bumps()

    write.assert_awaited_once_with(NAMESPACE_UPSTREAM_ROUTE)
    assert NAMESPACE_UPSTREAM_ROUTE not in poller._pending_bumps


@pytest.mark.parametrize("publication", ["immediate", "coalesced"])
async def test_request_during_publication_survives_the_write(monkeypatch, publication: str) -> None:
    poller = CacheInvalidationPoller(lambda: cast(AsyncSession, _ReadableSession()))
    started = asyncio.Event()
    release = asyncio.Event()
    writes = 0

    async def write(_namespace: str) -> None:
        nonlocal writes
        writes += 1
        started.set()
        await release.wait()

    monkeypatch.setattr(poller, "_bump_once", write)
    poller.request_bump(NAMESPACE_UPSTREAM_ROUTE)
    task = asyncio.create_task(
        poller.bump(NAMESPACE_UPSTREAM_ROUTE) if publication == "immediate" else poller._flush_pending_bumps()
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        assert NAMESPACE_UPSTREAM_ROUTE not in poller._pending_bumps
        poller.request_bump(NAMESPACE_UPSTREAM_ROUTE)
        release.set()
        await asyncio.wait_for(task, timeout=2)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert NAMESPACE_UPSTREAM_ROUTE in poller._pending_bumps
    await poller._flush_pending_bumps()
    assert writes == 2
    assert NAMESPACE_UPSTREAM_ROUTE not in poller._pending_bumps


@pytest.mark.parametrize("publication", ["immediate", "coalesced"])
@pytest.mark.parametrize("committed", [False, True], ids=["before-commit", "after-commit"])
async def test_cancelled_publication_is_retained_even_after_commit(
    monkeypatch, publication: str, committed: bool
) -> None:
    poller = CacheInvalidationPoller(lambda: cast(AsyncSession, _ReadableSession()))
    started = asyncio.Event()
    writes = 0

    async def interrupted_write(_namespace: str) -> None:
        nonlocal writes
        if committed:
            writes += 1
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(poller, "_bump_once", interrupted_write)
    # Deliberately leave direct publication unqueued: it owns retention itself.
    if publication == "coalesced":
        poller.request_bump(NAMESPACE_UPSTREAM_ROUTE)
    task = asyncio.create_task(
        poller.bump(NAMESPACE_UPSTREAM_ROUTE) if publication == "immediate" else poller._flush_pending_bumps()
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert NAMESPACE_UPSTREAM_ROUTE in poller._pending_bumps

    async def recovered_write(_namespace: str) -> None:
        nonlocal writes
        writes += 1

    monkeypatch.setattr(poller, "_bump_once", recovered_write)
    await poller._flush_pending_bumps()
    assert writes == (2 if committed else 1)
    assert NAMESPACE_UPSTREAM_ROUTE not in poller._pending_bumps


async def test_cancellation_during_direct_retry_backoff_is_retained(monkeypatch) -> None:
    poller = CacheInvalidationPoller(lambda: cast(AsyncSession, _ReadableSession()))
    started = asyncio.Event()

    async def parked_backoff(_seconds: float) -> None:
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(poller, "_bump_once", AsyncMock(side_effect=OperationalError("stmt", {}, Exception("locked"))))
    monkeypatch.setattr("app.core.cache.invalidation.asyncio.sleep", parked_backoff)
    task = asyncio.create_task(poller.bump(NAMESPACE_UPSTREAM_ROUTE))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert NAMESPACE_UPSTREAM_ROUTE in poller._pending_bumps
    recovered_write = AsyncMock()
    monkeypatch.setattr(poller, "_bump_once", recovered_write)
    await poller._flush_pending_bumps()
    recovered_write.assert_awaited_once_with(NAMESPACE_UPSTREAM_ROUTE)


async def test_driver_failure_after_commit_is_retained(monkeypatch) -> None:
    poller = CacheInvalidationPoller(lambda: cast(AsyncSession, _ReadableSession()))
    writes = 0

    async def committed_then_raised(_namespace: str) -> None:
        nonlocal writes
        writes += 1
        raise RuntimeError("commit accepted but driver completion failed")

    monkeypatch.setattr(poller, "_bump_once", committed_then_raised)
    assert await poller.bump(NAMESPACE_UPSTREAM_ROUTE) is False
    assert NAMESPACE_UPSTREAM_ROUTE in poller._pending_bumps
    assert writes == 1

    async def recovered_write(_namespace: str) -> None:
        nonlocal writes
        writes += 1

    monkeypatch.setattr(poller, "_bump_once", recovered_write)
    await poller._flush_pending_bumps()
    assert writes == 2
    assert NAMESPACE_UPSTREAM_ROUTE not in poller._pending_bumps

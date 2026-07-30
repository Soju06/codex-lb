from __future__ import annotations

import asyncio
from typing import cast

import pytest
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

"""Enter starlette's blocking ``TestClient`` from an async test without freezing the loop.

``TestClient.__enter__`` runs the app lifespan on a private portal thread and
blocks the *calling* thread until startup finishes; ``__exit__`` blocks the
same way for shutdown. Inside an ``async def`` test the calling thread is the
shared session event loop, which also owns the ``async_client`` lifespan's
background writers (cache-invalidation bump flush, ring heartbeat, last-used
coalescer, ...). Every one of those writers is an aiosqlite round trip per
statement, so a write transaction that has executed its INSERT but not yet its
COMMIT needs the loop to run before it can release SQLite's single writer slot.
Freezing the loop therefore freezes the holder, and every write the portal
lifespan performs at startup (encryption-key fingerprint stamp, hard-sticky
outage-grace seed) waits the full 30 s ``busy_timeout`` before failing with
``database is locked`` (issue #1949). Retrying or ``BEGIN IMMEDIATE`` on the
startup path cannot help: the holder is frozen precisely because the caller is
blocked on it.

The helper keeps the loop alive by entering and exiting the client from a
worker thread, and flushes the deferred writers the loop owns before handing
the client over so the test body's blocking ``client.*`` calls do not race a
flush that would otherwise start a moment later.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from starlette.testclient import TestClient
from starlette.types import ASGIApp

from app.core.cache.invalidation import get_cache_invalidation_poller

_DRAIN_TIMEOUT_SECONDS = 5.0


async def flush_deferred_sqlite_writers(app: ASGIApp) -> None:
    """Complete the loop-owned SQLite writes that are deferred from the request path.

    Detached request-log / settlement persistence is drained the same way the
    ``async_client`` response hook drains it; the cache-invalidation poller's
    pending bumps are flushed now instead of on its next tick so no write can
    start on this loop while the caller is blocked in a portal call.
    """
    service = getattr(getattr(app, "state", None), "proxy_service", None)
    if service is not None and hasattr(service, "drain_persistence_tasks"):
        await service.drain_persistence_tasks(timeout_seconds=_DRAIN_TIMEOUT_SECONDS)
    poller = get_cache_invalidation_poller()
    if poller is not None:
        # The poller has no public "flush now"; its tick is `_flush_pending_bumps`
        # followed by the version read, and only the flush writes.
        await poller._flush_pending_bumps()


@asynccontextmanager
async def off_loop_test_client(app: ASGIApp, **client_kwargs: Any) -> AsyncIterator[TestClient]:
    """``async with off_loop_test_client(app) as client:`` for async tests.

    Drop-in for ``with TestClient(app) as client:``; the yielded client is the
    ordinary blocking ``TestClient`` (its ``websocket_connect`` / ``get`` /
    ``post`` calls still block the loop briefly, which is safe once nothing the
    loop owns is mid-transaction).
    """
    await flush_deferred_sqlite_writers(app)
    client = TestClient(app, **client_kwargs)
    await asyncio.to_thread(client.__enter__)
    try:
        yield client
    finally:
        await asyncio.to_thread(client.__exit__, None, None, None)

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

Keeping the test loop alive exposes a second cross-loop hazard, so the nested
lifespan runs behind :class:`KeepPortalLoopAwake`: the app's process-global
``anyio.Lock`` singletons (settings cache, SQLite writer section, account and
rate-limit caches, ...) are now shared by two *running* loops. anyio hands lock
ownership to the next waiter by resolving that waiter's future from the
releasing task's thread; when the releaser runs on the test loop and the waiter
on the portal loop, the wake-up is queued through a non-thread-safe
``call_soon`` and the portal loop, asleep in ``select`` with nothing scheduled,
never notices. The nested lifespan then hangs at startup and the test loop's
next acquire of the same lock deadlocks behind it (observed with the ring
heartbeat's ``settings_cache.get()`` against the nested startup's settings
read). A short periodic timer on the portal loop makes every such queued
wake-up run within one tick.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from starlette.testclient import TestClient
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.cache.invalidation import get_cache_invalidation_poller, set_cache_invalidation_poller

_DRAIN_TIMEOUT_SECONDS = 5.0
_PORTAL_TICK_SECONDS = 0.05


class KeepPortalLoopAwake:
    """ASGI wrapper that keeps the portal loop polling for the whole nested lifespan.

    Wake-ups queued on the portal loop from the test loop's thread (an
    ``anyio.Lock`` handoff from a process-global singleton, see the module
    docstring) are only executed once the portal loop wakes; the ticker
    guarantees that happens within ``_PORTAL_TICK_SECONDS``. Non-lifespan
    scopes pass straight through.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "lifespan":
            await self.app(scope, receive, send)
            return
        ticker = asyncio.create_task(_tick_forever(), name="off-loop-test-client-portal-ticker")
        try:
            await self.app(scope, receive, send)
        finally:
            ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ticker


async def _tick_forever() -> None:
    while True:
        await asyncio.sleep(_PORTAL_TICK_SECONDS)


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
    # The nested lifespan registers its own cache-invalidation poller and, on
    # shutdown, clears the registration it finds (its own). The outer lifespan's
    # poller keeps running but would be left unregistered, turning every later
    # bump in the test into a silent no-op; put it back once the nested client
    # is gone.
    outer_poller = get_cache_invalidation_poller()
    client = TestClient(KeepPortalLoopAwake(app), **client_kwargs)
    try:
        await _enter_off_loop(client)
        try:
            yield client
        finally:
            await asyncio.to_thread(client.__exit__, None, None, None)
    finally:
        if outer_poller is not None and get_cache_invalidation_poller() is None:
            set_cache_invalidation_poller(outer_poller)


async def _enter_off_loop(client: TestClient) -> None:
    """Run ``client.__enter__`` on a worker thread; never leave a lifespan behind.

    Cancelling the awaiting task (test timeout, cancelled test) stops the wait
    but not the worker thread, which would go on to finish startup and leave
    the portal lifespan and its background writers running into later tests.
    The startup call is shielded, awaited to completion on cancellation, and
    the fully-entered client is exited before the cancellation propagates.
    ``TestClient.__enter__`` tears its own portal down when startup raises.
    """
    enter = asyncio.ensure_future(asyncio.to_thread(client.__enter__))
    try:
        await asyncio.shield(enter)
    except asyncio.CancelledError:
        if not enter.done():
            await asyncio.wait({enter})
        if not enter.cancelled() and enter.exception() is None:
            await asyncio.to_thread(client.__exit__, None, None, None)
        raise

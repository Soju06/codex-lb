"""Pin the harness contract that keeps the shared test loop alive while a blocking
``TestClient`` lifespan starts on its portal thread (issue #1949)."""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.cache.invalidation import NAMESPACE_FIREWALL, get_cache_invalidation_poller
from app.db.models import CacheInvalidation, RuntimeSentinel
from app.db.session import SessionLocal
from tests.integration.off_loop_test_client import (
    KeepPortalLoopAwake,
    flush_deferred_sqlite_writers,
    off_loop_test_client,
)

# Well under the 30 s SQLite busy_timeout the deadlock used to burn before failing.
_STARTUP_DEADLINE_SECONDS = 15.0
_HOLD_SECONDS = 1.0
# Upper bound the dummy lifespan puts on a wake-up that arrives from a foreign
# thread: a loop that never ticks only notices it when this timer fires, so a
# broken ticker fails the timing assertion instead of hanging the test.
_FOREIGN_WAKEUP_BOUND_SECONDS = 3.0
_FOREIGN_WAKEUP_DELAY_SECONDS = 0.1


@pytest.mark.asyncio
async def test_off_loop_test_client_starts_while_loop_owned_write_transaction_is_open(async_client, app_instance):
    """A loop-owned write that only COMMITs after the loop runs must not stall portal startup.

    The holder mimics a background writer caught between INSERT and COMMIT at
    the moment the test enters the second lifespan: it releases only via a
    timer on this loop. With a blocking ``with TestClient(...)`` the timer could
    never fire and the portal lifespan's sentinel stamps waited out busy_timeout.
    """
    del async_client  # the first lifespan (and its background writers) live on this loop
    loop = asyncio.get_running_loop()
    insert_done = asyncio.Event()
    release = asyncio.Event()

    async def hold_write_transaction() -> None:
        async with SessionLocal() as session:
            session.add(RuntimeSentinel(name="test_off_loop_client_holder", value="held"))
            await session.flush()  # INSERT executed: the SQLite writer slot is held from here
            insert_done.set()
            await release.wait()
            await session.commit()

    holder = asyncio.create_task(hold_write_transaction())
    await insert_done.wait()
    loop.call_later(_HOLD_SECONDS, release.set)

    started = time.monotonic()
    async with off_loop_test_client(app_instance) as client:
        elapsed = time.monotonic() - started
        assert elapsed < _STARTUP_DEADLINE_SECONDS, f"portal lifespan startup took {elapsed:.1f}s"
        assert client.get("/health/live").status_code == 200

    await holder
    async with SessionLocal() as session:
        stored = await session.scalar(
            select(RuntimeSentinel.value).where(RuntimeSentinel.name == "test_off_loop_client_holder")
        )
    assert stored == "held"


@pytest.mark.asyncio
async def test_flush_deferred_sqlite_writers_writes_pending_cache_bumps_now(async_client, app_instance):
    del async_client
    poller = get_cache_invalidation_poller()
    assert poller is not None

    async def firewall_version() -> int:
        async with SessionLocal() as session:
            version = await session.scalar(
                select(CacheInvalidation.version).where(CacheInvalidation.namespace == NAMESPACE_FIREWALL)
            )
        return int(version or 0)

    before = await firewall_version()
    poller.request_bump(NAMESPACE_FIREWALL)

    await flush_deferred_sqlite_writers(app_instance)

    assert await firewall_version() == before + 1


@pytest.mark.asyncio
async def test_off_loop_test_client_restores_the_outer_cache_invalidation_poller(async_client, app_instance):
    """The nested lifespan clears the poller registration it finds on shutdown (its own).

    Without restoring the outer lifespan's poller, every ``bump_cache_invalidation``
    for the rest of the test would be a silent no-op against a still-running poller.
    """
    del async_client
    outer_poller = get_cache_invalidation_poller()
    assert outer_poller is not None

    async with off_loop_test_client(app_instance) as client:
        assert client.get("/health/live").status_code == 200
        nested_poller = get_cache_invalidation_poller()
        assert nested_poller is not None
        assert nested_poller is not outer_poller

    assert get_cache_invalidation_poller() is outer_poller


@pytest.mark.asyncio
async def test_off_loop_test_client_exits_the_client_when_startup_is_cancelled(async_client, app_instance, monkeypatch):
    """Cancelling the entering task must not leave the portal lifespan running."""
    del async_client
    outer_poller = get_cache_invalidation_poller()
    entering = threading.Event()
    exited: list[TestClient] = []
    original_enter = TestClient.__enter__
    original_exit = TestClient.__exit__

    def recording_enter(self):
        entering.set()
        return original_enter(self)

    def recording_exit(self, *args):
        exited.append(self)
        return original_exit(self, *args)

    monkeypatch.setattr(TestClient, "__enter__", recording_enter)
    monkeypatch.setattr(TestClient, "__exit__", recording_exit)

    async def use_client() -> None:
        async with off_loop_test_client(app_instance):
            pytest.fail("startup was cancelled; the body must not run")

    task = asyncio.create_task(use_client())
    assert await asyncio.to_thread(entering.wait, 10.0), "worker thread never reached TestClient.__enter__"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(exited) == 1
    assert exited[0].portal is None, "the portal lifespan was left running"
    assert get_cache_invalidation_poller() is outer_poller


@pytest.mark.asyncio
async def test_blocking_test_client_is_rejected_on_the_running_test_loop(app_instance):
    with pytest.raises(RuntimeError, match="off_loop_test_client"):
        with TestClient(app_instance):
            pytest.fail("the blocking client must not start a lifespan on the running loop")


def _foreign_thread_wakeup_lifespan_app():
    """ASGI app whose startup waits for a future that another thread resolves non-thread-safely.

    This is exactly how an ``anyio.Lock`` released on the test loop hands ownership
    to a waiter on the portal loop: ``fut.set_result`` from the wrong thread queues
    the wake-up without waking the loop.
    """
    ready = threading.Event()
    state: dict[str, object] = {}

    async def app(scope, receive, send) -> None:
        assert scope["type"] == "lifespan"
        await receive()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        state["future"] = future
        ready.set()
        # The bound timer is the only thing that wakes a bare portal loop; when it
        # fires, the queued wake-up and the timeout race, so treat both as "done".
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.shield(future), _FOREIGN_WAKEUP_BOUND_SECONDS)
        await send({"type": "lifespan.startup.complete"})
        await receive()
        await send({"type": "lifespan.shutdown.complete"})

    def resolve_from_foreign_thread() -> None:
        assert ready.wait(10.0)
        time.sleep(_FOREIGN_WAKEUP_DELAY_SECONDS)
        future = state["future"]
        assert isinstance(future, asyncio.Future)
        future.set_result(None)  # deliberately not call_soon_threadsafe

    return app, resolve_from_foreign_thread


def _startup_seconds(app) -> float:
    dummy_app, resolve = _foreign_thread_wakeup_lifespan_app()
    resolver = threading.Thread(target=resolve, daemon=True)
    started = time.monotonic()
    resolver.start()
    with TestClient(app(dummy_app)):
        elapsed = time.monotonic() - started
    resolver.join(10.0)
    return elapsed


def test_keep_portal_loop_awake_delivers_wakeups_queued_from_a_foreign_thread():
    # Control: a bare portal loop only notices the foreign wake-up when the
    # bound timer fires, which is how the nested lifespan hung in CI.
    assert _startup_seconds(lambda app: app) >= _FOREIGN_WAKEUP_BOUND_SECONDS * 0.8
    # With the ticker the queued wake-up runs on the next tick.
    assert _startup_seconds(KeepPortalLoopAwake) < _FOREIGN_WAKEUP_BOUND_SECONDS * 0.5


def test_blocking_test_client_still_works_from_sync_tests(app_instance):
    with TestClient(app_instance) as client:
        assert client.get("/health/live").status_code == 200

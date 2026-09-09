"""Pin the harness contract that keeps the shared test loop alive while a blocking
``TestClient`` lifespan starts on its portal thread (issue #1949)."""

from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.cache.invalidation import NAMESPACE_FIREWALL, get_cache_invalidation_poller
from app.db.models import CacheInvalidation, RuntimeSentinel
from app.db.session import SessionLocal
from tests.integration.off_loop_test_client import flush_deferred_sqlite_writers, off_loop_test_client

# Well under the 30 s SQLite busy_timeout the deadlock used to burn before failing.
_STARTUP_DEADLINE_SECONDS = 15.0
_HOLD_SECONDS = 1.0


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
async def test_blocking_test_client_is_rejected_on_the_running_test_loop(app_instance):
    with pytest.raises(RuntimeError, match="off_loop_test_client"):
        with TestClient(app_instance):
            pytest.fail("the blocking client must not start a lifespan on the running loop")


def test_blocking_test_client_still_works_from_sync_tests(app_instance):
    with TestClient(app_instance) as client:
        assert client.get("/health/live").status_code == 200

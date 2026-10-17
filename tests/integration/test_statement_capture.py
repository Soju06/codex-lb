from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from app.db.models import CacheInvalidation
from app.db.session import SessionLocal, engine
from tests.integration.statement_capture import capture_task_statements

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_capture_task_statements_ignores_pre_existing_tasks(db_setup):
    """Query-cost assertions must not count statements an ambient loop issues.

    The suite leaves background loops running against the same engine (the
    0.5 s cache-invalidation poll, the 10 s bridge ring heartbeat), so an
    unscoped ``before_cursor_execute`` listener turns an exact statement count
    into a coin flip. Those loops were started before the window opened, so
    they cannot carry its marker. The statement is awaited here rather than
    raced, so the demonstration is deterministic.
    """
    del db_setup

    started = asyncio.Event()
    finished = asyncio.Event()

    async def _ambient_loop() -> None:
        await started.wait()
        async with SessionLocal() as session:
            # A shape the measured task never issues, so its exclusion below
            # is attributable rather than a counting coincidence.
            await session.execute(select(func.count()).select_from(CacheInvalidation))
        finished.set()

    # Created *before* the window: this is what an ambient loop looks like.
    ambient = asyncio.create_task(_ambient_loop(), name="statement-capture-ambient-task")
    try:
        async with capture_task_statements(engine) as statements:
            async with SessionLocal() as session:
                await session.execute(select(CacheInvalidation.namespace))
                started.set()
                await finished.wait()
                await session.execute(select(CacheInvalidation.namespace))
    finally:
        started.set()
        await ambient

    assert not [statement for statement in statements if "count(" in statement]
    assert statements == ["select cache_invalidation.namespace from cache_invalidation"] * 2


@pytest.mark.asyncio
async def test_capture_task_statements_keeps_spawned_tasks(db_setup):
    """A read the measured code issues from a task it spawns is still its cost.

    Scoping on task identity alone would drop it, so moving -- or adding -- a
    query inside an awaited ``create_task`` would read as free. The window
    keeps the caller's lineage, not one task, so it does not.
    """
    del db_setup

    async def _statement_from_a_spawned_task() -> None:
        async with SessionLocal() as session:
            await session.execute(select(func.count()).select_from(CacheInvalidation))

    async with capture_task_statements(engine) as statements:
        await asyncio.create_task(
            _statement_from_a_spawned_task(),
            name="statement-capture-spawned-task",
        )

    assert statements == ["select count(*) as count_1 from cache_invalidation"]

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from app.db.models import CacheInvalidation
from app.db.session import SessionLocal, engine
from tests.integration.statement_capture import capture_task_statements

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_capture_task_statements_ignores_other_tasks(db_setup):
    """Query-cost assertions must not count statements a concurrent task issues.

    The suite leaves ambient background loops running against the same engine
    (the 0.5 s cache-invalidation poll, the 10 s bridge ring heartbeat), so an
    unscoped ``before_cursor_execute`` listener turns an exact statement count
    into a coin flip. This pins the scoping that keeps those counts exact: a
    statement another task issues *while the window is open* -- awaited here
    rather than raced, so the demonstration is deterministic -- is excluded,
    and the calling task's own statements are all kept.
    """
    del db_setup

    async def _statement_from_another_task() -> int:
        async with SessionLocal() as session:
            # A shape the measured task never issues, so its exclusion below
            # is attributable rather than a counting coincidence.
            result = await session.execute(select(func.count()).select_from(CacheInvalidation))
            return int(result.scalar_one())

    async with capture_task_statements(engine) as statements:
        async with SessionLocal() as session:
            await session.execute(select(CacheInvalidation.namespace))
            other_task_rows = await asyncio.create_task(
                _statement_from_another_task(),
                name="statement-capture-other-task",
            )
            await session.execute(select(CacheInvalidation.namespace))

    # The other task really did run a statement inside the window.
    assert other_task_rows >= 0
    assert not [statement for statement in statements if "count(" in statement]
    assert statements == ["select cache_invalidation.namespace from cache_invalidation"] * 2

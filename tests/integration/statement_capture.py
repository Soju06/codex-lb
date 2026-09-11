"""Task-scoped SQL capture for query-cost assertions.

``before_cursor_execute`` on the process-wide engine sees every statement the
process issues, not only the ones the request under test issues. Two ambient
background loops the suite deliberately leaves running write into the same
engine while a test is measuring:

* ``CacheInvalidationPoller`` polls ``cache_invalidation`` every 0.5 s, and
* the bridge ring membership heartbeat issues three statements every 10 s
  (a ``bridge_ring_members`` upsert, the member list, and a
  ``dashboard_settings`` load).

Either one landing inside a capture window inflates an exact statement count
by one (or three) and makes the assertion flake. Both run on their own asyncio
tasks, while an ``ASGITransport`` request runs inline on the calling task, so
dropping statements issued by another task excludes the ambient loops exactly
without weakening what the count proves.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine


def normalize_sql(statement: str) -> str:
    """Collapse whitespace and case so statements compare as shapes."""
    return " ".join(statement.split()).lower()


@asynccontextmanager
async def capture_task_statements(async_engine: AsyncEngine) -> AsyncIterator[list[str]]:
    """Collect the normalized SQL issued by the calling task inside the block."""
    own_task = asyncio.current_task()
    if own_task is None:  # pragma: no cover - the suite always runs inside a task
        raise RuntimeError("capture_task_statements() must be used from inside a task")
    statements: list[str] = []

    def _capture(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        try:
            if asyncio.current_task() is not own_task:
                return
        except RuntimeError:  # pragma: no cover - driver thread without a loop
            return
        statements.append(normalize_sql(statement))

    event.listen(async_engine.sync_engine, "before_cursor_execute", _capture)
    try:
        yield statements
    finally:
        event.remove(async_engine.sync_engine, "before_cursor_execute", _capture)

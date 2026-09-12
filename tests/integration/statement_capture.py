"""Caller-scoped SQL capture for query-cost assertions.

``before_cursor_execute`` on the process-wide engine sees every statement the
process issues, not only the ones the code under test issues. Two ambient
background loops the suite deliberately leaves running write into the same
engine while a test is measuring:

* ``CacheInvalidationPoller`` polls ``cache_invalidation`` every 0.5 s, and
* the bridge ring membership heartbeat issues three statements every 10 s
  (a ``bridge_ring_members`` upsert, the member list, and a
  ``dashboard_settings`` load).

Either one landing inside a capture window inflates an exact statement count
by one (or three) and makes the assertion flake.

The window therefore keeps a statement when the *caller's lineage* issued it --
the calling task, or any task spawned inside the window -- and drops it
otherwise. Lineage is carried by a ``ContextVar``: ``asyncio.create_task``
copies the current context, so a task the measured code spawns inherits the
window marker, while a loop that was already running when the window opened
cannot, its context having been copied before the marker existed.

Scoping on task *identity* instead would drop the ambient loops just as
exactly, but it would also drop a statement the measured code moved -- or
added -- inside a child task, which is precisely the cost regression these
counts exist to catch. Filtering on statement *shape* would not work at all
here: the ring heartbeat's ``dashboard_settings`` load is textually identical
to the one an overview poll issues.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

_CAPTURE_WINDOW: ContextVar[object | None] = ContextVar("statement_capture_window", default=None)


def normalize_sql(statement: str) -> str:
    """Collapse whitespace and case so statements compare as shapes."""
    return " ".join(statement.split()).lower()


@asynccontextmanager
async def capture_task_statements(async_engine: AsyncEngine) -> AsyncIterator[list[str]]:
    """Collect the normalized SQL the calling task and its child tasks issue."""
    window = object()
    token = _CAPTURE_WINDOW.set(window)
    statements: list[str] = []

    def _capture(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if _CAPTURE_WINDOW.get() is not window:
            return
        statements.append(normalize_sql(statement))

    event.listen(async_engine.sync_engine, "before_cursor_execute", _capture)
    try:
        yield statements
    finally:
        event.remove(async_engine.sync_engine, "before_cursor_execute", _capture)
        _CAPTURE_WINDOW.reset(token)

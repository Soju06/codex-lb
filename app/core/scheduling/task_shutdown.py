"""Bounded graceful stopping for periodic background tasks."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.utils.shared_future import wait_on_shared_future

logger = logging.getLogger(__name__)

# How long a stopping task may take to finish the unit of work it is in the
# middle of (typically one DB transaction) before it is cancelled. Loops wait
# on their stop event between ticks, so an idle task exits immediately; the
# grace only applies to a task caught mid-tick.
DATABASE_TASK_STOP_GRACE_SECONDS = 2.0


def _describe(task: asyncio.Task[Any]) -> str:
    coro = task.get_coro()
    return getattr(coro, "__qualname__", None) or task.get_name()


async def stop_task_after_grace(task: asyncio.Task[Any]) -> None:
    """Let a task finish its current unit of work, cancelling it only as a fallback.

    Callers set the task's stop event first. Cancelling a task while it is
    inside database work interrupts SQLAlchemy's connection cleanup (on SQLite
    the pool logs ``Exception closing connection``) and can leave rows such as
    the leader lease unreleased.
    """
    # wait_on_shared_future never cancels ``task`` itself and absorbs a
    # level-cancelled caller's repeated cancels (see scripts/check_cancellation_safety.py).
    try:
        await wait_on_shared_future(task, timeout=DATABASE_TASK_STOP_GRACE_SECONDS)
        return
    except TimeoutError:
        pass
    except asyncio.CancelledError:
        if task.cancelled():
            return
        raise
    logger.warning(
        "Background task still busy %.1fs after stop was requested; cancelling task=%s",
        DATABASE_TASK_STOP_GRACE_SECONDS,
        _describe(task),
    )
    task.cancel()
    try:
        await wait_on_shared_future(task)
    except asyncio.CancelledError:
        if not task.cancelled():
            raise

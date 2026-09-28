"""Bounded graceful stopping for periodic background tasks."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core import shutdown as shutdown_state
from app.core.utils.shared_future import wait_on_shared_future

logger = logging.getLogger(__name__)

# How long a stopping task may take to finish the unit of work it is in the
# middle of (typically one DB transaction) before it is cancelled. Loops wait
# on their stop event between ticks, so an idle task exits immediately; the
# grace only applies to a task caught mid-tick.
DATABASE_TASK_STOP_GRACE_SECONDS = 2.0

_undrained: set[asyncio.Task[Any]] = set()


def _wait_budget_seconds() -> float:
    """How long one stop wait may take: the grace, capped by the drain time left.

    The stops draw only on what is left of the shared *drain* deadline, never on
    the post-drain cleanup reserve that follows it (POST_DRAIN_CLEANUP_TIMEOUT_SECONDS,
    25s in app/core/server.py). That reserve is sized for the steps after the
    stops: the leader-lease release (10s), the metrics-server wait (5s) and
    close_db()'s bounded teardown drain (2 x 5s); the server force-exits once it
    is spent. Outside a server shutdown (no shared deadline) the full grace applies.
    """
    remaining = shutdown_state.remaining_drain_timeout_seconds()
    if remaining is None:
        return DATABASE_TASK_STOP_GRACE_SECONDS
    return max(0.0, min(DATABASE_TASK_STOP_GRACE_SECONDS, remaining))


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
    # Each wait is capped by the shared shutdown budget (see _wait_budget_seconds),
    # so the scheduler stops, which run sequentially, cannot push the process
    # past its forced-exit deadline before lease release and DB disposal.
    grace = _wait_budget_seconds()
    try:
        await wait_on_shared_future(task, timeout=grace)
        return
    except TimeoutError:
        pass
    except asyncio.CancelledError:
        if task.cancelled():
            return
        raise
    logger.warning(
        "Background task still busy %.1fs after stop was requested; cancelling task=%s",
        grace,
        _describe(task),
    )
    task.cancel()
    cancel_wait = _wait_budget_seconds()
    try:
        await wait_on_shared_future(task, timeout=cancel_wait)
    except TimeoutError:
        # The task is deferring cancellation (e.g. inside shielded DB cleanup).
        # Do not block shutdown on it, but keep it visible: while it runs, the
        # shutdown must not be recorded as clean (see undrained_tasks()).
        logger.warning(
            "Background task still running %.1fs after cancellation; leaving it tracked task=%s",
            cancel_wait,
            _describe(task),
        )
        _undrained.add(task)
        task.add_done_callback(_undrained.discard)
    except asyncio.CancelledError:
        if not task.cancelled():
            raise


def undrained_tasks() -> frozenset[asyncio.Task[Any]]:
    """Stopped tasks that were still running after cancellation and its bounded wait."""

    return frozenset(task for task in _undrained if not task.done())

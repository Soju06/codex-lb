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


async def stop_task_after_grace(task: asyncio.Task[Any], *, await_cancellation: bool = False) -> None:
    """Let a task finish its current unit of work, cancelling it only as a fallback.

    Callers set the task's stop event first. Cancelling a task while it is
    inside database work interrupts SQLAlchemy's connection cleanup (on SQLite
    the pool logs ``Exception closing connection``) and can leave rows such as
    the leader lease unreleased.

    ``await_cancellation=True`` is for a stop that runs inside its own bounded
    deadline and whose caller relies on the task having fully finished (the
    leader-lease keeper inside ``release()``, bounded by the 10s release
    deadline): the grace is the plain grace rather than the drain-capped one,
    and after cancelling, the task is awaited until it is done. Otherwise the
    grace is capped by the drain time left and the post-cancel wait by the
    plain grace, after which a still-running task is tracked.
    """
    # One loop turn first: a loop idling on its stop event exits here, so it is
    # neither warned about nor cancelled even when no drain time is left.
    await asyncio.sleep(0)
    if task.done():
        if not task.cancelled():
            task.result()
        return
    # wait_on_shared_future never cancels ``task`` itself and absorbs a
    # level-cancelled caller's repeated cancels (see scripts/check_cancellation_safety.py).
    grace = DATABASE_TASK_STOP_GRACE_SECONDS if await_cancellation else _wait_budget_seconds()
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
    try:
        if await_cancellation:
            await wait_on_shared_future(task)
            return
        # The plain grace, not the drain budget: a promptly cancelled task is
        # awaited to completion, so stop order in app/main.py still holds (the
        # cache poller stops only after the model scheduler has finished).
        await wait_on_shared_future(task, timeout=DATABASE_TASK_STOP_GRACE_SECONDS)
    except TimeoutError:
        # The task is deferring cancellation (e.g. inside shielded DB cleanup).
        # Do not block shutdown on it, but keep it visible: while it runs, the
        # shutdown must not be recorded as clean (see undrained_tasks()).
        logger.warning(
            "Background task still running %.1fs after cancellation; leaving it tracked task=%s",
            DATABASE_TASK_STOP_GRACE_SECONDS,
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

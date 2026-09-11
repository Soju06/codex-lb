"""Bounded retry for the startup writes that race SQLite's single writer slot.

Startup stamps two insert-if-absent sentinels into ``runtime_sentinels``
(the encryption-key fingerprint and the hard-sticky outage-grace marker).
Both are the first statement of their transaction on a fresh connection, so
neither can be protected by ``BEGIN IMMEDIATE`` (there is no earlier read to
upgrade), and on a loaded host either can lose the writer slot and surface
``database is locked`` (issue #1949). The window is short and self-healing,
so a small bounded retry is the whole fix; it is shared here so both call
sites keep one budget and one diagnostic instead of drifting apart.

The diagnostic matters as much as the retry: ``database is locked`` is
emitted for two opposite mechanisms that only ``sqlite_errorname``
separates. ``SQLITE_BUSY_SNAPSHOT`` (a stale WAL snapshot that cannot be
upgraded) returns immediately and a retry on a fresh transaction fixes it;
``SQLITE_BUSY`` returns only after the full ``busy_timeout``, meaning
another writer held the slot that long and the retry budget is irrelevant.
Logging the name plus the elapsed time makes the next occurrence
self-classifying.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy.exc import OperationalError

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Three attempts inside ~0.35s: long enough to outlast the sub-second
# snapshot-upgrade class, short enough that a genuinely wedged writer slot
# still surfaces at startup instead of being hidden behind a long sleep.
SQLITE_LOCK_RETRY_DELAYS_SECONDS = (0.05, 0.1, 0.2)


def is_sqlite_lock_error(exc: OperationalError) -> bool:
    """Return True for a transient SQLite ``database is locked``/``busy``."""
    message = str(exc.orig if exc.orig is not None else exc).lower()
    return "database is locked" in message or "database is busy" in message


def sqlite_error_name(exc: OperationalError) -> str | None:
    """Return the driver's extended result code name, when the driver set one.

    ``sqlite3`` populates ``sqlite_errorname`` only on exceptions it raises
    itself, so a constructed ``OperationalError`` has no such attribute:
    read it defensively rather than with ``hasattr``-guarded access.
    """
    return getattr(exc.orig, "sqlite_errorname", None)


async def retry_on_sqlite_lock(
    operation: Callable[[], Awaitable[T]],
    *,
    what: str,
    before_retry: Callable[[], Awaitable[None]] | None = None,
) -> T:
    """Run ``operation``, retrying transient SQLite lock failures.

    Non-lock ``OperationalError``s propagate untouched, and so does a lock
    failure that survives the whole budget — the caller's existing
    failure semantics are preserved either way. ``before_retry`` runs
    between attempts for callers that must reset a session whose
    transaction the failure left dirty.
    """
    started_at = time.monotonic()
    for delay_seconds in (*SQLITE_LOCK_RETRY_DELAYS_SECONDS, None):
        attempt_started_at = time.monotonic()
        try:
            return await operation()
        except OperationalError as exc:
            if not is_sqlite_lock_error(exc):
                raise
            attempt_seconds = time.monotonic() - attempt_started_at
            total_seconds = time.monotonic() - started_at
            if delay_seconds is None:
                logger.warning(
                    "sqlite lock retry budget exhausted what=%s sqlite_errorname=%s "
                    "attempt_seconds=%.3f total_seconds=%.3f",
                    what,
                    sqlite_error_name(exc),
                    attempt_seconds,
                    total_seconds,
                )
                raise
            logger.debug(
                "retrying sqlite lock failure what=%s sqlite_errorname=%s "
                "attempt_seconds=%.3f total_seconds=%.3f next_delay_seconds=%.3f",
                what,
                sqlite_error_name(exc),
                attempt_seconds,
                total_seconds,
                delay_seconds,
            )
            if before_retry is not None:
                await before_retry()
            await asyncio.sleep(delay_seconds)
    raise AssertionError("unreachable: the retry loop either returns or raises")

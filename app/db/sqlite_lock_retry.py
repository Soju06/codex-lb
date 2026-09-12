"""The single place SQLite write-lock contention is classified and reported.

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

Every site that classifies a SQLite lock failure routes through
:func:`is_sqlite_lock_error` here, so one message set describes "transient
write-lock contention" process-wide instead of each module carrying its own
drifting substring list. Sites that keep their own hand-rolled retry budget
(the API-key usage-reservation writes and the refresh-claim upsert) call
:func:`should_retry_after_sqlite_lock` for the predicate, the diagnostic and
the backoff while keeping their own attempt count and their own re-raise;
sites that deliberately fail fast and let their next tick retry (leader
election's best-effort shutdown lease writes) call the predicate directly.
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


# Every message SQLite emits for write-lock contention, matched
# case-insensitively. ``database is locked`` / ``database is busy`` are
# SQLITE_BUSY (and SQLITE_BUSY_SNAPSHOT, which reuses the same prose);
# ``database table is locked`` / ``database schema is locked`` are
# SQLITE_LOCKED, the same contention reported against a table or the schema
# cookie rather than the file; ``busy_snapshot`` catches a driver or wrapper
# that surfaces the extended result-code name instead of the prose. All of
# them mean "another writer holds the slot, try again", so every site treats
# the whole set alike rather than each keeping its own subset.
_SQLITE_LOCK_MESSAGE_FRAGMENTS = (
    "database is locked",
    "database is busy",
    "database table is locked",
    "database schema is locked",
    "busy_snapshot",
)


def is_sqlite_lock_error(exc: BaseException) -> bool:
    """Return True for a transient SQLite write-lock failure.

    Only an ``OperationalError`` qualifies, and only the DRIVER exception's
    message is inspected — ``str(OperationalError)`` also renders the failing
    statement and its bound parameters, so matching on it would classify an
    unrelated failure as transient whenever the SQL or a parameter value
    happened to contain the lock text. A wrapper with no ``orig`` carries no
    driver evidence at all, so it is not contention either: SQLAlchemy raises
    those itself, and every lock failure we retry comes from the driver.
    """
    if not isinstance(exc, OperationalError) or exc.orig is None:
        return False
    message = str(exc.orig).lower()
    return any(fragment in message for fragment in _SQLITE_LOCK_MESSAGE_FRAGMENTS)


def sqlite_error_name(exc: BaseException) -> str | None:
    """Return the driver's extended result code name, when the driver set one.

    ``sqlite3`` populates ``sqlite_errorname`` only on exceptions it raises
    itself, so a constructed ``OperationalError`` has no such attribute:
    read it defensively rather than with ``hasattr``-guarded access.
    """
    driver_exc = exc.orig if isinstance(exc, OperationalError) else exc
    return getattr(driver_exc, "sqlite_errorname", None)


def _report_lock_attempt(exc: BaseException, *, what: str, giving_up: bool, **extra: object) -> None:
    """Emit the one report format both retry helpers share.

    Each helper reports the same two facts — which write contended and which
    mechanism the driver named — and differs only in the attempt/timing fields
    it can supply, which are appended as trailing ``key=value`` pairs. Emitting
    from here gives that format a single owner, so a field added for one helper
    cannot silently drift from the other's. The helpers keep their own budgets;
    only the report is shared.
    """
    trailer = "".join(f" {name}={value}" for name, value in extra.items())
    logger.log(
        logging.WARNING if giving_up else logging.DEBUG,
        "%s what=%s sqlite_errorname=%s%s",
        "sqlite lock retry budget exhausted" if giving_up else "retrying sqlite lock failure",
        what,
        sqlite_error_name(exc),
        trailer,
    )


async def should_retry_after_sqlite_lock(
    exc: BaseException,
    *,
    what: str,
    attempt: int,
    max_attempts: int,
    base_delay_seconds: float,
) -> bool:
    """Classify one failed attempt of a caller-owned SQLite lock retry loop.

    Returns ``True`` — after awaiting the attempt's exponential backoff — when
    the caller should try again, and ``False`` when it must re-raise, either
    because the failure is not transient write-lock contention or because the
    caller's budget is spent. The caller keeps its own attempt count and its
    own ``raise``; only the predicate, the diagnostic and the backoff shape
    are shared, so no call site's outcome changes.
    """
    if not is_sqlite_lock_error(exc):
        return False
    if attempt >= max_attempts - 1:
        _report_lock_attempt(exc, what=what, giving_up=True, attempts=max_attempts)
        return False
    _report_lock_attempt(exc, what=what, giving_up=False, attempt=attempt)
    await asyncio.sleep(base_delay_seconds * (2**attempt))
    return True


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
                _report_lock_attempt(
                    exc,
                    what=what,
                    giving_up=True,
                    attempt_seconds=f"{attempt_seconds:.3f}",
                    total_seconds=f"{total_seconds:.3f}",
                )
                raise
            _report_lock_attempt(
                exc,
                what=what,
                giving_up=False,
                attempt_seconds=f"{attempt_seconds:.3f}",
                total_seconds=f"{total_seconds:.3f}",
                next_delay_seconds=f"{delay_seconds:.3f}",
            )
            if before_retry is not None:
                await before_retry()
            await asyncio.sleep(delay_seconds)
    raise AssertionError("unreachable: the retry loop either returns or raises")

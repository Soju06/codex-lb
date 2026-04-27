from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar

from sqlalchemy import exc as sa_exc

_T = TypeVar("_T")

SQLITE_LOCK_RETRY_DELAYS_SECONDS: tuple[float, ...] = (0.05, 0.1, 0.2)
SQLITE_LOCK_RETRY_ATTEMPTS = len(SQLITE_LOCK_RETRY_DELAYS_SECONDS) + 1

_SQLITE_LOCK_MARKERS = (
    "database is locked",
    "database table is locked",
    "database is busy",
)


def is_sqlite_lock_error(exc: BaseException) -> bool:
    checked: set[int] = set()
    stack: list[BaseException] = [exc]
    while stack:
        current = stack.pop()
        if id(current) in checked:
            continue
        checked.add(id(current))

        message = str(current).lower()
        if isinstance(current, sqlite3.OperationalError) and _has_sqlite_lock_marker(message):
            return True
        if isinstance(current, sa_exc.OperationalError) and _has_sqlite_lock_marker(message):
            return True

        for attr in ("orig", "__cause__", "__context__"):
            nested = getattr(current, attr, None)
            if isinstance(nested, BaseException):
                stack.append(nested)
    return False


async def retry_sqlite_lock(
    operation: Callable[[], Awaitable[_T]],
    *,
    operation_name: str,
    on_retry: Callable[[], Awaitable[None]] | None = None,
    delays_seconds: Sequence[float] = SQLITE_LOCK_RETRY_DELAYS_SECONDS,
    logger: logging.Logger | None = None,
) -> _T:
    attempts = len(delays_seconds) + 1
    for attempt_index in range(attempts):
        try:
            return await operation()
        except Exception as exc:
            if not is_sqlite_lock_error(exc):
                raise
            if attempt_index == attempts - 1:
                if logger is not None:
                    logger.error(
                        "SQLite write lock retry exhausted operation=%s attempts=%s",
                        operation_name,
                        attempts,
                    )
                raise
            if on_retry is not None:
                await on_retry()
            delay = delays_seconds[attempt_index]
            if logger is not None:
                logger.warning(
                    "retrying SQLite write after lock operation=%s attempt=%s/%s delay_seconds=%.3f",
                    operation_name,
                    attempt_index + 1,
                    attempts,
                    delay,
                )
            await asyncio.sleep(delay)
    raise RuntimeError(f"SQLite lock retry exhausted without result: {operation_name}")


def _has_sqlite_lock_marker(message: str) -> bool:
    return any(marker in message for marker in _SQLITE_LOCK_MARKERS)

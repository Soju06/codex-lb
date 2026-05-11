from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar, cast

from sqlalchemy import exc as sa_exc
from sqlalchemy.ext.asyncio import AsyncSession

_T = TypeVar("_T")

SQLITE_LOCK_RETRY_DELAYS_SECONDS: tuple[float, ...] = (0.05, 0.1, 0.2)
SQLITE_LOCK_RETRY_ATTEMPTS = len(SQLITE_LOCK_RETRY_DELAYS_SECONDS) + 1

_SQLITE_LOCK_MARKERS = (
    "database is locked",
    "database table is locked",
    "database is busy",
)
_SQLITE_WRITE_LOCK = asyncio.Lock()
_RETRY_SENTINEL = object()


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
    serialize_writes: bool = False,
) -> _T:
    attempts = len(delays_seconds) + 1
    for attempt_index in range(attempts):
        if serialize_writes:
            async with _SQLITE_WRITE_LOCK:
                result = await _run_retryable_attempt(
                    operation,
                    operation_name=operation_name,
                    attempt_index=attempt_index,
                    attempts=attempts,
                    on_retry=on_retry,
                    delays_seconds=delays_seconds,
                    logger=logger,
                )
        else:
            result = await _run_retryable_attempt(
                operation,
                operation_name=operation_name,
                attempt_index=attempt_index,
                attempts=attempts,
                on_retry=on_retry,
                delays_seconds=delays_seconds,
                logger=logger,
            )
        if result is _RETRY_SENTINEL:
            await asyncio.sleep(delays_seconds[attempt_index])
            continue
        return cast(_T, result)
    raise RuntimeError(f"SQLite lock retry exhausted without result: {operation_name}")


async def _run_retryable_attempt(
    operation: Callable[[], Awaitable[_T]],
    *,
    operation_name: str,
    attempt_index: int,
    attempts: int,
    on_retry: Callable[[], Awaitable[None]] | None,
    delays_seconds: Sequence[float],
    logger: logging.Logger | None,
) -> _T | object:
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
        return _RETRY_SENTINEL


async def retry_sqlite_write(
    session: AsyncSession,
    operation: Callable[[], Awaitable[_T]],
    *,
    operation_name: str,
    delays_seconds: Sequence[float] = SQLITE_LOCK_RETRY_DELAYS_SECONDS,
    logger: logging.Logger | None = None,
) -> _T:
    async def _rollback() -> None:
        with contextlib.suppress(BaseException):
            await session.rollback()

    try:
        return await retry_sqlite_lock(
            operation,
            operation_name=operation_name,
            on_retry=_rollback,
            delays_seconds=delays_seconds,
            logger=logger,
            serialize_writes=session_uses_sqlite(session),
        )
    except BaseException:
        await _rollback()
        raise


def session_uses_sqlite(session: AsyncSession) -> bool:
    try:
        bind = session.get_bind()
    except (AttributeError, TypeError):
        return False
    dialect_name = getattr(getattr(bind, "dialect", None), "name", None)
    return dialect_name == "sqlite"


def _has_sqlite_lock_marker(message: str) -> bool:
    return any(marker in message for marker in _SQLITE_LOCK_MARKERS)

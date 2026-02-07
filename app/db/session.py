from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import AsyncIterator, Awaitable, TypeVar

import anyio
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config.settings import get_settings
from app.db.migrations import run_migrations
from app.db.sqlite_utils import check_sqlite_integrity, sqlite_db_path_from_url

_settings = get_settings()

logger = logging.getLogger(__name__)

_SQLITE_BUSY_TIMEOUT_MS = 5_000
_SQLITE_BUSY_TIMEOUT_SECONDS = _SQLITE_BUSY_TIMEOUT_MS / 1000


def _is_sqlite_url(url: str) -> bool:
    return url.startswith("sqlite+aiosqlite:///") or url.startswith("sqlite:///")


def _is_sqlite_memory_url(url: str) -> bool:
    return _is_sqlite_url(url) and ":memory:" in url


def _configure_sqlite_engine(engine: Engine, *, enable_wal: bool) -> None:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection: sqlite3.Connection, _: object) -> None:
        cursor: sqlite3.Cursor = dbapi_connection.cursor()
        try:
            if enable_wal:
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
        finally:
            cursor.close()


if _is_sqlite_url(_settings.database_url):
    is_sqlite_memory = _is_sqlite_memory_url(_settings.database_url)
    if is_sqlite_memory:
        engine = create_async_engine(
            _settings.database_url,
            echo=False,
            connect_args={"timeout": _SQLITE_BUSY_TIMEOUT_SECONDS},
        )
    else:
        engine = create_async_engine(
            _settings.database_url,
            echo=False,
            pool_size=_settings.database_pool_size,
            max_overflow=_settings.database_max_overflow,
            pool_timeout=_settings.database_pool_timeout_seconds,
            connect_args={"timeout": _SQLITE_BUSY_TIMEOUT_SECONDS},
        )
    _configure_sqlite_engine(engine.sync_engine, enable_wal=not is_sqlite_memory)
else:
    engine = create_async_engine(
        _settings.database_url,
        echo=False,
        pool_size=_settings.database_pool_size,
        max_overflow=_settings.database_max_overflow,
        pool_timeout=_settings.database_pool_timeout_seconds,
    )

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

_T = TypeVar("_T")


def _ensure_sqlite_dir(url: str) -> None:
    if not (url.startswith("sqlite+aiosqlite:") or url.startswith("sqlite:")):
        return

    marker = ":///"
    marker_index = url.find(marker)
    if marker_index < 0:
        return

    # Works for both relative (sqlite+aiosqlite:///./db.sqlite) and absolute
    # paths (sqlite+aiosqlite:////var/lib/app/db.sqlite).
    path = url[marker_index + len(marker) :]
    path = path.partition("?")[0]
    path = path.partition("#")[0]

    if not path or path == ":memory:":
        return

    Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)


async def _shielded(awaitable: Awaitable[_T]) -> _T:
    with anyio.CancelScope(shield=True):
        return await awaitable


async def _safe_rollback(session: AsyncSession) -> None:
    if not session.in_transaction():
        return
    try:
        await _shielded(session.rollback())
    except BaseException:
        return


async def _safe_close(session: AsyncSession) -> None:
    try:
        await _shielded(session.close())
    except BaseException:
        return


async def get_session() -> AsyncIterator[AsyncSession]:
    session = SessionLocal()
    try:
        yield session
    except BaseException:
        await _safe_rollback(session)
        raise
    finally:
        if session.in_transaction():
            await _safe_rollback(session)
        await _safe_close(session)


async def init_db() -> None:
    from app.db.models import Base

    _ensure_sqlite_dir(_settings.database_url)
    sqlite_path = sqlite_db_path_from_url(_settings.database_url)
    if sqlite_path is not None:
        integrity = check_sqlite_integrity(sqlite_path)
        if not integrity.ok:
            details = integrity.details or "unknown error"
            logger.error("SQLite integrity check failed path=%s details=%s", sqlite_path, details)
            if "locked" in details.lower():
                message = (
                    f"SQLite integrity check failed for {sqlite_path} ({details}). "
                    "Another instance may be running. Stop it and retry."
                )
            else:
                message = (
                    f"SQLite integrity check failed for {sqlite_path} ({details}). "
                    "The database appears corrupted or the filesystem is unhealthy. "
                    "Stop the app and run "
                    f'`python -m app.db.recover --db "{sqlite_path}" --replace` '
                    "or restore a backup from the same directory."
                )
            raise RuntimeError(message)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session:
        try:
            updated = await run_migrations(session)
            if updated:
                logger.info("Applied database migrations count=%s", updated)
        except Exception:
            logger.exception("Failed to apply database migrations")
            if get_settings().database_migrations_fail_fast:
                raise


async def close_db() -> None:
    await engine.dispose()

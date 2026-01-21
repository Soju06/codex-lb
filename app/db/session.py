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

DATABASE_URL = get_settings().database_url

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


if _is_sqlite_url(DATABASE_URL):
    engine = create_async_engine(
        DATABASE_URL,
        echo=False,
        connect_args={"timeout": _SQLITE_BUSY_TIMEOUT_SECONDS},
    )
    _configure_sqlite_engine(engine.sync_engine, enable_wal=not _is_sqlite_memory_url(DATABASE_URL))
else:
    engine = create_async_engine(DATABASE_URL, echo=False)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

_T = TypeVar("_T")


def _ensure_sqlite_dir(url: str) -> None:
    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix):
        return
    path = url[len(prefix) :]
    if path == ":memory:":
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

    _ensure_sqlite_dir(DATABASE_URL)

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

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config.settings import get_settings

DATABASE_URL = get_settings().database_url

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


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


async def _safe_rollback(session: AsyncSession) -> None:
    if not session.in_transaction():
        return
    try:
        await asyncio.shield(session.rollback())
    except Exception:
        return


async def _safe_close(session: AsyncSession) -> None:
    try:
        await asyncio.shield(session.close())
    except Exception:
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
        if DATABASE_URL.startswith("sqlite"):
            await _ensure_sqlite_request_logs_columns(conn)


async def _ensure_sqlite_request_logs_columns(conn) -> None:
    result = await conn.execute(text("PRAGMA table_info(request_logs)"))
    columns = {row[1] for row in result.fetchall()}
    if "reasoning_effort" not in columns:
        await conn.execute(text("ALTER TABLE request_logs ADD COLUMN reasoning_effort VARCHAR"))

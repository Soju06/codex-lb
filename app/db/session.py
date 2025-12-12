from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config.settings import get_settings

DATABASE_URL = get_settings().database_url

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def _ensure_sqlite_dir(url: str) -> None:
    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix):
        return
    path = url[len(prefix) :]
    if path == ":memory:":
        return
    Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            if session.in_transaction():
                await session.rollback()


async def init_db() -> None:
    from app.db.models import Base

    _ensure_sqlite_dir(DATABASE_URL)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

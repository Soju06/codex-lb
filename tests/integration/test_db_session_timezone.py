from __future__ import annotations

import os
import re

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db import session as session_module

pytestmark = pytest.mark.integration


def _postgres_test_url() -> str:
    url = os.environ.get("CODEX_LB_TEST_DATABASE_URL")
    if not url or not url.startswith("postgresql+asyncpg://"):
        pytest.skip("requires CODEX_LB_TEST_DATABASE_URL=postgresql+asyncpg://...")
    return url


async def _current_database_name(url: str) -> str:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            name = await conn.scalar(text("SELECT current_database()"))
    finally:
        await engine.dispose()
    assert isinstance(name, str)
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        pytest.skip(f"test database name is not safe to quote in ALTER DATABASE: {name!r}")
    return name


async def _set_database_timezone_default(url: str, database_name: str, timezone: str | None) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            if timezone is None:
                await conn.execute(text(f'ALTER DATABASE "{database_name}" RESET timezone'))
            else:
                await conn.execute(text(f"ALTER DATABASE \"{database_name}\" SET timezone TO '{timezone}'"))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_asyncpg_engine_pins_session_timezone_to_utc_despite_database_default() -> None:
    """Regression: asyncpg interprets naive datetimes using the session time zone.

    Force the test database default away from UTC, then create a brand-new engine
    through the application's PostgreSQL engine kwargs. Without
    ``server_settings.timezone=UTC`` this connection inherits the database
    default and the assertion below fails on the PostgreSQL CI job.
    """

    url = _postgres_test_url()
    database_name = await _current_database_name(url)

    try:
        await _set_database_timezone_default(url, database_name, "Europe/Amsterdam")
        engine = create_async_engine(
            url,
            **session_module._postgres_async_engine_kwargs(url),
        )
        try:
            async with engine.connect() as conn:
                timezone = await conn.scalar(text("SHOW TIME ZONE"))
        finally:
            await engine.dispose()
    finally:
        await _set_database_timezone_default(url, database_name, None)

    assert timezone == "UTC"

"""Disposable migration databases, including the explicit PostgreSQL reset guard."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config.settings import get_settings


@pytest.fixture(params=["sqlite", "postgresql"])
async def migration_url(request, tmp_path):
    if request.param == "sqlite":
        return f"sqlite+aiosqlite:///{tmp_path / 'reset-pool.db'}"
    url = get_settings().database_url
    if not url.startswith("postgresql+"):
        pytest.skip("Requires CODEX_LB_TEST_DATABASE_URL pointing to a disposable PostgreSQL database")
    if url != os.environ.get("CODEX_LB_TEST_DATABASE_URL") or make_url(url).database != "codex_lb_test":
        pytest.fail(
            "Refusing to reset PostgreSQL: configure CODEX_LB_TEST_DATABASE_URL to the dedicated codex_lb_test database"
        )
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DROP SCHEMA public CASCADE"))
            await connection.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()
    return url

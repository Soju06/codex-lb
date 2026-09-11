from __future__ import annotations

import pytest
from alembic import command
from anyio import to_thread
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.migrate import _build_alembic_config, run_upgrade

pytestmark = pytest.mark.integration

_PARENT_REVISION = "20260911_030000_add_local_login_policy"
_TARGET_REVISION = "20260911_040000_add_api_key_account_usage_percent"


async def _api_key_columns(engine) -> dict[str, tuple[object, ...]]:
    async with engine.connect() as connection:
        result = await connection.execute(text("PRAGMA table_info('api_keys')"))
        return {str(row[1]): tuple(row) for row in result}


async def _stamped_revision(engine) -> str | None:
    async with engine.connect() as connection:
        return (await connection.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()


@pytest.mark.asyncio
async def test_api_key_account_usage_share_migration_upgrades_and_downgrades(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'api-key-share.sqlite'}"
    await to_thread.run_sync(lambda: run_upgrade(database_url, _PARENT_REVISION, bootstrap_legacy=False))
    engine = create_async_engine(database_url, future=True)
    try:
        await to_thread.run_sync(lambda: run_upgrade(database_url, _TARGET_REVISION, bootstrap_legacy=False))
        assert "account_usage_percent" in await _api_key_columns(engine)
        assert await _stamped_revision(engine) == _TARGET_REVISION

        await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(database_url), _PARENT_REVISION))
        assert "account_usage_percent" not in await _api_key_columns(engine)
        assert await _stamped_revision(engine) == _PARENT_REVISION
    finally:
        await engine.dispose()

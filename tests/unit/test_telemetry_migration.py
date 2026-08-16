from __future__ import annotations

import pytest
from alembic import command
from anyio import to_thread
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.migrate import _build_alembic_config, inspect_migration_state, run_upgrade

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_telemetry_migration_upgrade_defaults_and_downgrade(tmp_path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'telemetry.sqlite'}"
    parent = "20260803_000000_merge_http_bridge_recovery_and_capability_lineage_heads"
    revision = "20260806_000000_add_anonymous_telemetry"
    telemetry_columns = {
        "telemetry_consent",
        "telemetry_instance_id",
        "telemetry_private_key_encrypted",
    }

    async def columns_and_rows(engine):
        async with engine.connect() as connection:
            columns = {row[1] for row in await connection.execute(text("PRAGMA table_info('dashboard_settings')"))}
            rows = []
            if telemetry_columns <= columns:
                rows = (
                    await connection.execute(
                        text(
                            "SELECT telemetry_consent, telemetry_instance_id, "
                            "telemetry_private_key_encrypted FROM dashboard_settings"
                        )
                    )
                ).all()
            return columns, rows

    await to_thread.run_sync(lambda: run_upgrade(db_url, parent, bootstrap_legacy=False))
    engine = create_async_engine(db_url)
    try:
        columns, _ = await columns_and_rows(engine)
        assert not telemetry_columns & columns

        await to_thread.run_sync(lambda: run_upgrade(db_url, revision, bootstrap_legacy=False))
        columns, rows = await columns_and_rows(engine)
        assert telemetry_columns <= columns
        assert rows
        assert all(row == ("undecided", None, None) for row in rows)

        await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(db_url), parent))
        columns, _ = await columns_and_rows(engine)
        assert not telemetry_columns & columns

        result = await to_thread.run_sync(lambda: run_upgrade(db_url, "head", bootstrap_legacy=False))
        assert result.current_revision == inspect_migration_state(db_url).head_revision
        columns, _ = await columns_and_rows(engine)
        assert telemetry_columns <= columns
    finally:
        await engine.dispose()

from __future__ import annotations

import pytest
from alembic import command
from anyio import to_thread
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.migrate import _build_alembic_config, inspect_migration_state, run_upgrade

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_usage_caps_upgrade_downgrade_preserves_accounts(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'caps.sqlite'}"
    parent = "20260830_000000_add_quota_warmup_claim_expiry"
    await to_thread.run_sync(lambda: run_upgrade(url, parent, bootstrap_legacy=False))
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("""
                INSERT INTO accounts (id, email, plan_type, access_token_encrypted, refresh_token_encrypted,
                    id_token_encrypted, last_refresh, status, codex_installation_id)
                VALUES ('legacy', 'legacy@example.com', 'plus', X'00', X'00', X'00',
                        CURRENT_TIMESTAMP, 'active', 'test')
            """))
        result = await to_thread.run_sync(lambda: run_upgrade(url, "head", bootstrap_legacy=False))
        assert result.current_revision == inspect_migration_state(url).head_revision
        async with engine.begin() as conn:
            row = (await conn.execute(text(
                "SELECT usage_cap_5h_percent, usage_cap_weekly_percent FROM accounts"
            ))).one()
            assert tuple(row) == (None, None)
            await conn.execute(text("UPDATE accounts SET usage_cap_5h_percent=80, usage_cap_weekly_percent=50"))
        await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(url), parent))
        async with engine.connect() as conn:
            columns = {row[1] for row in await conn.execute(text("PRAGMA table_info('accounts')"))}
            assert "usage_cap_5h_percent" not in columns
            assert "usage_cap_weekly_percent" not in columns
            assert (await conn.execute(text("SELECT id FROM accounts"))).scalar_one() == "legacy"
        await to_thread.run_sync(lambda: run_upgrade(url, "head", bootstrap_legacy=False))
    finally:
        await engine.dispose()

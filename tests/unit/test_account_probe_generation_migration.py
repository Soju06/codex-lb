from __future__ import annotations

import pytest
from alembic import command
from anyio import to_thread
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.migrate import _build_alembic_config, check_migration_policy, run_upgrade

pytestmark = pytest.mark.unit

PARENT = "20260910_000000_request_logs_missing_cost_index"
REVISION = "20260910_010000_add_account_rejection_generation"


@pytest.mark.asyncio
async def test_probe_generation_migration_preserves_historical_hold(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'probe-migration.sqlite'}"
    await to_thread.run_sync(lambda: run_upgrade(url, PARENT, bootstrap_legacy=False))
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text("""
                INSERT INTO accounts (id, email, plan_type, access_token_encrypted,
                    refresh_token_encrypted, id_token_encrypted, last_refresh, status, reset_at, blocked_at,
                    codex_installation_id)
                VALUES ('legacy', 'legacy@example.invalid', 'pro', X'01', X'02', X'03',
                    '2026-09-10', 'rate_limited', 2000000000, 1900000000, 'legacy-installation')
            """)
            )
        for _ in range(2):
            await to_thread.run_sync(lambda: run_upgrade(url, REVISION, bootstrap_legacy=False))
            async with engine.connect() as connection:
                row = (
                    await connection.execute(
                        text("""
                    SELECT status, reset_at, blocked_at, block_generation, rejected_model,
                        rejected_service_tier, probe_claim_token, probe_claim_expires_at
                    FROM accounts WHERE id = 'legacy'
                """)
                    )
                ).one()
                assert tuple(row) == ("rate_limited", 2000000000, 1900000000, 0, None, None, None, None)
            await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(url), PARENT))
        assert await to_thread.run_sync(lambda: check_migration_policy(url)) == ()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("complete_schema", [False, True])
async def test_probe_generation_bootstrap_preserves_existing_evidence(tmp_path, complete_schema):
    url = f"sqlite+aiosqlite:///{tmp_path / 'probe-bootstrap.sqlite'}"
    await to_thread.run_sync(lambda: run_upgrade(url, PARENT, bootstrap_legacy=False))
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("ALTER TABLE accounts ADD COLUMN block_generation BIGINT NOT NULL DEFAULT 0"))
            await connection.execute(text("ALTER TABLE accounts ADD COLUMN rejected_model VARCHAR"))
            if complete_schema:
                await connection.execute(text("ALTER TABLE accounts ADD COLUMN rejected_service_tier VARCHAR"))
                await connection.execute(text("ALTER TABLE accounts ADD COLUMN probe_claim_token VARCHAR"))
                await connection.execute(text("ALTER TABLE accounts ADD COLUMN probe_claim_expires_at DATETIME"))
            await connection.execute(
                text("""
                INSERT INTO accounts (id, email, plan_type, access_token_encrypted,
                    refresh_token_encrypted, id_token_encrypted, last_refresh, status, reset_at, blocked_at,
                    codex_installation_id, block_generation, rejected_model)
                VALUES ('existing', 'existing@example.invalid', 'pro', X'01', X'02', X'03',
                    '2026-09-10', 'rate_limited', 2000000000, 1900000000, 'existing-installation',
                    7, 'gpt-6-astra')
                """)
            )
        await to_thread.run_sync(lambda: run_upgrade(url, REVISION, bootstrap_legacy=False))
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text("""
                    SELECT status, reset_at, blocked_at, block_generation, rejected_model,
                        rejected_service_tier, probe_claim_token, probe_claim_expires_at
                    FROM accounts WHERE id = 'existing'
                    """)
                )
            ).one()
            assert tuple(row) == ("rate_limited", 2000000000, 1900000000, 7, "gpt-6-astra", None, None, None)
    finally:
        await engine.dispose()

from __future__ import annotations

import pytest
from alembic import command
from anyio import to_thread
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.migrate import _build_alembic_config, check_schema_drift, run_upgrade

pytestmark = pytest.mark.integration
PARENT = "20260913_000000_add_oidc_provider_flow"
FIELDS = {"latency_first_output_ms", "output_delta_count", "latency_upstream_terminal_ms"}


@pytest.mark.asyncio
async def test_output_timing_migration_preserves_unknown_history(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'output-timing.db'}"
    await to_thread.run_sync(lambda: run_upgrade(url, PARENT, bootstrap_legacy=False))
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO request_logs (request_id, model, status, output_tokens, reasoning_tokens, "
                    "latency_ms, latency_first_token_ms, cost_usd) "
                    "VALUES ('historic', 'gpt-5.6-sol', 'success', 120, 110, 2005, 2000, 0.123456789)"
                )
            )
        await to_thread.run_sync(lambda: run_upgrade(url, "head", bootstrap_legacy=False))
        assert await to_thread.run_sync(lambda: check_schema_drift(url)) == ()
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT output_tokens, reasoning_tokens, latency_ms, latency_first_token_ms, "
                        "cost_usd, latency_first_output_ms, output_delta_count, latency_upstream_terminal_ms "
                        "FROM request_logs"
                    )
                )
            ).one()
            assert tuple(row) == (120, 110, 2005, 2000, 0.123456789, None, None, None)
        await to_thread.run_sync(lambda: command.downgrade(_build_alembic_config(url), PARENT))
        async with engine.begin() as connection:
            columns = await connection.run_sync(
                lambda conn: {c["name"] for c in inspect(conn).get_columns("request_logs")}
            )
            assert not columns & FIELDS
            await connection.execute(text("ALTER TABLE request_logs ADD COLUMN output_delta_count INTEGER"))
        await to_thread.run_sync(lambda: run_upgrade(url, "head", bootstrap_legacy=False))
        assert await to_thread.run_sync(lambda: check_schema_drift(url)) == ()
        async with engine.connect() as connection:
            assert (await connection.execute(text("SELECT cost_usd FROM request_logs"))).scalar_one() == 0.123456789
    finally:
        await engine.dispose()

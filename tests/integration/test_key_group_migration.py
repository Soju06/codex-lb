from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from app.db.migrate import _build_alembic_config, check_schema_drift, run_upgrade

pytestmark = pytest.mark.integration


def test_key_group_upgrade_downgrade_preserves_existing_keys(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'groups.db'}"
    previous = "20260830_000000_add_quota_warmup_claim_expiry"
    run_upgrade(url, previous, bootstrap_legacy=False)
    engine = create_engine(url.replace("+aiosqlite", ""))
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO api_keys (id, name, key_hash, key_prefix, is_active) "
                    "VALUES ('existing', 'Existing', 'hash', 'prefix', 1)"
                )
            )
        run_upgrade(url, "head", bootstrap_legacy=False)
        assert check_schema_drift(url) == ()
        with engine.begin() as conn:
            assert conn.execute(text("SELECT usage_group FROM api_keys WHERE id = 'existing'")).scalar_one() is None
            conn.execute(text("UPDATE api_keys SET usage_group = 'Team A' WHERE id = 'existing'"))
        command.downgrade(_build_alembic_config(url), previous)
        assert "usage_group" not in {column["name"] for column in inspect(engine).get_columns("api_keys")}
        with engine.connect() as conn:
            assert conn.execute(text("SELECT name FROM api_keys WHERE id = 'existing'")).scalar_one() == "Existing"
        run_upgrade(url, "head", bootstrap_legacy=False)
        assert check_schema_drift(url) == ()
        with engine.connect() as conn:
            assert conn.execute(text("SELECT usage_group FROM api_keys WHERE id = 'existing'")).scalar_one() is None
    finally:
        engine.dispose()

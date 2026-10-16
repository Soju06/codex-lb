from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from app.db.migrate import _build_alembic_config, check_schema_drift, run_upgrade

pytestmark = pytest.mark.integration
PARENT = "20260918_000000_merge_scim_and_overflow_heads"
REVISION = "20260926_000000_add_source_ownership"
HEAD = "20260926_010000_add_source_ownership_history"


def test_source_ownership_migration_preserves_historical_logs(tmp_path: Path):
    url = os.environ.get("CODEX_LB_TEST_MIGRATION_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'ownership.db'}")
    config = _build_alembic_config(url)
    assert ScriptDirectory.from_config(config).get_heads() == [HEAD]
    run_upgrade(url, PARENT, bootstrap_legacy=False)
    engine = create_engine(url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg"))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO request_logs (request_id, model, status, model_source_id) "
                    "VALUES ('historical-response', 'model', 'success', 'deleted-source')"
                )
            )
        run_upgrade(url, "head", bootstrap_legacy=False)
        assert check_schema_drift(url) == ()
        with engine.connect() as connection:
            assert connection.execute(text("SELECT count(*) FROM model_source_ownership")).scalar_one() == 0
            assert connection.execute(text("SELECT count(*) FROM model_source_ownership_history")).scalar_one() == 0
            assert connection.execute(
                text(
                    "SELECT model_source_id, model_source_revision FROM request_logs "
                    "WHERE request_id = 'historical-response'"
                )
            ).one() == ("deleted-source", None)
        command.downgrade(config, PARENT)
        assert not inspect(engine).has_table("model_source_ownership")
        assert not inspect(engine).has_table("model_source_ownership_history")
        assert "model_source_revision" not in {column["name"] for column in inspect(engine).get_columns("request_logs")}
        run_upgrade(url, "head", bootstrap_legacy=False)
        assert check_schema_drift(url) == ()
    finally:
        engine.dispose()

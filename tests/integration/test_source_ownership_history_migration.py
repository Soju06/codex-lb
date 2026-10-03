from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from app.db.migrate import _build_alembic_config, check_schema_drift, run_upgrade

pytestmark = pytest.mark.integration
PARENT = "20260926_000000_add_source_ownership"
REVISION = "20260926_010000_add_source_ownership_history"


def test_history_migration_preserves_expired_and_live_credential_evidence(tmp_path: Path) -> None:
    url = os.environ.get("CODEX_LB_TEST_MIGRATION_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'history.db'}")
    config = _build_alembic_config(url)
    assert ScriptDirectory.from_config(config).get_heads() == ["20260926_010000_add_source_ownership_history"]
    run_upgrade(url, PARENT, bootstrap_legacy=False)
    # The explicitly isolated migration database may already be at head after
    # another migration test. Exercise this revision from its parent each time.
    command.downgrade(config, PARENT)
    engine = create_engine(url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg"))
    keys = ["history-migration-expired", "history-migration-live"]
    try:
        with engine.begin() as connection:
            for key, expiry in zip(keys, [datetime(2020, 1, 1), datetime(2090, 1, 1)], strict=True):
                connection.execute(
                    text(
                        "INSERT INTO model_source_ownership (reference_key, source_id, source_revision, expires_at) "
                        "VALUES (:key, 'historical-source', 'historical-credential', :expiry)"
                    ),
                    {"key": key, "expiry": expiry},
                )
            connection.execute(
                text(
                    "INSERT INTO request_logs (request_id, model, status, model_source_id) "
                    "VALUES ('history-migration-response', 'model', 'success', 'historical-source')"
                )
            )
        run_upgrade(url, "head", bootstrap_legacy=False)
        assert check_schema_drift(url) == ()
        with engine.connect() as connection:
            history = connection.execute(
                text(
                    "SELECT reference_key, source_id, source_revision FROM model_source_ownership_history "
                    "WHERE reference_key IN (:expired, :live) ORDER BY reference_key"
                ),
                {"expired": keys[0], "live": keys[1]},
            ).all()
            assert history == [(key, "historical-source", "historical-credential") for key in keys]
            legacy = connection.execute(
                text(
                    "SELECT model_source_id, model_source_revision FROM request_logs "
                    "WHERE request_id = 'history-migration-response'"
                )
            ).one()
            assert legacy == ("historical-source", None)

        command.downgrade(config, PARENT)
        assert not inspect(engine).has_table("model_source_ownership_history")
        assert "model_source_revision" not in {column["name"] for column in inspect(engine).get_columns("request_logs")}
        run_upgrade(url, "head", bootstrap_legacy=False)
        assert check_schema_drift(url) == ()
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT source_revision FROM model_source_ownership_history WHERE reference_key = :key"),
                    {"key": keys[0]},
                ).scalar_one()
                == "historical-credential"
            )
    finally:
        engine.dispose()

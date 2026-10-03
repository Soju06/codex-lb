from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from app.db.migrate import _build_alembic_config, check_schema_drift, run_upgrade

pytestmark = pytest.mark.integration
PARENT = "20260926_010000_add_source_ownership_history"
REVISION = "20260929_000000_add_source_websocket"


def test_source_websocket_migration_defaults_and_round_trip(tmp_path: Path):
    path = tmp_path / "source-ws.db"
    url = f"sqlite+aiosqlite:///{path}"
    config = _build_alembic_config(url)
    assert ScriptDirectory.from_config(config).get_heads() == [REVISION]
    run_upgrade(url, PARENT, bootstrap_legacy=False)
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO model_sources (id, name, base_url, supports_responses) "
                    "VALUES ('old', 'old', 'https://example.invalid/v1', 1)"
                )
            )
        run_upgrade(url, "head", bootstrap_legacy=False)
        with engine.begin() as connection:
            assert connection.execute(text("SELECT supports_responses_websocket FROM model_sources")).scalar_one() == 0
            connection.execute(text("UPDATE model_sources SET supports_responses_websocket = 1"))
        run_upgrade(url, "head", bootstrap_legacy=False)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT supports_responses_websocket FROM model_sources")).scalar_one() == 1
        command.downgrade(config, PARENT)
        with engine.connect() as connection:
            assert "supports_responses_websocket" not in {
                column["name"] for column in inspect(connection).get_columns("model_sources")
            }
            assert connection.execute(text("SELECT supports_responses FROM model_sources")).scalar_one() == 1
        run_upgrade(url, "head", bootstrap_legacy=False)
        assert check_schema_drift(url) == ()
    finally:
        engine.dispose()

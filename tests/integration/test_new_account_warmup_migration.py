from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from app.db.migrate import _build_alembic_config, check_schema_drift, run_upgrade

pytestmark = pytest.mark.integration

PARENT = "20260918_000000_merge_scim_and_overflow_heads"
REVISION = "20261003_000000_add_new_account_warmup_setting"
COLUMN = "limit_warmup_auto_enable_new_accounts"


def test_new_account_warmup_migration_preserves_preferences(tmp_path: Path) -> None:
    database = tmp_path / "warmup-setting.db"
    url = f"sqlite+aiosqlite:///{database}"
    config = _build_alembic_config(url)
    scripts = ScriptDirectory.from_config(config)
    assert len(scripts.get_heads()) == 1
    revision = scripts.get_revision(REVISION)
    assert revision is not None
    assert revision.down_revision == PARENT
    run_upgrade(url, PARENT, bootstrap_legacy=False)
    engine = create_engine(f"sqlite:///{database}")
    try:
        with engine.begin() as connection:
            connection.execute(text("UPDATE dashboard_settings SET limit_warmup_enabled = 0 WHERE id = 1"))
            for enabled in (False, True):
                connection.execute(
                    text(
                        "INSERT INTO accounts (id, codex_installation_id, email, plan_type, access_token_encrypted, "
                        "refresh_token_encrypted, id_token_encrypted, last_refresh, status, limit_warmup_enabled) "
                        "VALUES (:id, :id, :email, 'plus', X'00', X'00', X'00', "
                        "'2026-09-23 00:00:00', 'active', :enabled)"
                    ),
                    {"id": str(enabled), "email": f"{enabled}@example.com", "enabled": enabled},
                )
        run_upgrade(url, REVISION, bootstrap_legacy=False)
        with engine.begin() as connection:
            assert connection.execute(text(f"SELECT {COLUMN} FROM dashboard_settings WHERE id = 1")).scalar_one() == 0
            assert (
                connection.execute(
                    text("SELECT limit_warmup_enabled FROM dashboard_settings WHERE id = 1")
                ).scalar_one()
                == 0
            )
            assert connection.execute(
                text("SELECT limit_warmup_enabled FROM accounts ORDER BY id")
            ).scalars().all() == [0, 1]
            connection.execute(text(f"UPDATE dashboard_settings SET {COLUMN} = 1 WHERE id = 1"))
        run_upgrade(url, REVISION, bootstrap_legacy=False)
        with engine.connect() as connection:
            assert connection.execute(text(f"SELECT {COLUMN} FROM dashboard_settings WHERE id = 1")).scalar_one() == 1
        command.downgrade(config, PARENT)
        with engine.connect() as connection:
            assert COLUMN not in {column["name"] for column in inspect(connection).get_columns("dashboard_settings")}
            assert connection.execute(
                text("SELECT limit_warmup_enabled FROM accounts ORDER BY id")
            ).scalars().all() == [0, 1]
        run_upgrade(url, "head", bootstrap_legacy=False)
        assert check_schema_drift(url) == ()
    finally:
        engine.dispose()

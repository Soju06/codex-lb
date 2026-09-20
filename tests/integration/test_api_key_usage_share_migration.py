"""API-key usage-share policy survives the public migration lifecycle."""

from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.migrate import _build_alembic_config, check_schema_drift, run_upgrade

pytestmark = pytest.mark.integration

_RELEASED_RETIREMENT = "20260914_000000_drop_subscription_overflow_schema"
_GUARDED_RETIREMENT = "20260914_000001_drop_subscription_overflow_schema"
_BASE = "20260914_000002_merge_overflow_retirement_heads"
_REVISION = "20260918_000000_add_api_key_usage_share_percent"
_COLUMN = "usage_share_percent"
_CONSTRAINT = "ck_api_keys_usage_share_percent"
_ROLLUP_TABLE = "request_demand_quarter_rollups"
_ROLLUP_INDEX = "idx_request_demand_account_slot"


@pytest.mark.parametrize("retirement_revision", [_RELEASED_RETIREMENT, _GUARDED_RETIREMENT])
def test_retirement_branch_stamps_converge_to_usage_share_head(
    tmp_path: Path,
    retirement_revision: str,
) -> None:
    path = tmp_path / f"{retirement_revision}.sqlite"
    url = f"sqlite+aiosqlite:///{path}"
    assert run_upgrade(url, retirement_revision, bootstrap_legacy=False).current_revision == retirement_revision
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO api_keys (id, name, key_hash, key_prefix, is_active)
                    VALUES ('branch-key', 'Branch key', 'branch-hash', 'sk-branch', 1)
                    """
                )
            )

        assert run_upgrade(url, "head", bootstrap_legacy=False).current_revision == _REVISION
        script = ScriptDirectory.from_config(_build_alembic_config(url))
        assert script.get_heads() == [_REVISION]
        merge_revision = script.get_revision(_BASE)
        assert merge_revision is not None
        merge_parents = merge_revision.down_revision
        assert isinstance(merge_parents, tuple)
        assert set(merge_parents) == {_RELEASED_RETIREMENT, _GUARDED_RETIREMENT}
        with engine.connect() as connection:
            assert (
                connection.execute(text("SELECT name FROM api_keys WHERE id='branch-key'")).scalar_one() == "Branch key"
            )
        assert check_schema_drift(url) == ()
    finally:
        engine.dispose()


def test_usage_share_migration_is_reversible_and_preserves_keys(tmp_path: Path) -> None:
    path = tmp_path / "usage-share.sqlite"
    url = f"sqlite+aiosqlite:///{path}"
    run_upgrade(url, _BASE, bootstrap_legacy=False)
    script = ScriptDirectory.from_config(_build_alembic_config(url))
    assert script.get_revision(_REVISION).down_revision == _BASE
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO api_keys (id, name, key_hash, key_prefix, is_active)
                    VALUES ('existing-key', 'Existing key', 'existing-hash', 'sk-existing', 1)
                    """
                )
            )
        assert _COLUMN not in {column["name"] for column in inspect(engine).get_columns("api_keys")}
        assert _ROLLUP_INDEX not in {index["name"] for index in inspect(engine).get_indexes(_ROLLUP_TABLE)}
        with engine.begin() as connection:
            connection.execute(text(f"CREATE INDEX {_ROLLUP_INDEX} ON {_ROLLUP_TABLE} (slot_epoch, account_id)"))

        assert run_upgrade(url, _REVISION, bootstrap_legacy=False).current_revision == _REVISION
        columns = {column["name"]: column for column in inspect(engine).get_columns("api_keys")}
        assert columns[_COLUMN]["nullable"] is True
        assert _CONSTRAINT in {constraint["name"] for constraint in inspect(engine).get_check_constraints("api_keys")}
        indexes = {index["name"]: index for index in inspect(engine).get_indexes(_ROLLUP_TABLE)}
        assert indexes[_ROLLUP_INDEX]["column_names"] == ["account_id", "slot_epoch"]
        with engine.connect() as connection:
            plan = connection.execute(
                text(
                    "EXPLAIN QUERY PLAN SELECT request_count FROM request_demand_quarter_rollups "
                    "WHERE account_id=:account_id AND slot_epoch>=:start AND slot_epoch<:end"
                ),
                {"account_id": "account", "start": 0, "end": 1},
            ).all()
            assert _ROLLUP_INDEX in " ".join(str(row[-1]) for row in plan)
            assert (
                connection.execute(
                    text("SELECT usage_share_percent FROM api_keys WHERE id='existing-key'")
                ).scalar_one()
                is None
            )

        for value in (None, 1, 100):
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE api_keys SET usage_share_percent=:value WHERE id='existing-key'"),
                    {"value": value},
                )
        for value in (0, 101):
            with pytest.raises(IntegrityError), engine.begin() as connection:
                connection.execute(
                    text("UPDATE api_keys SET usage_share_percent=:value WHERE id='existing-key'"),
                    {"value": value},
                )

        command.downgrade(_build_alembic_config(url), _BASE)
        assert _COLUMN not in {column["name"] for column in inspect(engine).get_columns("api_keys")}
        assert _ROLLUP_INDEX not in {index["name"] for index in inspect(engine).get_indexes(_ROLLUP_TABLE)}
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT id, name, key_hash, key_prefix, is_active FROM api_keys WHERE id='existing-key'")
            ).one()
        assert tuple(row) == ("existing-key", "Existing key", "existing-hash", "sk-existing", 1)

        assert run_upgrade(url, "head", bootstrap_legacy=False).current_revision == _REVISION
        indexes = {index["name"]: index for index in inspect(engine).get_indexes(_ROLLUP_TABLE)}
        assert indexes[_ROLLUP_INDEX]["column_names"] == ["account_id", "slot_epoch"]
        assert check_schema_drift(url) == ()
    finally:
        engine.dispose()

"""Exercise the rejection/spool merge through populated CLI upgrades."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, Engine, make_url

from app.db.migrate import _build_alembic_config, check_schema_drift, run_upgrade
from app.db.migration_url import to_sync_database_url

pytestmark = pytest.mark.integration

_COMMON_PARENT = "20260910_000000_request_logs_missing_cost_index"
_REJECTION = "20260910_010000_add_account_rejection_generation"
_SPOOL = "20260910_010000_dashboard_spool_retention"
_PARENTS = (_REJECTION, _SPOOL)
_MERGE = "20260910_160000_merge_rejection_spool_heads"
_RETENTION = "http_responses_session_bridge_operation_spool_retention_seconds"
_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class _MigrationDatabase:
    url: str
    engine: Engine
    starting_revisions: tuple[str, ...]


def _cli(url: str, *arguments: str) -> str:
    env = os.environ.copy()
    env["CODEX_LB_DATABASE_URL"] = env["CODEX_LB_TEST_DATABASE_URL"] = url
    result = subprocess.run(
        [sys.executable, "-m", "app.db.migrate", *arguments],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def _seed_common(connection: Connection) -> None:
    connection.execute(
        text(
            """
            INSERT INTO accounts (
                id, codex_installation_id, email, plan_type, access_token_encrypted,
                refresh_token_encrypted, id_token_encrypted, last_refresh, status
            ) VALUES ('retained-account', 'retained-installation', 'retained@example.invalid',
                      'pro', :token, :token, :token, '2026-09-10 10:00:00', 'active')
            """
        ),
        {"token": b"disposable-test-token"},
    )
    connection.execute(text("UPDATE dashboard_settings SET upstream_stream_transport = 'websocket' WHERE id = 1"))


def _seed_parent(connection: Connection, revision: str) -> None:
    if revision == _REJECTION:
        connection.execute(
            text(
                """
                UPDATE accounts SET block_generation = 7, rejected_model = 'retained-model',
                    rejected_service_tier = 'priority', probe_claim_token = 'retained-claim',
                    probe_claim_expires_at = '2026-09-10 11:00:00'
                WHERE id = 'retained-account'
                """
            )
        )
    else:
        connection.execute(text(f"UPDATE dashboard_settings SET {_RETENTION} = 12345.0 WHERE id = 1"))


def _revisions(engine: Engine) -> tuple[str, ...]:
    with engine.connect() as connection:
        return tuple(connection.execute(text("SELECT version_num FROM alembic_version ORDER BY version_num")).scalars())


def _state(engine: Engine) -> dict[str, Any]:
    with engine.connect() as connection:
        inspector = inspect(connection)
        tables = ("accounts", "dashboard_settings")
        return {
            "rows": {
                table: [dict(row) for row in connection.execute(text(f"SELECT * FROM {table} ORDER BY id")).mappings()]
                for table in tables
            },
            "schema": {
                table: {
                    "columns": [
                        (column["name"], str(column["type"]), column["nullable"], column["default"])
                        for column in inspector.get_columns(table)
                    ],
                    "primary_key": inspector.get_pk_constraint(table),
                    "foreign_keys": inspector.get_foreign_keys(table),
                    "indexes": [
                        {
                            **index,
                            "dialect_options": {
                                key: str(value) for key, value in index.get("dialect_options", {}).items()
                            },
                        }
                        for index in inspector.get_indexes(table)
                    ],
                }
                for table in tables
            },
        }


@contextmanager
def _disposable_database(tmp_path: Path) -> Iterator[_MigrationDatabase]:
    configured_url = os.environ["CODEX_LB_TEST_DATABASE_URL"]
    admin = None
    database_name = f"merge_rejection_spool_{uuid4().hex}"
    if configured_url.startswith("postgresql+"):
        # The configured server is disposable; each case owns a fresh database.
        admin = create_engine(to_sync_database_url(configured_url), isolation_level="AUTOCOMMIT")
        with admin.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        url = make_url(configured_url).set(database=database_name).render_as_string(hide_password=False)
    else:
        url = f"sqlite+aiosqlite:///{tmp_path / 'merge-parents.sqlite'}"
    engine = create_engine(to_sync_database_url(url))
    try:
        yield _MigrationDatabase(url, engine, ())
    finally:
        engine.dispose()
        if admin is not None:
            try:
                with admin.connect() as connection:
                    connection.execute(text(f'DROP DATABASE "{database_name}"'))
            finally:
                admin.dispose()


@pytest.fixture(params=[(_REJECTION,), (_SPOOL,), _PARENTS], ids=["rejection-parent", "spool-parent", "both-parents"])
def branch_database(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[_MigrationDatabase]:
    with _disposable_database(tmp_path) as database:
        run_upgrade(database.url, _COMMON_PARENT, bootstrap_legacy=False)
        with database.engine.begin() as connection:
            _seed_common(connection)
        database.starting_revisions = request.param
        for revision in database.starting_revisions:
            run_upgrade(database.url, revision, bootstrap_legacy=False)
            with database.engine.begin() as connection:
                _seed_parent(connection, revision)
        assert _revisions(database.engine) == tuple(sorted(database.starting_revisions))
        yield database


def test_rejection_spool_histories_converge_at_one_head(tmp_path: Path) -> None:
    script = ScriptDirectory.from_config(_build_alembic_config(f"sqlite+aiosqlite:///{tmp_path / 'graph.sqlite'}"))
    (head,) = script.get_heads()
    assert _MERGE in {revision.revision for revision in script.iterate_revisions(head, "base")}
    merge = script.get_revision(_MERGE)
    assert merge is not None and merge.down_revision == _PARENTS
    for revision in _PARENTS:
        parent = script.get_revision(revision)
        assert parent is not None and parent.down_revision == _COMMON_PARENT


def test_populated_cli_upgrade_preserves_both_branches(
    branch_database: _MigrationDatabase,
) -> None:
    database = branch_database
    expected_rows = _state(database.engine)["rows"]
    if _REJECTION not in database.starting_revisions:
        for row in expected_rows["accounts"]:
            row.update(
                block_generation=0,
                rejected_model=None,
                rejected_service_tier=None,
                probe_claim_token=None,
                probe_claim_expires_at=None,
            )
    if _SPOOL not in database.starting_revisions:
        for row in expected_rows["dashboard_settings"]:
            row[_RETENTION] = None

    _cli(database.url, "upgrade", "head")
    (head,) = ScriptDirectory.from_config(_build_alembic_config(database.url)).get_heads()
    assert _revisions(database.engine) == (head,)
    merged_rows = _state(database.engine)["rows"]
    # Guest revocation adds generation zero; other later nullable fields start NULL.
    for table, expected in expected_rows.items():
        actual = merged_rows[table]
        for row in actual:
            for column in set(row) - set(expected[0]):
                if table == "dashboard_settings" and column == "guest_session_generation":
                    assert row.pop(column) == 0
                else:
                    assert row.pop(column) is None
        assert actual == expected
    output = _cli(database.url, "check")
    assert "migration_policy=ok" in output and "schema_drift=none" in output


def test_isolated_rejection_spool_merge_only_downgrades_preserve_both_branches(
    branch_database: _MigrationDatabase,
) -> None:
    database = branch_database
    # Later joins retain sibling stamps on downgrade. Start at this merge directly
    # so this test exercises only the original no-op merge and its two parents.
    _cli(database.url, "upgrade", _MERGE)
    with database.engine.begin() as connection:
        for revision in _PARENTS:
            if revision not in database.starting_revisions:
                _seed_parent(connection, revision)
    populated = _state(database.engine)
    merge_drift = check_schema_drift(database.url)
    for parent in _PARENTS:
        assert _revisions(database.engine) == (_MERGE,)
        command.downgrade(_build_alembic_config(database.url), parent)
        assert _revisions(database.engine) == tuple(sorted(_PARENTS))
        assert _state(database.engine) == populated
        assert check_schema_drift(database.url) == merge_drift
        _cli(database.url, "upgrade", _MERGE)
        assert _revisions(database.engine) == (_MERGE,)
        assert _state(database.engine) == populated
    _cli(database.url, "upgrade", "head")
    (head,) = ScriptDirectory.from_config(_build_alembic_config(database.url)).get_heads()
    assert _revisions(database.engine) == (head,)
    roundtrip_rows = _state(database.engine)["rows"]
    for table, rows in populated["rows"].items():
        assert [{column: row[column] for column in rows[0]} for row in roundtrip_rows[table]] == rows
    assert roundtrip_rows["dashboard_settings"][0]["guest_session_generation"] == 0
    assert "schema_drift=none" in _cli(database.url, "check")

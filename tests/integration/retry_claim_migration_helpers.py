"""Isolated CLI, snapshots and populated fixtures for migration-join tests."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

_SPOOL = "20260910_010000_dashboard_spool_retention"
_GUEST = "20260908_000000_add_guest_session_generation"
_RETENTION = "http_responses_session_bridge_operation_spool_retention_seconds"
_ROOT = Path(__file__).resolve().parents[2]


def _environment(path: Path) -> dict[str, str]:
    url = f"sqlite+aiosqlite:///{path}"
    return {**os.environ, "CODEX_LB_DATABASE_URL": url, "CODEX_LB_TEST_DATABASE_URL": url}


def _run(path: Path, *args: str) -> str:
    result = subprocess.run(
        args,
        cwd=_ROOT,
        env=_environment(path),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, f"{args!r}\n{result.stdout}\n{result.stderr}"
    return result.stdout


def _cli(path: Path, *args: str) -> str:
    return _run(path, str(Path(sys.executable).with_name("codex-lb-db")), *args)


def _revisions(path: Path) -> tuple[str, ...]:
    with sqlite3.connect(path) as connection:
        return tuple(
            row[0] for row in connection.execute("SELECT version_num FROM alembic_version ORDER BY version_num")
        )


def _seed_branch(path: Path, branch: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE dashboard_settings SET sticky_threads_enabled = 0 WHERE id = 1")
        assert connection.execute("SELECT COUNT(*) FROM dashboard_settings WHERE id = 1").fetchone()[0] == 1
        connection.execute(
            """
            INSERT INTO http_bridge_retry_circuits (
                session_key_kind, session_key_hash, api_key_scope, consecutive_failures,
                cooldown_until_epoch, last_detail, updated_at_epoch, admission_generation
            ) VALUES ('session_header', 'retained-retry', '__anonymous__', 2,
                      1300.0, 'stream_incomplete', 1200.0, 7)
            """
        )
    _seed_branch_values(path, branch)


def _seed_branch_values(path: Path, branch: str) -> None:
    with sqlite3.connect(path) as connection:
        if branch == _SPOOL:
            connection.execute(f"UPDATE dashboard_settings SET {_RETENTION} = 98765.5 WHERE id = 1")
        elif branch == _GUEST:
            connection.execute("UPDATE dashboard_settings SET guest_session_generation = 17 WHERE id = 1")
        else:
            connection.execute(
                """
                UPDATE http_bridge_retry_circuits SET admission_claimed_at_epoch = 1201.25,
                    admission_claimed_generation = 7, admission_claimed_until_epoch = 4102444800.0
                WHERE session_key_hash = 'retained-retry'
                """
            )


def _state(path: Path) -> dict[str, Any]:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        schema = [
            tuple(row)
            for row in connection.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_schema "
                "WHERE name != 'alembic_version' AND tbl_name != 'alembic_version' "
                "ORDER BY type, name"
            )
        ]
        tables = [row[1] for row in schema if row[0] == "table"]
        rows = {
            table: [dict(row) for row in connection.execute('SELECT * FROM "' + table.replace('"', '""') + '"')]
            for table in tables
        }
        return {"schema": schema, "rows": rows}


def _assert_parent_rows_preserved(before: dict[str, Any], after: dict[str, Any]) -> None:
    for table, expected_rows in before["rows"].items():
        actual_rows = after["rows"][table]
        assert len(actual_rows) == len(expected_rows), table
        for expected, actual in zip(expected_rows, actual_rows, strict=True):
            assert {column: actual[column] for column in expected} == expected, table


def _downgrade_to_parent(path: Path, parent: str) -> None:
    # Name the parent: a relative -1 walk is ambiguous at a merge.
    # The public DB CLI has no downgrade command; invoke Alembic with the same isolated URL.
    _run(
        path,
        sys.executable,
        "-c",
        "import os, sys; from alembic import command; from app.db.migrate import _build_alembic_config; "
        "command.downgrade(_build_alembic_config(os.environ['CODEX_LB_DATABASE_URL']), sys.argv[1])",
        parent,
    )


def _seed_users(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "INSERT INTO dashboard_roles (id, slug, name, kind, permissions_version) "
            "VALUES ('retained-role', 'retained-role', 'Retained role', 'custom', 9)"
        )
        connection.executemany(
            "INSERT INTO dashboard_role_grants (role_id, permission, scope) VALUES ('retained-role', ?, ?)",
            [("api_keys:read", "own"), ("accounts:read", "all")],
        )
        connection.execute(
            "INSERT INTO dashboard_users (id, username, role_id, session_generation, password_hash) "
            "VALUES ('retained-user', 'retained-user', 'retained-role', 23, 'retained-user-hash')"
        )
        connection.execute(
            "INSERT INTO dashboard_identities (id, user_id, provider, provider_key, subject) "
            "VALUES ('retained-identity', 'retained-user', 'oidc', 'retained-provider', 'retained-subject')"
        )


def _seed_invite(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("UPDATE dashboard_users SET status = 'invited' WHERE id = 'retained-user'")
        connection.execute(
            "INSERT INTO dashboard_user_invites (id, user_id, token_hash, expires_at, "
            "created_by_user_id, sso_only, username_locked, created_at) "
            "VALUES ('retained-invite', 'retained-user', ?, '2100-01-01 00:00:00.000000', "
            "'retained-issuer-snapshot', 1, 1, '2026-09-10 12:00:00.000000')",
            (bytes(range(32)),),
        )

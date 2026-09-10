"""Keep invitations and populated dashboard ownership intact across the join."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

from app.db.migrate import _build_alembic_config, run_upgrade
from tests.integration.test_migration_merge_dashboard_users_rejection import (
    _GUEST,
    _auth_state,
    _seed_users,
)
from tests.integration.test_migration_merge_rejection_spool import (
    _REJECTION,
    _SPOOL,
    _cli,
    _disposable_database,
    _MigrationDatabase,
    _revisions,
    _seed_common,
    _seed_parent,
    _state,
)

pytestmark = pytest.mark.integration

_AUDIT = "20260909_030000_add_audit_actor_columns"
_REJECTION_MERGE = "20260910_200000_merge_dashboard_users_rejection_heads"
_INVITES = "20260909_040000_add_dashboard_user_invites"
_MERGE = "20260910_220000_merge_invites_rejection_heads"
_PARENTS = (_REJECTION_MERGE, _INVITES)


def _seed_invites(connection: Connection) -> None:
    for status in ("pending", "consumed", "revoked", "expired"):
        connection.execute(
            text("""
            INSERT INTO dashboard_users (id, username, role_id, session_generation, status)
            VALUES (:id, :id, 'retained-role', 6, :status)
        """),
            {"id": f"invite-user-{status}", "status": "active" if status == "consumed" else "invited"},
        )
        connection.execute(
            text("""
            INSERT INTO dashboard_user_invites (id, user_id, token_hash, expires_at,
                consumed_at, revoked_at, created_by_user_id, sso_only, username_locked)
            VALUES (:id, :user_id, :hash, :expiry, :consumed, :revoked,
                'deleted-inviter-snapshot', :sso_only, :locked)
        """),
            {
                "id": f"invite-{status}",
                "user_id": f"invite-user-{status}",
                "hash": status.encode().ljust(32, b"x"),
                "expiry": "2026-09-01 12:00:00+00:00" if status == "expired" else "2026-10-01 12:00:00+00:00",
                "consumed": "2026-09-10 11:00:00+00:00" if status == "consumed" else None,
                "revoked": "2026-09-10 11:00:00+00:00" if status == "revoked" else None,
                "sso_only": status == "pending",
                "locked": status in ("pending", "consumed"),
            },
        )


def _invite_state(engine: Engine) -> dict[str, Any]:
    with engine.connect() as connection:
        inspector = inspect(connection)
        table = "dashboard_user_invites"
        if not inspector.has_table(table):
            return {}
        return {
            "rows": [dict(row) for row in connection.execute(text(f"SELECT * FROM {table} ORDER BY id")).mappings()],
            "columns": [(c["name"], str(c["type"]), c["nullable"], c["default"]) for c in inspector.get_columns(table)],
            "primary_key": inspector.get_pk_constraint(table),
            "foreign_keys": inspector.get_foreign_keys(table),
            "unique": inspector.get_unique_constraints(table),
            "indexes": inspector.get_indexes(table),
        }


@pytest.fixture(
    params=[(_REJECTION_MERGE,), (_INVITES,), _PARENTS], ids=["rejection-parent", "invite-parent", "both-parents"]
)
def branch_database(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[_MigrationDatabase]:
    with _disposable_database(tmp_path) as database:
        run_upgrade(database.url, _GUEST, bootstrap_legacy=False)
        with database.engine.begin() as connection:
            _seed_common(connection)
            _seed_parent(connection, _SPOOL)
            connection.execute(
                text("""
                UPDATE dashboard_settings SET guest_session_generation = 9,
                    password_hash = 'legacy-admin-hash', totp_secret_encrypted = :secret,
                    totp_last_verified_step = 21 WHERE id = 1
            """),
                {"secret": b"legacy-admin-secret"},
            )
            connection.execute(
                text("""
                INSERT INTO audit_logs (timestamp, action, details)
                VALUES ('2026-09-10 10:00:00.000000', 'retained-action', 'retained-details')
            """)
            )
        run_upgrade(database.url, _AUDIT, bootstrap_legacy=False)
        with database.engine.begin() as connection:
            _seed_users(connection)
        database.starting_revisions = request.param
        for revision in database.starting_revisions:
            run_upgrade(database.url, revision, bootstrap_legacy=False)
            with database.engine.begin() as connection:
                if revision == _INVITES:
                    _seed_invites(connection)
                else:
                    _seed_parent(connection, _REJECTION)
        assert _revisions(database.engine) == tuple(sorted(database.starting_revisions))
        yield database


def test_invites_and_rejection_converge_at_one_head(tmp_path: Path) -> None:
    script = ScriptDirectory.from_config(_build_alembic_config(f"sqlite+aiosqlite:///{tmp_path / 'graph.sqlite'}"))
    assert script.get_heads() == [_MERGE]
    merge = script.get_revision(_MERGE)
    assert merge is not None and merge.down_revision == _PARENTS


def test_populated_cli_upgrade_and_roundtrip_preserve_invites(branch_database: _MigrationDatabase) -> None:
    database = branch_database
    expected_rows = _state(database.engine)["rows"]
    expected_auth = _auth_state(database.engine)
    expected_invites = _invite_state(database.engine)
    if _REJECTION_MERGE not in database.starting_revisions:
        for row in expected_rows["accounts"]:
            row.update(
                block_generation=0,
                rejected_model=None,
                rejected_service_tier=None,
                probe_claim_token=None,
                probe_claim_expires_at=None,
            )
    _cli(database.url, "upgrade", "head")
    assert _revisions(database.engine) == (_MERGE,)
    assert _state(database.engine)["rows"] == expected_rows
    assert _auth_state(database.engine) == expected_auth
    if _INVITES in database.starting_revisions:
        assert _invite_state(database.engine) == expected_invites
    else:
        assert _invite_state(database.engine)["rows"] == []
    output = _cli(database.url, "check")
    assert "migration_policy=ok" in output and "schema_drift=none" in output

    with database.engine.begin() as connection:
        if _INVITES not in database.starting_revisions:
            _seed_invites(connection)
        if _REJECTION_MERGE not in database.starting_revisions:
            _seed_parent(connection, _REJECTION)
    populated = (_state(database.engine), _auth_state(database.engine), _invite_state(database.engine))
    for parent in _PARENTS:
        command.downgrade(_build_alembic_config(database.url), parent)
        assert _revisions(database.engine) == tuple(sorted(_PARENTS))
        assert (_state(database.engine), _auth_state(database.engine), _invite_state(database.engine)) == populated
        assert "schema_drift=none" in _cli(database.url, "check")
        _cli(database.url, "upgrade", "head")
        assert _revisions(database.engine) == (_MERGE,)
        assert (_state(database.engine), _auth_state(database.engine), _invite_state(database.engine)) == populated
        output = _cli(database.url, "check")
        assert "migration_policy=ok" in output and "schema_drift=none" in output

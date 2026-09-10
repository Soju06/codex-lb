"""Preserve dashboard identities and rejection evidence through the final join."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

from app.db.migrate import _build_alembic_config, check_schema_drift, run_upgrade
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

_GUEST = "20260908_000000_add_guest_session_generation"
_REJECTION_MERGE = "20260910_180000_merge_guest_rejection_heads"
_USERS = "20260909_030000_add_audit_actor_columns"
_MERGE = "20260910_200000_merge_dashboard_users_rejection_heads"
_PARENTS = (_REJECTION_MERGE, _USERS)
_ADMIN = "7a4fc02d-216e-5be7-b974-bc437f23df60"
_AUTH_TABLES = (
    "dashboard_roles",
    "dashboard_role_grants",
    "dashboard_users",
    "dashboard_identities",
    "api_keys",
    "audit_logs",
)


def _seed_users(connection: Connection) -> None:
    connection.execute(
        text("""
        INSERT INTO dashboard_roles (id, slug, name, kind, permissions_version)
        VALUES ('retained-role', 'retained-role', 'Retained role', 'custom', 4)
    """)
    )
    connection.execute(
        text("""
        INSERT INTO dashboard_role_grants (role_id, permission, scope)
        VALUES ('retained-role', 'api_keys.read', 'own'), ('retained-role', 'usage.read', 'all')
    """)
    )
    connection.execute(
        text("""
        INSERT INTO dashboard_users (id, username, email, role_id, password_hash,
            totp_secret_encrypted, totp_last_verified_step, session_generation)
        VALUES ('retained-user', 'retained-user', 'user@example.invalid', 'retained-role',
            'retained-user-hash', :secret, 42, 13)
    """),
        {"secret": b"retained-user-secret"},
    )
    connection.execute(
        text("""
        INSERT INTO dashboard_identities (id, user_id, provider, provider_key, subject, groups_json)
        VALUES ('retained-identity', 'retained-user', 'oidc', 'retained-provider',
            'retained-subject', '["retained-group"]')
    """)
    )
    connection.execute(
        text("""
        INSERT INTO api_keys (id, name, key_hash, key_prefix, owner_user_id, created_by_user_id, is_active)
        VALUES ('retained-key', 'Retained key', 'retained-key-hash', 'retained-prefix',
            'retained-user', 'retained-user', true)
    """)
    )
    connection.execute(
        text("""
        UPDATE audit_logs SET actor_user_id = 'retained-user', actor_username = 'retained-user',
            actor_role_slug = 'retained-role', auth_method = 'password', target_type = 'account',
            target_id = 'retained-account', severity = 'warning' WHERE action = 'retained-action'
        """)
    )
    # After re-projection, the user row is authoritative: the merge must not
    # copy the deliberately stale legacy credential back onto this user.
    connection.execute(
        text("""
        UPDATE dashboard_users SET password_hash = 'current-admin-hash',
            totp_secret_encrypted = :secret, totp_last_verified_step = 84,
            session_generation = 17 WHERE id = :id
    """),
        {"secret": b"current-admin-secret", "id": _ADMIN},
    )


def _auth_state(engine: Engine) -> dict[str, Any]:
    with engine.connect() as connection:
        inspector = inspect(connection)
        tables = [table for table in _AUTH_TABLES if inspector.has_table(table)]
        return {
            "rows": {
                table: [
                    dict(row) for row in connection.execute(text(f"SELECT * FROM {table} ORDER BY 1, 2")).mappings()
                ]
                for table in tables
            },
            "schema": {
                table: {
                    "columns": [
                        (c["name"], str(c["type"]), c["nullable"], c["default"]) for c in inspector.get_columns(table)
                    ],
                    "primary_key": inspector.get_pk_constraint(table),
                    "foreign_keys": inspector.get_foreign_keys(table),
                    "unique": inspector.get_unique_constraints(table),
                    "indexes": inspector.get_indexes(table),
                }
                for table in tables
            },
        }


@pytest.fixture(
    params=[(_REJECTION_MERGE,), (_USERS,), _PARENTS], ids=["rejection-parent", "users-parent", "both-parents"]
)
def branch_database(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[_MigrationDatabase]:
    with _disposable_database(tmp_path) as database:
        run_upgrade(database.url, _GUEST, bootstrap_legacy=False)
        with database.engine.begin() as connection:
            _seed_common(connection)
            connection.execute(
                text(
                    "INSERT INTO audit_logs (timestamp, action, details) "
                    "VALUES ('2026-09-10 10:00:00.000000', 'retained-action', 'retained-details')"
                )
            )
            _seed_parent(connection, _SPOOL)
            connection.execute(
                text("""
                UPDATE dashboard_settings SET guest_session_generation = 9,
                    password_hash = 'legacy-admin-hash', totp_secret_encrypted = :secret,
                    totp_last_verified_step = 21 WHERE id = 1
            """),
                {"secret": b"legacy-admin-secret"},
            )
        database.starting_revisions = request.param
        for revision in database.starting_revisions:
            run_upgrade(database.url, revision, bootstrap_legacy=False)
            with database.engine.begin() as connection:
                if revision == _REJECTION_MERGE:
                    _seed_parent(connection, _REJECTION)
                else:
                    _seed_users(connection)
        assert _revisions(database.engine) == tuple(sorted(database.starting_revisions))
        yield database


def test_dashboard_users_and_rejection_have_one_head(tmp_path: Path) -> None:
    script = ScriptDirectory.from_config(_build_alembic_config(f"sqlite+aiosqlite:///{tmp_path / 'graph.sqlite'}"))
    (head,) = script.get_heads()
    assert _MERGE in {revision.revision for revision in script.iterate_revisions(head, "base")}
    merge = script.get_revision(_MERGE)
    assert merge is not None and merge.down_revision == _PARENTS


def test_populated_cli_upgrade_preserves_dashboard_users_and_rejection(branch_database: _MigrationDatabase) -> None:
    database = branch_database
    expected_rows = _state(database.engine)["rows"]
    expected_auth = _auth_state(database.engine)
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
    (head,) = ScriptDirectory.from_config(_build_alembic_config(database.url)).get_heads()
    assert _revisions(database.engine) == (head,)
    assert _state(database.engine)["rows"] == expected_rows
    auth = _auth_state(database.engine)
    if _USERS in database.starting_revisions:
        assert auth == expected_auth
    else:
        expected_audit = expected_auth["rows"]["audit_logs"]
        for row in expected_audit:
            row.update(
                actor_user_id=None,
                actor_username=None,
                actor_role_slug=None,
                auth_method=None,
                target_type=None,
                target_id=None,
                severity="info",
            )
        assert auth["rows"]["audit_logs"] == expected_audit
        roles = auth["rows"]["dashboard_roles"]
        assert {row["slug"] for row in roles} == {"admin", "operator", "member", "viewer", "guest"}
        assert auth["rows"]["dashboard_role_grants"] == []
        assert auth["rows"]["dashboard_identities"] == []
        (admin,) = auth["rows"]["dashboard_users"]
        assert admin["id"] == _ADMIN and admin["username"] == "admin"
        assert admin["role_id"] == next(row["id"] for row in roles if row["slug"] == "admin")
        assert admin["password_hash"] == "legacy-admin-hash"
        assert admin["totp_secret_encrypted"] == b"legacy-admin-secret"
        assert admin["totp_last_verified_step"] == 21 and admin["session_generation"] == 0
        assert admin["is_break_glass"] and not admin["must_change_password"]
    output = _cli(database.url, "check")
    assert "migration_policy=ok" in output and "schema_drift=none" in output


def test_isolated_dashboard_users_rejection_merge_roundtrip(branch_database: _MigrationDatabase) -> None:
    database = branch_database
    _cli(database.url, "upgrade", _MERGE)
    with database.engine.begin() as connection:
        if _USERS not in database.starting_revisions:
            _seed_users(connection)
        if _REJECTION_MERGE not in database.starting_revisions:
            _seed_parent(connection, _REJECTION)
    populated = (_state(database.engine), _auth_state(database.engine))
    merge_drift = check_schema_drift(database.url)
    for parent in _PARENTS:
        command.downgrade(_build_alembic_config(database.url), parent)
        assert _revisions(database.engine) == tuple(sorted(_PARENTS))
        assert (_state(database.engine), _auth_state(database.engine)) == populated
        assert check_schema_drift(database.url) == merge_drift
        _cli(database.url, "upgrade", _MERGE)
        assert _revisions(database.engine) == (_MERGE,)
        assert (_state(database.engine), _auth_state(database.engine)) == populated
        assert check_schema_drift(database.url) == merge_drift
    _cli(database.url, "upgrade", "head")
    (head,) = ScriptDirectory.from_config(_build_alembic_config(database.url)).get_heads()
    assert _revisions(database.engine) == (head,)
    assert (_state(database.engine), _auth_state(database.engine)) == populated
    assert "schema_drift=none" in _cli(database.url, "check")

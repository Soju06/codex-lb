"""Published affinity and identity histories retain their data when merged."""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, create_engine, inspect, text

from app.db.migrate import _build_alembic_config, check_schema_drift, run_upgrade
from app.db.migration_url import to_sync_database_url

pytestmark = pytest.mark.integration
_AFFINITY = "20260910_180000_merge_affinity_guest_heads"
_IDENTITY = "20260909_030000_add_audit_actor_columns"
_HEAD = "20260910_200000_merge_affinity_identity_heads"
_COLUMNS = ("sticky_key_source", "sticky_kind", "sticky_key_hash")
_AUTH_TABLES = ("dashboard_roles", "dashboard_role_grants", "dashboard_users", "dashboard_identities")


@pytest.fixture
def migration_url(tmp_path: Path, db_setup: bool) -> Iterator[str]:
    del db_setup
    configured = os.environ["CODEX_LB_DATABASE_URL"]
    if not configured.startswith("postgresql"):
        yield f"sqlite+aiosqlite:///{tmp_path / 'identity-merge.sqlite'}"
        return
    assert os.environ["CODEX_LB_TEST_DATABASE_URL"] == configured
    engine = create_engine(to_sync_database_url(configured))
    # Historical enum migrations inspect all schemas, so use the disposable
    # test database's public schema, as the other PostgreSQL migration tests do.
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        yield configured
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        engine.dispose()


def _rows(connection: Connection, table: str) -> list[dict]:
    return [dict(row) for row in connection.execute(text(f"SELECT * FROM {table}")).mappings()]


def _auth_snapshot(connection: Connection) -> dict[str, list[dict]]:
    return {table: sorted(_rows(connection, table), key=repr) for table in _AUTH_TABLES}


def _current_head(url: str) -> str:
    """The tree's single head, derived instead of pinned so later merge revisions
    do not have to edit these suites; the single-head property itself is asserted
    by tests/integration/test_migration_merge_overflow_transport.py."""

    (head,) = ScriptDirectory.from_config(_build_alembic_config(url)).get_heads()
    return head


def _applied_revisions(url: str, connection: Connection) -> set[str]:
    """Every revision in the ancestry of the rows in `alembic_version`."""

    versions = tuple(connection.execute(text("SELECT version_num FROM alembic_version")).scalars())
    script = ScriptDirectory.from_config(_build_alembic_config(url))
    return {revision.revision for revision in script.iterate_revisions(versions, "base")}


def _assert_rows_preserved(connection: Connection, table: str, snapshot: list[dict]) -> None:
    """`table` still carries every value the snapshot recorded.

    Compared on the snapshot's columns that the schema still has: a revision
    merged in from another branch may add a column the snapshot cannot know
    about, or drop one it recorded (the legacy dashboard credential columns),
    and neither is a fact about the branch under test.
    """

    live = {column["name"] for column in inspect(connection).get_columns(table)}
    columns = [key for key in (snapshot[0].keys() if snapshot else ()) if key in live]
    narrowed = [{key: row[key] for key in columns} for row in snapshot]
    actual = [{key: row[key] for key in columns} for row in _rows(connection, table)]
    assert sorted(actual, key=repr) == sorted(narrowed, key=repr), table


def _legacy_credential_columns_exist(connection: Connection) -> bool:
    live = {column["name"] for column in inspect(connection).get_columns("dashboard_settings")}
    return {"password_hash", "totp_secret_encrypted", "totp_last_verified_step"} <= live


def _seed_history(connection: Connection) -> None:
    connection.execute(
        text("""
        INSERT INTO audit_logs (action, timestamp) VALUES ('synthetic.action', '2026-09-10 00:00:00.123456')
    """)
    )
    connection.execute(
        text("""
        INSERT INTO accounts (id, codex_installation_id, email, plan_type,
            access_token_encrypted, refresh_token_encrypted, id_token_encrypted, last_refresh, status)
        VALUES ('merge-owner', 'synthetic-installation', 'synthetic@example.invalid', 'plus',
            :token, :token, :token, '2026-09-10 00:00:00', 'active')
        """),
        {"token": b"synthetic-encrypted-token"},
    )
    connection.execute(
        text("""
        INSERT INTO request_logs (account_id, request_id, model, status)
        VALUES ('merge-owner', 'synthetic-history', 'synthetic-model', 'success')
    """)
    )
    connection.execute(
        text("""
        INSERT INTO api_keys (id, name, key_hash, key_prefix, is_active)
        VALUES ('synthetic-key', 'synthetic', 'synthetic-hash', 'synthetic-prefix', true)
    """)
    )
    connection.execute(text("UPDATE dashboard_settings SET guest_session_generation=7 WHERE id=1"))
    if _legacy_credential_columns_exist(connection):
        # Only a schema that predates the drop revision has anywhere to put a
        # legacy credential; at head it is the account row or nothing.
        connection.execute(
            text("""
            UPDATE dashboard_settings SET password_hash='legacy-password',
                totp_secret_encrypted=:secret, totp_last_verified_step=11 WHERE id=1
        """),
            {"secret": b"legacy-totp"},
        )


def _seed_identity(connection: Connection) -> None:
    connection.execute(
        text("""
        UPDATE audit_logs SET actor_user_id='synthetic-admin', actor_username='admin',
            actor_role_slug='custom-role', auth_method='password', target_type='api_key',
            target_id='synthetic-key', severity='warning'
    """)
    )
    connection.execute(
        text("""
        INSERT INTO dashboard_roles (id, slug, name, kind)
        VALUES ('custom-role', 'custom-role', 'Synthetic role', 'custom')
    """)
    )
    connection.execute(
        text("""
        INSERT INTO dashboard_role_grants (role_id, permission, scope)
        VALUES ('custom-role', 'dashboard:read', 'own')
    """)
    )
    connection.execute(
        text("""
        INSERT INTO dashboard_users (id, username, role_id, password_hash,
            totp_secret_encrypted, totp_last_verified_step, session_generation)
        VALUES ('synthetic-admin', 'admin', 'custom-role', 'current-password', :secret, 23, 5)
    """),
        {"secret": b"current-totp"},
    )
    connection.execute(
        text("""
        INSERT INTO dashboard_identities (id, user_id, provider, provider_key, subject, groups_json)
        VALUES ('synthetic-identity', 'synthetic-admin', 'oidc', 'synthetic-provider', 'subject', '["operators"]')
    """)
    )
    connection.execute(
        text("""
        UPDATE api_keys SET owner_user_id='synthetic-admin', created_by_user_id='synthetic-admin',
            deactivated_reason='owner_disabled'
    """)
    )


@pytest.mark.parametrize("starting_revision", [_AFFINITY, _IDENTITY])
def test_populated_upgrade_and_merge_reversal_preserve_both_histories(
    migration_url: str, starting_revision: str
) -> None:
    url = migration_url
    run_upgrade(url, starting_revision, bootstrap_legacy=False)
    engine = create_engine(to_sync_database_url(url))
    try:
        with engine.begin() as connection:
            _seed_history(connection)
            if starting_revision == _IDENTITY:
                _seed_identity(connection)
                auth_before = _auth_snapshot(connection)
            else:
                connection.execute(
                    text("""
                    UPDATE request_logs SET sticky_key_source='session_id', sticky_kind='session',
                        sticky_key_hash='synthetic-affinity-hash'
                """)
                )
            before = {
                table: _rows(connection, table)
                for table in ("accounts", "request_logs", "api_keys", "dashboard_settings", "audit_logs")
            }
        assert run_upgrade(url, _HEAD, bootstrap_legacy=False).current_revision == _HEAD
        with engine.begin() as connection:
            for table, rows in before.items():
                _assert_rows_preserved(connection, table, rows)
            if starting_revision == _IDENTITY:
                assert _auth_snapshot(connection) == auth_before
                assert {name: _rows(connection, "request_logs")[0][name] for name in _COLUMNS} == dict.fromkeys(
                    _COLUMNS
                )
            else:
                admin = _rows(connection, "dashboard_users")
                assert len(admin) == 1
                assert admin[0]["username"] == "admin"
                assert admin[0]["password_hash"] == "legacy-password"
                assert admin[0]["totp_secret_encrypted"] == b"legacy-totp"
                assert admin[0]["totp_last_verified_step"] == 11
                assert admin[0]["session_generation"] == 0
                audit = _rows(connection, "audit_logs")[0]
                assert audit["severity"] == "info"
                assert all(
                    audit[name] is None
                    for name in (
                        "actor_user_id",
                        "actor_username",
                        "actor_role_slug",
                        "auth_method",
                        "target_type",
                        "target_id",
                    )
                )
                assert len(_rows(connection, "dashboard_roles")) == 5
                assert _rows(connection, "dashboard_role_grants") == []
                assert _rows(connection, "api_keys")[0]["owner_user_id"] is None
            connection.execute(
                text("""
                UPDATE dashboard_users SET password_hash='rotated-password', totp_secret_encrypted=:secret,
                    totp_last_verified_step=37, session_generation=13 WHERE username='admin'
            """),
                {"secret": b"rotated-totp"},
            )
            preserved = _auth_snapshot(connection)
            preserved.update({table: _rows(connection, table) for table in before})
        command.downgrade(_build_alembic_config(url), _AFFINITY)
        with engine.connect() as connection:
            # The merge is unapplied and both of its parents are back in the
            # ledger's ancestry; other branches stay applied alongside them.
            applied = _applied_revisions(url, connection)
            assert {_AFFINITY, _IDENTITY} <= applied
            assert _HEAD not in applied
            for table, rows in preserved.items():
                _assert_rows_preserved(connection, table, rows)
        # The merge re-applies; the other branch stays applied, so the ledger
        # reports both heads until the walk to the tree head converges them.
        reapplied = run_upgrade(url, _HEAD, bootstrap_legacy=False).current_revision
        assert reapplied is not None and _HEAD in reapplied.split(",")
        assert run_upgrade(url, "head", bootstrap_legacy=False).current_revision == _current_head(url)
        assert check_schema_drift(url) == ()
        with engine.connect() as connection:
            for table, rows in preserved.items():
                _assert_rows_preserved(connection, table, rows)
    finally:
        engine.dispose()


def test_fresh_single_head_and_bootstrap_preserve_the_account_credentials(migration_url: str) -> None:
    """Re-running the whole chain over an existing head schema changes no credential.

    The one-shot re-projection has nowhere to read from once the legacy columns
    are dropped, so the account rows -- the only authority left -- must come
    through a ledger-less bootstrap exactly as they were.
    """

    url = migration_url
    script = ScriptDirectory.from_config(_build_alembic_config(url))
    head = _current_head(url)
    assert script.get_revision(_HEAD).down_revision == (_AFFINITY, _IDENTITY)
    assert run_upgrade(url, "head", bootstrap_legacy=False).current_revision == head
    engine = create_engine(to_sync_database_url(url))
    try:
        with engine.begin() as connection:
            columns = {column["name"]: column for column in inspect(connection).get_columns("request_logs")}
            assert all(columns[name]["nullable"] and columns[name]["default"] is None for name in _COLUMNS)
            _seed_history(connection)
            _seed_identity(connection)
            # Bootstrap an existing schema with no Alembic ledger.
            connection.execute(text("DROP TABLE alembic_version"))
            before = _auth_snapshot(connection)
            before.update(
                {
                    table: _rows(connection, table)
                    for table in ("accounts", "request_logs", "api_keys", "dashboard_settings", "audit_logs")
                }
            )
        assert run_upgrade(url, "head", bootstrap_legacy=True).current_revision == _current_head(url)
        assert check_schema_drift(url) == ()
        with engine.connect() as connection:
            for table, rows in before.items():
                _assert_rows_preserved(connection, table, rows)
    finally:
        engine.dispose()

"""Invite state survives convergence with the published affinity history."""

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text

from app.db.migrate import _build_alembic_config, check_schema_drift, run_upgrade
from app.db.migration_url import to_sync_database_url
from tests.integration.test_affinity_identity_migration import (
    _applied_revisions,
    _assert_rows_preserved,
    _auth_snapshot,
    _current_head,
    _rows,
    _seed_history,
    _seed_identity,
)
from tests.integration.test_affinity_identity_migration import (
    migration_url as migration_url,
)

pytestmark = pytest.mark.integration
_AFFINITY = "20260910_200000_merge_affinity_identity_heads"
_INVITES = "20260909_040000_add_dashboard_user_invites"
_INVITE_MERGE = "20260910_220000_merge_affinity_invite_heads"
_TABLES = ("accounts", "request_logs", "api_keys", "dashboard_settings", "audit_logs", "dashboard_user_invites")


@pytest.mark.parametrize("starting_revision", [_AFFINITY, _INVITES])
def test_populated_invite_history_survives_merge_reversal(migration_url: str, starting_revision: str) -> None:
    url = migration_url
    run_upgrade(url, starting_revision, bootstrap_legacy=False)
    engine = create_engine(to_sync_database_url(url))
    try:
        with engine.begin() as connection:
            _seed_history(connection)
            _seed_identity(connection)
            if starting_revision == _INVITES:
                for index, state in enumerate(("pending", "consumed", "revoked")):
                    connection.execute(
                        text("""
                        INSERT INTO dashboard_users (id, username, role_id, password_hash, session_generation)
                        VALUES (:id, :id, 'custom-role', 'invite-user-password', 13)
                    """),
                        {"id": state},
                    )
                    connection.execute(
                        text("""
                        INSERT INTO dashboard_user_invites
                            (id, user_id, token_hash, expires_at, consumed_at, revoked_at,
                             created_by_user_id, sso_only, username_locked)
                        VALUES (:id, :id, :token, '2026-09-12 01:00:00+00:00', :consumed, :revoked,
                                'deleted-inviter-snapshot', :sso, :locked)
                    """),
                        {
                            "id": state,
                            "token": bytes([index + 1]),
                            "consumed": "2026-09-11 01:00:00+00:00" if state == "consumed" else None,
                            "revoked": "2026-09-11 02:00:00+00:00" if state == "revoked" else None,
                            "sso": state == "pending",
                            "locked": state != "pending",
                        },
                    )
                invites = _rows(connection, "dashboard_user_invites")
            else:
                connection.execute(
                    text("""
                    UPDATE request_logs SET sticky_key_source='session_id', sticky_kind='session',
                        sticky_key_hash='synthetic-affinity-hash'
                """)
                )
                invites = []
            before = _auth_snapshot(connection)
            before.update({table: _rows(connection, table) for table in _TABLES[:-1]})
        script = ScriptDirectory.from_config(_build_alembic_config(url))
        head = _current_head(url)
        assert script.get_revision(_INVITE_MERGE).down_revision == (_AFFINITY, _INVITES)
        assert run_upgrade(url, "head", bootstrap_legacy=False).current_revision == head
        assert check_schema_drift(url) == ()
        with engine.connect() as connection:
            for table, rows in before.items():
                _assert_rows_preserved(connection, table, rows)
            _assert_rows_preserved(connection, "dashboard_user_invites", invites)
            if starting_revision == _INVITES:
                log = _rows(connection, "request_logs")[0]
                assert (log["sticky_key_source"], log["sticky_kind"], log["sticky_key_hash"]) == (None, None, None)
            preserved = _auth_snapshot(connection)
            preserved.update({table: _rows(connection, table) for table in _TABLES})
        command.downgrade(_build_alembic_config(url), _AFFINITY)
        with engine.connect() as connection:
            # The merge is unapplied and both of its parents are back in the
            # ledger's ancestry; other branches stay applied alongside them.
            applied = _applied_revisions(url, connection)
            assert {_AFFINITY, _INVITES} <= applied
            assert _INVITE_MERGE not in applied
            for table, rows in preserved.items():
                # ``preserved`` is a snapshot of the *tree head*, and the
                # reversal legitimately takes columns back (every revision above
                # this merge that added one, starting with
                # `dashboard_settings.local_login_policy`) *and* gives columns
                # back (the legacy credential columns, which the drop revision
                # above this merge had taken). Neither set is row data this
                # branch wrote, so the reversal is checked over the columns the
                # two sides share; the re-upgrade below compares the snapshot
                # whole.
                _assert_rows_preserved(connection, table, rows)
        # Drift is asserted only after walking back to head, as the sibling
        # affinity merge-reversal tests do. Reaching the merge's parents also
        # unwinds every revision layered above the merge since, so a drift
        # assertion here is really an assertion that nothing newer than the
        # merge has changed the schema — true only by accident, and false for
        # the first descendant that adds a column.
        assert run_upgrade(url, "head", bootstrap_legacy=False).current_revision == _current_head(url)
        with engine.connect() as connection:
            for table, rows in preserved.items():
                assert sorted(_rows(connection, table), key=repr) == sorted(rows, key=repr)
        assert check_schema_drift(url) == ()
    finally:
        engine.dispose()

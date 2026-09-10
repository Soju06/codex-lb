"""Preserve populated CPA and dashboard identity branches across their merge."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from app.core.config.settings import get_settings
from app.db.migrate import _build_alembic_config, check_schema_drift, run_upgrade
from app.db.migration_url import to_sync_database_url
from tests.integration.test_migration_merge_cpa_guest import _KEY, _RETENTION, _BranchDatabase, _revisions

pytestmark = pytest.mark.integration

_COMMON = "20260908_000000_add_guest_session_generation"
_CPA = "20260910_030000_merge_cpa_guest_heads"
_IDENTITY = "20260909_030000_add_audit_actor_columns"
_PARENTS = (_CPA, _IDENTITY)
_MERGE = "20260910_040000_merge_cpa_dashboard_identity"
_LEGACY_SECRET = b"\x00legacy-encrypted-totp\xff"
_USER_SECRET = b"\x00user-encrypted-totp\xfe"


def test_cpa_dashboard_identity_merge_is_on_single_head_graph_and_keeps_original_parents(tmp_path: Path) -> None:
    script = ScriptDirectory.from_config(_build_alembic_config(f"sqlite+aiosqlite:///{tmp_path / 'graph.sqlite'}"))
    heads = script.get_heads()
    assert len(heads) == 1
    assert _MERGE in {revision.revision for revision in script.iterate_revisions(heads[0], "base")}
    merge = script.get_revision(_MERGE)
    assert merge is not None and merge.down_revision == _PARENTS
    expected_parents = {
        _CPA: ("20260910_020000_merge_cpa_spool_retention", _COMMON),
        _IDENTITY: "20260909_020000_reproject_compat_admin_credentials",
        "20260909_020000_reproject_compat_admin_credentials": "20260909_010000_add_dashboard_users",
        "20260909_010000_add_dashboard_users": "20260909_000000_add_dashboard_roles",
        "20260909_000000_add_dashboard_roles": _COMMON,
    }
    for revision, parent in expected_parents.items():
        original = script.get_revision(revision)
        assert original is not None and original.down_revision == parent


def _state(engine: Engine, *, include_invites: bool = False) -> dict[str, Any]:
    with engine.connect() as connection:
        inspector = inspect(connection)
        result = {}
        for table in (
            "model_sources",
            "model_source_models",
            "dashboard_settings",
            "dashboard_roles",
            "dashboard_role_grants",
            "dashboard_users",
            "dashboard_identities",
            "api_keys",
            "audit_logs",
            *(("dashboard_user_invites",) if include_invites else ()),
        ):
            if not inspector.has_table(table):
                continue
            primary_key = inspector.get_pk_constraint(table)
            order = ", ".join(primary_key["constrained_columns"])
            result[table] = {
                "rows": [
                    dict(row) for row in connection.execute(text(f"SELECT * FROM {table} ORDER BY {order}")).mappings()
                ],
                "columns": [
                    (c["name"], str(c["type"]), c["nullable"], c["default"]) for c in inspector.get_columns(table)
                ],
                "primary_key": primary_key,
                "foreign_keys": inspector.get_foreign_keys(table),
                "indexes": inspector.get_indexes(table),
                "unique_constraints": inspector.get_unique_constraints(table),
            }
        return result


def _seed_branch(engine: Engine, revision: str) -> None:
    with engine.begin() as connection:
        if revision == _CPA:
            connection.execute(
                text(
                    "UPDATE model_sources SET catalog_mode = 'cli_proxy_api', catalog_refresh_token = 'owned-refresh', "
                    "catalog_next_refresh_at = '2026-09-11 12:00:00' WHERE id = 'retained'"
                )
            )
            return
        connection.execute(
            text(
                "INSERT INTO dashboard_roles (id, slug, name, kind, permissions_version) "
                "VALUES ('retained-role', 'retained-role', 'Retained role', 'custom', 9)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO dashboard_role_grants (role_id, permission, scope) "
                "VALUES ('retained-role', 'accounts.read', 'all')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO dashboard_users (id, username, role_id, password_hash, totp_secret_encrypted, "
                "totp_last_verified_step, session_generation) "
                "VALUES ('retained-user', 'retained-user', 'retained-role', '$2b$retained-user', :secret, 77, 13)"
            ),
            {"secret": _USER_SECRET},
        )
        connection.execute(
            text(
                "INSERT INTO dashboard_identities (id, user_id, provider, provider_key, subject, groups_json) "
                "VALUES ('retained-identity', 'retained-user', 'oidc', "
                "'retained-provider', 'retained-subject', '[\"ops\"]')"
            )
        )
        connection.execute(
            text(
                "UPDATE api_keys SET owner_user_id = 'retained-user', created_by_user_id = 'retained-user', "
                "deactivated_reason = 'manual' WHERE id = 'retained-key'"
            )
        )
        connection.execute(
            text(
                "INSERT INTO audit_logs (timestamp, action, actor_ip, details, request_id, actor_user_id, "
                "actor_username, actor_role_slug, auth_method, target_type, target_id, severity) "
                "VALUES ('2026-09-10 12:34:56.123456', 'api_key.disable', '192.0.2.1', 'retained details', "
                "'attributed-request', 'retained-user', 'retained-user', 'retained-role', 'password', "
                "'api_key', 'retained-key', 'warning')"
            )
        )
        # The user row became authoritative at this parent. Replaying the
        # credential projection during a merge would overwrite these bytes.
        connection.execute(
            text(
                "UPDATE dashboard_users SET password_hash = '$2b$current-admin', "
                "totp_secret_encrypted = :secret, totp_last_verified_step = 88, session_generation = 17 "
                "WHERE username = 'admin'"
            ),
            {"secret": _USER_SECRET},
        )


def _seed_common(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO model_sources (id, name, base_url, api_key_encrypted) "
                "VALUES ('retained', 'CPA', 'http://localhost:8317/v1', :key)"
            ),
            {"key": _KEY},
        )
        connection.execute(text("INSERT INTO model_source_models (source_id, model) VALUES ('retained', 'kept-model')"))
        connection.execute(
            text(
                f"UPDATE dashboard_settings SET {_RETENTION} = 86400.5, guest_session_generation = 7, "
                "password_hash = '$2b$legacy-admin', totp_secret_encrypted = :secret, "
                "totp_last_verified_step = 42 WHERE id = 1"
            ),
            {"secret": _LEGACY_SECRET},
        )
        connection.execute(
            text(
                "INSERT INTO api_keys (id, name, key_hash, key_prefix, is_active) "
                "VALUES ('retained-key', 'Retained key', 'retained-key-hash', 'retained-prefix', :active)"
            ),
            {"active": False},
        )
        connection.execute(
            text(
                "INSERT INTO audit_logs (timestamp, action, actor_ip, details, request_id) "
                "VALUES ('2026-09-10 12:34:56', 'historical.action', '192.0.2.2', "
                "'historical details', 'historical-request')"
            )
        )


@pytest.fixture(params=[(_CPA,), (_IDENTITY,), _PARENTS], ids=["cpa-parent", "identity-parent", "both-parents"])
def branch_database(request: pytest.FixtureRequest, tmp_path: Path, db_setup) -> Iterator[_BranchDatabase]:
    configured_url = get_settings().database_url
    postgres = configured_url.startswith("postgresql+")
    url = configured_url if postgres else f"sqlite+aiosqlite:///{tmp_path / 'cpa-identity.sqlite'}"
    engine = create_engine(to_sync_database_url(url))
    try:
        if postgres:
            with engine.begin() as connection:
                connection.execute(text("DROP SCHEMA public CASCADE"))
                connection.execute(text("CREATE SCHEMA public"))
        run_upgrade(url, _COMMON, bootstrap_legacy=False)
        _seed_common(engine)
        parents = request.param
        for revision in parents:
            run_upgrade(url, revision, bootstrap_legacy=False)
            _seed_branch(engine, revision)
        assert _revisions(engine) == tuple(sorted(parents))
        yield _BranchDatabase(url, engine, parents)
    finally:
        engine.dispose()


def test_populated_branches_merge_and_downgrade_without_data_loss(branch_database: _BranchDatabase) -> None:
    database = branch_database
    before = _state(database.engine)
    result = run_upgrade(database.url, _MERGE, bootstrap_legacy=False)
    assert result.current_revision == _MERGE
    assert _revisions(database.engine) == (_MERGE,)
    merged = _state(database.engine)
    source = merged["model_sources"]["rows"][0]
    assert source["api_key_encrypted"] == _KEY
    assert source["catalog_mode"] == ("cli_proxy_api" if _CPA in database.parents else "manual")
    assert source["catalog_refresh_token"] == ("owned-refresh" if _CPA in database.parents else None)
    if _CPA not in database.parents:
        assert source["catalog_next_refresh_at"] is None
    settings = merged["dashboard_settings"]["rows"][0]
    assert settings[_RETENTION] == 86400.5
    assert settings["guest_session_generation"] == 7
    for table, old_state in before.items():
        new_state = merged[table]
        for old, new in zip(old_state["rows"], new_state["rows"], strict=True):
            expected = dict(old)
            if table == "audit_logs" and _IDENTITY not in database.parents and database.engine.dialect.name == "sqlite":
                # The audit revision pads second-precision SQLite text once.
                assert old["timestamp"] == "2026-09-10 12:34:56"
                expected["timestamp"] = "2026-09-10 12:34:56.000000"
            assert {column: new[column] for column in old} == expected
        if table not in ("model_sources", "api_keys", "audit_logs"):
            assert new_state == old_state
    users = {row["username"]: row for row in merged["dashboard_users"]["rows"]}
    if _IDENTITY in database.parents:
        assert users["retained-user"]["session_generation"] == 13
        assert users["retained-user"]["totp_secret_encrypted"] == _USER_SECRET
        assert users["admin"]["session_generation"] == 17
        assert users["admin"]["password_hash"] == "$2b$current-admin"
    else:
        assert users["admin"]["password_hash"] == "$2b$legacy-admin"
        assert users["admin"]["totp_secret_encrypted"] == _LEGACY_SECRET
        assert users["admin"]["totp_last_verified_step"] == 42
        assert users["admin"]["session_generation"] == 0
    key = merged["api_keys"]["rows"][0]
    assert key["key_hash"] == "retained-key-hash"
    assert key["owner_user_id"] == ("retained-user" if _IDENTITY in database.parents else None)
    assert key["created_by_user_id"] == key["owner_user_id"]
    assert key["deactivated_reason"] == ("manual" if _IDENTITY in database.parents else None)
    audit = {row["request_id"]: row for row in merged["audit_logs"]["rows"]}
    historical = audit["historical-request"]
    assert historical["severity"] == "info"
    for column in ("actor_user_id", "actor_username", "actor_role_slug", "auth_method", "target_type", "target_id"):
        assert historical[column] is None
    if _IDENTITY in database.parents:
        attributed = audit["attributed-request"]
        assert attributed["actor_user_id"] == "retained-user"
        assert attributed["actor_username"] == "retained-user"
        assert attributed["actor_role_slug"] == "retained-role"
        assert attributed["auth_method"] == "password"
        assert attributed["target_type"] == "api_key"
        assert attributed["target_id"] == "retained-key"
        assert attributed["severity"] == "warning"
    historical_drift = check_schema_drift(database.url)
    assert len(historical_drift) == 1
    assert historical_drift[0].startswith("('add_table', Table('dashboard_user_invites',")

    for parent in _PARENTS:
        if parent not in database.parents:
            _seed_branch(database.engine, parent)
    populated = _state(database.engine)
    for parent in _PARENTS:
        command.downgrade(_build_alembic_config(database.url), parent)
        assert _revisions(database.engine) == tuple(sorted(_PARENTS))
        assert _state(database.engine) == populated
        assert check_schema_drift(database.url) == historical_drift
        result = run_upgrade(database.url, _MERGE, bootstrap_legacy=False)
        assert result.current_revision == _MERGE
        assert _revisions(database.engine) == (_MERGE,)
        assert _state(database.engine) == populated
        assert check_schema_drift(database.url) == historical_drift

    result = run_upgrade(database.url, "head", bootstrap_legacy=False)
    script = ScriptDirectory.from_config(_build_alembic_config(database.url))
    assert result.current_revision == script.get_heads()[0]
    assert _state(database.engine) == populated
    assert check_schema_drift(database.url) == ()

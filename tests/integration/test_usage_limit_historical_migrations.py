"""Reproduce the observed pre-linear schemas using frozen historical parentage.

The scalar/override DDL is unchanged from snapshot 496b69809f1280ad8695a442239ac38df04a6d93.
Only its graph changed. The parent checkpoints below were observed in PR #1528;
constructing their actual ancestry here does not need local Git history.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from alembic.script.base import Script
from alembic.script.revision import RevisionMap
from sqlalchemy import Connection, create_engine, inspect, text

from app.db import migrate
from app.db.migration_url import to_sync_database_url

pytestmark = pytest.mark.integration
_SCALAR = "20260728_010000_add_account_usage_limits"
_OVERRIDE = "20260910_010000_add_usage_limit_overrides"
_MERGE_ONE = "20261002_000000_merge_usage_limits_and_main_heads"
_MERGE_TWO = "20261002_010000_merge_usage_limit_review_heads"
_UPSTREAM = "20260918_000000_merge_scim_and_overflow_heads"
_PARENTS = (
    "20260816_000000_add_model_source_embeddings",
    "20260830_000000_add_quota_warmup_claim_expiry",
    "20260909_130000_add_request_logs_live_facet_indexes",
    "20260910_000000_request_logs_missing_cost_index",
    "20260912_000000_merge_thread_cache_and_bridge_retirement_heads",
    "20260912_010000_drop_legacy_dashboard_credentials",
    _UPSTREAM,  # Also the earlier name merge_scim_and_overflow_removal; same schema.
)
_COMPAT_USER = "7a4fc02d-216e-5be7-b974-bc437f23df60"


@pytest.fixture
def migration_url(tmp_path: Path, db_setup: bool) -> Iterator[str]:
    del db_setup
    configured = os.environ["CODEX_LB_DATABASE_URL"]
    if not configured.startswith("postgresql"):
        yield f"sqlite:///{tmp_path / 'historical-usage.sqlite'}"
        return
    assert os.environ["CODEX_LB_TEST_DATABASE_URL"] == configured
    engine = create_engine(to_sync_database_url(configured))
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


def _historical_graph(url: str, parent: str) -> ScriptDirectory:
    directory = ScriptDirectory.from_config(migrate._build_alembic_config(url))
    parents = {_SCALAR: parent, _MERGE_ONE: (_SCALAR, _UPSTREAM), _MERGE_TWO: (_OVERRIDE, _MERGE_ONE)}
    scripts = []
    for script in directory.walk_revisions():
        module = ModuleType(script.module.__name__)
        module.__dict__.update(vars(script.module))
        module.__dict__["down_revision"] = parents.get(script.revision, script.down_revision)
        scripts.append(Script(module, script.revision, script.path))
    directory.revision_map = RevisionMap(lambda: iter(scripts))
    return directory


def _install_history(url: str, parent: str, stamp: str) -> None:
    directory = _historical_graph(url, parent)
    engine = create_engine(to_sync_database_url(url))
    try:
        with engine.begin() as connection:
            migrate._ensure_alembic_version_table_capacity_for_connection(connection, required_length=128)
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={
                    "render_as_batch": connection.dialect.name == "sqlite",
                    "fn": lambda heads, _: directory._upgrade_revs(stamp, heads),
                },
            )
            with context.begin_transaction(), Operations.context(context):
                context.run_migrations()
    finally:
        engine.dispose()


def _seed(connection: Connection, *, overrides: bool) -> str:
    connection.execute(
        text(
            "INSERT INTO accounts (id, codex_installation_id, email, plan_type, access_token_encrypted, "
            "refresh_token_encrypted, id_token_encrypted, last_refresh, status, usage_limit_enabled, "
            "usage_limit_percent) VALUES ('saved', 'install', 'saved@example.test', 'plus', :access, :refresh, "
            ":token, '2026-10-02 00:00:00', 'active', TRUE, 20)"
        ),
        {"access": b"access", "refresh": b"refresh", "token": b"id-token"},
    )
    if overrides:
        connection.execute(
            text(
                "UPDATE accounts SET usage_limit_percent = NULL, usage_limit_5h_percent = 30, "
                "usage_limit_weekly_percent = 40"
            )
        )
    connection.execute(text("INSERT INTO audit_logs (action, timestamp) VALUES ('saved', '2026-10-02 00:00:00')"))
    columns = {column["name"] for column in inspect(connection).get_columns("dashboard_settings")}
    legacy = "password_hash" in columns
    password = "legacy-hash" if legacy else "authoritative-hash"
    if legacy:
        connection.execute(
            text(
                "UPDATE dashboard_settings SET password_hash = :password, totp_secret_encrypted = :secret, "
                "totp_last_verified_step = 41 WHERE id = 1"
            ),
            {"password": password, "secret": b"totp"},
        )
    if inspect(connection).has_table("dashboard_users"):
        connection.execute(
            text(
                "INSERT INTO dashboard_users (id, username, role_id, password_hash, totp_secret_encrypted, "
                "totp_last_verified_step, session_generation) SELECT :id, 'renamed-admin', id, :password, :secret, "
                "41, 7 FROM dashboard_roles WHERE slug = 'admin'"
            ),
            {"id": _COMPAT_USER, "password": password, "secret": b"totp"},
        )
    return password


def _assert_upgraded(url: str, *, overrides: bool, password: str) -> None:
    result = migrate.run_upgrade(url, bootstrap_legacy=False)
    head = migrate.inspect_migration_state(url).head_revision
    assert result.current_revision == head
    assert migrate.check_schema_drift(url) == ()
    engine = create_engine(to_sync_database_url(url))
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all() == [head]
            policy = connection.execute(
                text(
                    "SELECT usage_limit_enabled, usage_limit_percent, usage_limit_5h_percent, "
                    "usage_limit_weekly_percent, access_token_encrypted, refresh_token_encrypted, id_token_encrypted "
                    "FROM accounts WHERE id = 'saved'"
                )
            ).one()
            assert policy == (
                True,
                None if overrides else 20.0,
                30.0 if overrides else None,
                40.0 if overrides else None,
                b"access",
                b"refresh",
                b"id-token",
            )
            assert connection.execute(text("SELECT count(*) FROM audit_logs WHERE action = 'saved'")).scalar() == 1
            credential = connection.execute(
                text(
                    "SELECT password_hash, totp_secret_encrypted, totp_last_verified_step FROM dashboard_users "
                    "WHERE id = :id"
                ),
                {"id": _COMPAT_USER},
            ).one()
            assert credential == (password, b"totp", 41)
            assert migrate._read_runtime_sentinel(connection, migrate.LEGACY_CREDENTIALS_RETIRED_SENTINEL) == (
                migrate.CREDENTIAL_DROP_REVISION
            )
            assert migrate._read_runtime_sentinel(connection, "usage_limit_upstream_repair") is None
        assert migrate.run_upgrade(url, bootstrap_legacy=False).current_revision == head
    finally:
        engine.dispose()


@pytest.mark.parametrize("parent", _PARENTS)
@pytest.mark.parametrize("stamp", [_SCALAR, _OVERRIDE])
def test_observed_historical_parent_schemas_upgrade(migration_url: str, parent: str, stamp: str) -> None:
    _install_history(migration_url, parent, stamp)
    engine = create_engine(to_sync_database_url(migration_url))
    try:
        with engine.begin() as connection:
            password = _seed(connection, overrides=stamp == _OVERRIDE)
            assert inspect(connection).has_table("dashboard_users") == (parent in _PARENTS[4:])
    finally:
        engine.dispose()
    _assert_upgraded(migration_url, overrides=stamp == _OVERRIDE, password=password)


@pytest.mark.parametrize("stamp", [_MERGE_ONE, _MERGE_TWO])
def test_actual_historical_merge_schemas_upgrade(migration_url: str, stamp: str) -> None:
    _install_history(migration_url, _PARENTS[0], stamp)
    engine = create_engine(to_sync_database_url(migration_url))
    try:
        with engine.begin() as connection:
            columns = {column["name"] for column in inspect(connection).get_columns("accounts")}
            assert ("usage_limit_5h_percent" in columns) == (stamp == _MERGE_TWO)
            password = _seed(connection, overrides=stamp == _MERGE_TWO)
    finally:
        engine.dispose()
    _assert_upgraded(migration_url, overrides=stamp == _MERGE_TWO, password=password)


@pytest.mark.parametrize("stamp", [_SCALAR, _OVERRIDE, _MERGE_ONE, _MERGE_TWO])
def test_canonical_same_stamps_preserve_retired_credentials(migration_url: str, stamp: str) -> None:
    migrate.run_upgrade(migration_url, stamp, bootstrap_legacy=False)
    engine = create_engine(to_sync_database_url(migration_url))
    try:
        with engine.begin() as connection:
            password = _seed(connection, overrides=stamp != _SCALAR)
    finally:
        engine.dispose()
    _assert_upgraded(migration_url, overrides=stamp != _SCALAR, password=password)


@pytest.mark.parametrize(
    ("stamp", "damage"),
    [(_SCALAR, "mixed-checkpoints"), (_MERGE_TWO, "retired-columns"), (_SCALAR, "retirement-marker")],
)
def test_unknown_marker_combination_fails_before_ledger_or_data_changes(
    migration_url: str, stamp: str, damage: str
) -> None:
    _install_history(migration_url, _PARENTS[0], stamp)
    engine = create_engine(to_sync_database_url(migration_url))
    try:
        with engine.begin() as connection:
            _seed(connection, overrides=stamp == _MERGE_TWO)
            if damage == "mixed-checkpoints":
                connection.execute(text("ALTER TABLE quota_planner_decisions ADD COLUMN lease_expires_at TIMESTAMP"))
            elif damage == "retired-columns":
                connection.execute(text("ALTER TABLE dashboard_settings ADD COLUMN password_hash TEXT"))
            else:
                connection.execute(
                    text("INSERT INTO runtime_sentinels (name, value) VALUES (:name, 'unknown')"),
                    {"name": migrate.LEGACY_CREDENTIALS_RETIRED_SENTINEL},
                )
        with pytest.raises(migrate.MigrationBootstrapError, match="Unrecognized historical usage-limit schema"):
            migrate.run_upgrade(migration_url, bootstrap_legacy=False)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == stamp
            assert connection.execute(text("SELECT count(*) FROM accounts WHERE id = 'saved'")).scalar() == 1
            assert migrate._read_runtime_sentinel(connection, "usage_limit_upstream_repair") is None
    finally:
        engine.dispose()


def test_interrupted_historical_repair_resumes_from_ordinary_ledger(migration_url: str, monkeypatch) -> None:
    _install_history(migration_url, _PARENTS[0], _OVERRIDE)
    engine = create_engine(to_sync_database_url(migration_url))
    try:
        with engine.begin() as connection:
            password = _seed(connection, overrides=True)
        upgrade = command.upgrade

        def interrupt(config, revision):
            upgrade(config, "20260821_000000_add_retry_circuit_admission_generation")
            raise RuntimeError("interrupted after committed upstream work")

        with monkeypatch.context() as patch:
            patch.setattr(command, "upgrade", interrupt)
            with pytest.raises(RuntimeError, match="interrupted"):
                migrate.run_upgrade(migration_url, bootstrap_legacy=False)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT version_num FROM alembic_version")).scalar() == (
                "20260821_000000_add_retry_circuit_admission_generation"
            )
            assert migrate._read_runtime_sentinel(connection, "usage_limit_upstream_repair") == _PARENTS[0]
        assert migrate.run_upgrade(migration_url, "+1", bootstrap_legacy=False).current_revision == (
            "20260826_000000_add_http_bridge_event_chunks"
        )
        assert migrate.run_upgrade(migration_url, _SCALAR, bootstrap_legacy=False).current_revision == _SCALAR
        with engine.connect() as connection:
            assert migrate._read_runtime_sentinel(connection, "usage_limit_upstream_repair") == _PARENTS[0]
        assert migrate.run_upgrade(migration_url, "+1", bootstrap_legacy=False).current_revision == _OVERRIDE
        with engine.connect() as connection:
            assert migrate._read_runtime_sentinel(connection, "usage_limit_upstream_repair") is None
        _assert_upgraded(migration_url, overrides=True, password=password)
    finally:
        engine.dispose()


def test_successful_non_head_repair_can_downgrade_and_upgrade(migration_url: str) -> None:
    _install_history(migration_url, _PARENTS[0], _SCALAR)
    engine = create_engine(to_sync_database_url(migration_url))
    try:
        with engine.begin() as connection:
            password = _seed(connection, overrides=False)
        # Resolve +1 against the historical scalar stamp, not its upstream
        # checkpoint, and finish the repair at the canonical override schema.
        assert migrate.run_upgrade(migration_url, "+1", bootstrap_legacy=False).current_revision == _OVERRIDE
        with engine.connect() as connection:
            assert migrate._read_runtime_sentinel(connection, "usage_limit_upstream_repair") is None
        command.downgrade(migrate._build_alembic_config(migration_url), _SCALAR)
        _assert_upgraded(migration_url, overrides=False, password=password)
    finally:
        engine.dispose()


def test_repair_interrupted_after_head_stamp_cleans_marker_on_retry(migration_url: str, monkeypatch) -> None:
    _install_history(migration_url, _PARENTS[0], _OVERRIDE)
    engine = create_engine(to_sync_database_url(migration_url))
    try:
        with engine.begin() as connection:
            password = _seed(connection, overrides=True)
        upgrade = command.upgrade

        def interrupt(config, revision):
            upgrade(config, revision)
            raise RuntimeError("interrupted after head stamp")

        with monkeypatch.context() as patch:
            patch.setattr(command, "upgrade", interrupt)
            with pytest.raises(RuntimeError, match="interrupted after head stamp"):
                migrate.run_upgrade(migration_url, bootstrap_legacy=False)
        with engine.connect() as connection:
            assert migrate._read_runtime_sentinel(connection, "usage_limit_upstream_repair") == _PARENTS[0]
        _assert_upgraded(migration_url, overrides=True, password=password)
    finally:
        engine.dispose()


@pytest.mark.parametrize("stamp", [_SCALAR, _OVERRIDE, _MERGE_ONE, _MERGE_TWO])
def test_canonical_index_drift_remains_reportable_without_blocking_upgrade(
    migration_url: str, stamp: str, monkeypatch
) -> None:
    migrate.run_upgrade(migration_url, stamp, bootstrap_legacy=False)
    engine = create_engine(to_sync_database_url(migration_url))
    missing_index = repr(("missing_index", "request_logs", "idx_logs_missing_cost"))
    try:
        with engine.begin() as connection:
            _seed(connection, overrides=stamp != _SCALAR)
            connection.execute(text("DROP INDEX idx_logs_missing_cost"))
        before = migrate.check_schema_drift(migration_url)
        assert missing_index in before
        with monkeypatch.context() as patch:
            if stamp == _MERGE_TWO:

                def unexpected_upgrade(*args, **kwargs):
                    pytest.fail("canonical head must retain the existing upgrade skip")

                patch.setattr(command, "upgrade", unexpected_upgrade)
            result = migrate.run_upgrade(migration_url, bootstrap_legacy=False)
        assert result.current_revision == migrate.inspect_migration_state(migration_url).head_revision
        after = migrate.check_schema_drift(migration_url)
        assert missing_index in after
        if stamp == _MERGE_TWO:
            assert after == before
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT password_hash FROM dashboard_users WHERE id = :id"), {"id": _COMPAT_USER}
                ).scalar()
                == "authoritative-hash"
            )
            assert migrate._read_runtime_sentinel(connection, "usage_limit_upstream_repair") is None
    finally:
        engine.dispose()


def test_historical_merge_one_repairs_overrides_despite_upstream_index_drift(migration_url: str) -> None:
    _install_history(migration_url, _PARENTS[0], _MERGE_ONE)
    engine = create_engine(to_sync_database_url(migration_url))
    try:
        with engine.begin() as connection:
            _seed(connection, overrides=False)
            connection.execute(text("DROP INDEX idx_logs_missing_cost"))
        result = migrate.run_upgrade(migration_url, bootstrap_legacy=False)
        assert result.current_revision == migrate.inspect_migration_state(migration_url).head_revision
        assert repr(("missing_index", "request_logs", "idx_logs_missing_cost")) in migrate.check_schema_drift(
            migration_url
        )
        with engine.connect() as connection:
            assert connection.execute(
                text(
                    "SELECT usage_limit_enabled, usage_limit_percent, usage_limit_5h_percent, "
                    "usage_limit_weekly_percent FROM accounts WHERE id = 'saved'"
                )
            ).one() == (True, 20.0, None, None)
            assert migrate._read_runtime_sentinel(connection, "usage_limit_upstream_repair") is None
    finally:
        engine.dispose()

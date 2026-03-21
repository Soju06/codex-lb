from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from alembic.util.exc import CommandError
from sqlalchemy import create_engine, inspect, text
from sqlalchemy import exc as sa_exc
from sqlalchemy.engine import Connection

import app.db.migrate as migrate_module
from app.db.alembic.revision_ids import OLD_TO_NEW_REVISION_MAP
from app.db.backup import create_sqlite_pre_migration_backup, list_sqlite_pre_migration_backups
from app.db.migrate import (
    MigrationBootstrapError,
    _build_alembic_config,
    _collect_migration_policy_violations,
    _ensure_alembic_version_table_capacity_for_connection,
    _max_revision_id_length,
    _read_current_revisions_from_connection,
    check_migration_policy,
    check_schema_drift,
    inspect_migration_state,
    run_upgrade,
)
from app.db.migration_url import to_sync_database_url
from app.db.models import Base
from app.modules.usage.additional_quota_keys import clear_additional_quota_registry_cache


def _db_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path}"


def test_check_schema_drift_disposes_sync_engine(monkeypatch) -> None:
    class _FakeConnectionContext:
        def __init__(self) -> None:
            self.connection = object()

        def __enter__(self) -> object:
            return self.connection

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    class _FakeEngine:
        def __init__(self) -> None:
            self.connection_context = _FakeConnectionContext()
            self.disposed = False

        def connect(self) -> _FakeConnectionContext:
            return self.connection_context

        def dispose(self) -> None:
            self.disposed = True

    fake_engine = _FakeEngine()

    monkeypatch.setattr(migrate_module, "create_engine", lambda *args, **kwargs: fake_engine)
    monkeypatch.setattr(
        migrate_module.MigrationContext,
        "configure",
        lambda *, connection, opts: SimpleNamespace(connection=connection, opts=opts),
    )
    monkeypatch.setattr(migrate_module, "compare_metadata", lambda context, metadata: [])
    monkeypatch.setattr(migrate_module, "_manual_schema_drift_diffs", lambda connection: ())

    assert check_schema_drift("sqlite+aiosqlite:///tmp/drift.db") == ()
    assert fake_engine.disposed is True


def test_inspect_migration_state_requires_upgrade_when_uninitialized(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.db"
    state = inspect_migration_state(_db_url(db_path))

    assert state.needs_upgrade is True
    assert state.current_revision is None
    assert state.has_alembic_version_table is False


def test_inspect_migration_state_no_upgrade_after_head(tmp_path: Path) -> None:
    db_path = tmp_path / "head.db"
    url = _db_url(db_path)

    result = run_upgrade(url, "head", bootstrap_legacy=False)
    state = inspect_migration_state(url)

    assert result.current_revision == state.head_revision
    assert state.needs_upgrade is False
    assert state.current_revision == state.head_revision
    assert state.has_alembic_version_table is True


def test_schema_migration_contract_matches_after_upgrade(tmp_path: Path) -> None:
    """Prisma-style contract: migrated schema must match ORM metadata and policy."""
    db_path = tmp_path / "contract.db"
    url = _db_url(db_path)

    run_upgrade(url, "head", bootstrap_legacy=False)

    assert check_migration_policy(url) == ()
    assert check_schema_drift(url) == ()


def test_base_revision_does_not_depend_on_live_metadata(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "base.db"
    url = _db_url(db_path)

    def _raise_create_all(*_: object, **__: object) -> None:
        raise AssertionError("base revision must not call Base.metadata.create_all")

    monkeypatch.setattr(Base.metadata, "create_all", _raise_create_all)

    base_revision = OLD_TO_NEW_REVISION_MAP["000_base_schema"]
    result = run_upgrade(url, base_revision, bootstrap_legacy=False)
    assert result.current_revision == base_revision


def test_request_logs_transport_stays_in_additive_migration_chain(tmp_path: Path) -> None:
    db_path = tmp_path / "request-logs-transport.db"
    url = _db_url(db_path)
    base_revision = OLD_TO_NEW_REVISION_MAP["000_base_schema"]
    transport_revision = "20260310_000000_add_request_logs_transport"

    run_upgrade(url, base_revision, bootstrap_legacy=False)

    sync_url = to_sync_database_url(url)
    with create_engine(sync_url, future=True).connect() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("request_logs")}
        assert "transport" in columns

    result = run_upgrade(url, transport_revision, bootstrap_legacy=False)
    assert result.current_revision == transport_revision

    with create_engine(sync_url, future=True).connect() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("request_logs")}
        assert "transport" in columns


def test_check_schema_drift_detects_rogue_table(tmp_path: Path) -> None:
    db_path = tmp_path / "drift.db"
    url = _db_url(db_path)

    run_upgrade(url, "head", bootstrap_legacy=False)
    assert check_schema_drift(url) == ()

    sync_url = to_sync_database_url(url)
    with create_engine(sync_url, future=True).connect() as connection:
        connection.execute(text("CREATE TABLE rogue_table (id INTEGER PRIMARY KEY)"))
        connection.commit()

    drift = check_schema_drift(url)
    assert drift
    assert any("rogue_table" in diff for diff in drift)


def test_check_schema_drift_detects_missing_manual_performance_index(tmp_path: Path) -> None:
    db_path = tmp_path / "missing-index.db"
    url = _db_url(db_path)

    run_upgrade(url, "head", bootstrap_legacy=False)

    sync_url = to_sync_database_url(url)
    with create_engine(sync_url, future=True).connect() as connection:
        connection.execute(text("DROP INDEX idx_usage_window_account_latest"))
        connection.commit()

    drift = check_schema_drift(url)
    assert any("idx_usage_window_account_latest" in diff for diff in drift)


def test_run_upgrade_auto_remaps_legacy_revision_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "remap.db"
    url = _db_url(db_path)

    initial = run_upgrade(url, "head", bootstrap_legacy=False)
    assert initial.current_revision is not None

    sync_url = to_sync_database_url(url)
    with create_engine(sync_url, future=True).begin() as connection:
        connection.execute(
            text("UPDATE alembic_version SET version_num = :legacy"),
            {"legacy": "013_add_dashboard_settings_routing_strategy"},
        )

    result = run_upgrade(url, "head", bootstrap_legacy=False)
    assert result.current_revision == initial.current_revision


def test_run_upgrade_without_auto_remap_fails_for_legacy_revision_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "no-remap.db"
    url = _db_url(db_path)

    run_upgrade(url, "head", bootstrap_legacy=False)

    sync_url = to_sync_database_url(url)
    with create_engine(sync_url, future=True).begin() as connection:
        connection.execute(
            text("UPDATE alembic_version SET version_num = :legacy"),
            {"legacy": "013_add_dashboard_settings_routing_strategy"},
        )

    with pytest.raises(CommandError, match="Can't locate revision identified by"):
        run_upgrade(url, "head", bootstrap_legacy=False, auto_remap_legacy_revisions=False)


def test_run_upgrade_repairs_branched_legacy_revision_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "branch-repair.db"
    url = _db_url(db_path)

    ancestor = "20260218_000100_add_import_without_overwrite_and_drop_accounts_email_unique"
    run_upgrade(url, ancestor, bootstrap_legacy=False)

    sync_url = to_sync_database_url(url)
    with create_engine(sync_url, future=True).begin() as connection:
        connection.execute(text("ALTER TABLE api_keys ADD COLUMN enforced_model VARCHAR"))
        connection.execute(text("ALTER TABLE api_keys ADD COLUMN enforced_reasoning_effort VARCHAR"))
        connection.execute(
            text("UPDATE alembic_version SET version_num = :legacy"),
            {"legacy": "013_add_api_key_enforcement_fields"},
        )

    result = run_upgrade(url, "head", bootstrap_legacy=False)
    assert result.current_revision is not None

    with create_engine(sync_url, future=True).connect() as connection:
        inspector = inspect(connection)
        dashboard_columns = {column["name"] for column in inspector.get_columns("dashboard_settings")}
        api_key_columns = {column["name"] for column in inspector.get_columns("api_keys")}

        assert "routing_strategy" in dashboard_columns
        assert "enforced_model" in api_key_columns
        assert "enforced_reasoning_effort" in api_key_columns
        assert inspector.has_table("api_firewall_allowlist")


def test_run_upgrade_repairs_branched_legacy_revision_ids_with_parallel_head(tmp_path: Path) -> None:
    db_path = tmp_path / "branch-repair-parallel.db"
    url = _db_url(db_path)

    run_upgrade(url, "20260228_030000_add_api_firewall_allowlist", bootstrap_legacy=False)

    sync_url = to_sync_database_url(url)
    with create_engine(sync_url, future=True).begin() as connection:
        connection.execute(text("ALTER TABLE api_keys ADD COLUMN enforced_model VARCHAR"))
        connection.execute(text("ALTER TABLE api_keys ADD COLUMN enforced_reasoning_effort VARCHAR"))
        connection.execute(text("DELETE FROM alembic_version"))
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": "013_add_api_key_enforcement_fields"},
        )
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": "014_add_api_firewall_allowlist"},
        )

    result = run_upgrade(url, "head", bootstrap_legacy=False)
    assert result.current_revision == inspect_migration_state(url).head_revision


def test_run_upgrade_backfills_additional_usage_quota_key_from_configured_registry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quota-registry.db"
    url = _db_url(db_path)
    registry_path = tmp_path / "additional_quota_registry.json"
    registry_path.write_text(
        json.dumps(
            [
                {
                    "quota_key": "spark_enterprise",
                    "display_label": "Spark Enterprise",
                    "limit_name_aliases": ["codex_other"],
                    "metered_feature_aliases": ["codex_bengalfox"],
                }
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("CODEX_LB_ADDITIONAL_QUOTA_REGISTRY_FILE", str(registry_path))
    clear_additional_quota_registry_cache()

    run_upgrade(url, "20260309_000000_add_additional_usage_history", bootstrap_legacy=False)

    sync_url = to_sync_database_url(url)
    recorded_at = datetime.now(timezone.utc)
    with create_engine(sync_url, future=True).begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO accounts (
                    id,
                    email,
                    plan_type,
                    access_token_encrypted,
                    refresh_token_encrypted,
                    id_token_encrypted,
                    last_refresh,
                    status,
                    deactivation_reason,
                    chatgpt_account_id,
                    reset_at
                ) VALUES (
                    :id,
                    :email,
                    :plan_type,
                    :access_token_encrypted,
                    :refresh_token_encrypted,
                    :id_token_encrypted,
                    :last_refresh,
                    :status,
                    :deactivation_reason,
                    :chatgpt_account_id,
                    :reset_at
                )
                """
            ),
            {
                "id": "acc_registry",
                "email": "registry@example.com",
                "plan_type": "plus",
                "access_token_encrypted": b"access",
                "refresh_token_encrypted": b"refresh",
                "id_token_encrypted": b"id",
                "last_refresh": recorded_at,
                "status": "active",
                "deactivation_reason": None,
                "chatgpt_account_id": None,
                "reset_at": None,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO additional_usage_history (
                    account_id,
                    limit_name,
                    metered_feature,
                    window,
                    used_percent,
                    reset_at,
                    window_minutes,
                    recorded_at
                ) VALUES (
                    :account_id,
                    :limit_name,
                    :metered_feature,
                    :window,
                    :used_percent,
                    :reset_at,
                    :window_minutes,
                    :recorded_at
                )
                """
            ),
            {
                "account_id": "acc_registry",
                "limit_name": "codex_other",
                "metered_feature": "codex_bengalfox",
                "window": "primary",
                "used_percent": 12.5,
                "reset_at": None,
                "window_minutes": 60,
                "recorded_at": recorded_at,
            },
        )

    run_upgrade(url, "head", bootstrap_legacy=False)

    with create_engine(sync_url, future=True).connect() as connection:
        quota_key = connection.execute(text("SELECT quota_key FROM additional_usage_history")).scalar_one()

    assert quota_key == "spark_enterprise"
    clear_additional_quota_registry_cache()


def test_run_upgrade_rejects_duplicate_additional_quota_aliases_in_registry(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "quota-registry-invalid.db"
    url = _db_url(db_path)
    registry_path = tmp_path / "additional_quota_registry.json"
    registry_path.write_text(
        json.dumps(
            [
                {
                    "quota_key": "spark_enterprise",
                    "display_label": "Spark Enterprise",
                    "limit_name_aliases": ["codex_other"],
                },
                {
                    "quota_key": "spark_enterprise_backup",
                    "display_label": "Spark Enterprise Backup",
                    "limit_name_aliases": ["codex_other"],
                },
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("CODEX_LB_ADDITIONAL_QUOTA_REGISTRY_FILE", str(registry_path))
    clear_additional_quota_registry_cache()

    run_upgrade(url, "20260309_000000_add_additional_usage_history", bootstrap_legacy=False)

    with pytest.raises(ValueError, match="duplicate additional quota alias"):
        run_upgrade(url, "head", bootstrap_legacy=False)

    clear_additional_quota_registry_cache()


def test_run_upgrade_fails_for_unsupported_alembic_version_id(tmp_path: Path) -> None:
    db_path = tmp_path / "unsupported.db"
    url = _db_url(db_path)

    run_upgrade(url, "head", bootstrap_legacy=False)

    sync_url = to_sync_database_url(url)
    with create_engine(sync_url, future=True).begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num = 'legacy_custom_999'"))

    with pytest.raises(MigrationBootstrapError, match="Unsupported alembic_version revision ids"):
        run_upgrade(url, "head", bootstrap_legacy=False)


def test_check_migration_policy_reports_head_and_format_violations(monkeypatch, tmp_path: Path) -> None:
    class _FakeRevision:
        def __init__(self, revision: str, path: str) -> None:
            self.revision = revision
            self.path = path

    class _FakeScriptDirectory:
        def get_heads(self) -> list[str]:
            return ["head_a", "head_b"]

        def walk_revisions(self) -> list[_FakeRevision]:
            return [
                _FakeRevision("invalid-revision-id", "/tmp/not-matching-name.py"),
            ]

    fake_script_dir = _FakeScriptDirectory()
    monkeypatch.setattr("app.db.migrate.ScriptDirectory.from_config", lambda _: fake_script_dir)

    config = _build_alembic_config(_db_url(tmp_path / "policy.db"))
    violations = _collect_migration_policy_violations(config)

    assert any("alembic_head_count_invalid" in violation for violation in violations)
    assert any("alembic_revision_id_format_invalid" in violation for violation in violations)
    assert any("alembic_revision_filename_mismatch" in violation for violation in violations)

    wrapper_violations = check_migration_policy(_db_url(tmp_path / "policy-wrapper.db"))
    assert wrapper_violations == violations


def test_create_sqlite_pre_migration_backup_rotates_old_files(tmp_path: Path) -> None:
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite-bytes")

    created: list[Path] = []
    base_time = datetime(2026, 2, 13, 12, 0, 0, tzinfo=timezone.utc)

    for index in range(3):
        backup = create_sqlite_pre_migration_backup(
            db_path,
            max_files=2,
            now=base_time + timedelta(minutes=index),
        )
        created.append(backup)

    backups = list_sqlite_pre_migration_backups(db_path)
    assert len(backups) == 2
    assert backups == created[-2:]
    assert backups[0].read_bytes() == b"sqlite-bytes"
    assert backups[1].read_bytes() == b"sqlite-bytes"


class _FakeStringType:
    def __init__(self, length: int | None) -> None:
        self.length = length


class _FakeConnection:
    def __init__(self, *, dialect_name: str = "postgresql") -> None:
        self.dialect = SimpleNamespace(name=dialect_name)
        self.executed_sql: list[str] = []

    def execute(self, statement: object) -> None:
        self.executed_sql.append(str(statement))


class _MissingAlembicVersionConnection:
    def execute(self, statement: object) -> None:
        raise sa_exc.ProgrammingError(
            str(statement),
            {},
            Exception('relation "alembic_version" does not exist'),
        )


class _FakeInspector:
    def __init__(self, *, has_table: bool, version_num_length: int | None = None) -> None:
        self._has_table = has_table
        self._version_num_length = version_num_length

    def has_table(self, table_name: str) -> bool:
        assert table_name == "alembic_version"
        return self._has_table

    def get_columns(self, table_name: str) -> list[dict[str, object]]:
        assert table_name == "alembic_version"
        return [
            {
                "name": "version_num",
                "type": _FakeStringType(self._version_num_length),
            }
        ]


def test_ensure_alembic_version_table_capacity_creates_table_when_missing(monkeypatch) -> None:
    connection = _FakeConnection()
    inspector = _FakeInspector(has_table=False)
    monkeypatch.setattr("app.db.migrate.inspect", lambda _: inspector)

    _ensure_alembic_version_table_capacity_for_connection(connection, required_length=64)  # type: ignore[arg-type]

    assert connection.executed_sql == [
        "CREATE TABLE alembic_version ( version_num VARCHAR(64) NOT NULL, PRIMARY KEY (version_num) )"
    ]


def test_ensure_alembic_version_table_capacity_alters_short_column(monkeypatch) -> None:
    connection = _FakeConnection()
    inspector = _FakeInspector(has_table=True, version_num_length=32)
    monkeypatch.setattr("app.db.migrate.inspect", lambda _: inspector)

    _ensure_alembic_version_table_capacity_for_connection(connection, required_length=64)  # type: ignore[arg-type]

    assert connection.executed_sql == ["ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(64)"]


def test_read_current_revisions_returns_empty_when_alembic_version_table_is_missing() -> None:
    connection = _MissingAlembicVersionConnection()

    assert _read_current_revisions_from_connection(cast(Connection, connection)) == ()


def test_max_revision_id_length_exceeds_alembic_default(tmp_path: Path) -> None:
    db_path = tmp_path / "length-check.db"
    config = _build_alembic_config(_db_url(db_path))

    assert _max_revision_id_length(config) > 32

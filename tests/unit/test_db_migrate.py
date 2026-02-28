from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.util.exc import CommandError
from sqlalchemy import create_engine, text

from app.db.alembic.revision_ids import OLD_TO_NEW_REVISION_MAP
from app.db.backup import create_sqlite_pre_migration_backup, list_sqlite_pre_migration_backups
from app.db.migrate import (
    MigrationBootstrapError,
    _build_alembic_config,
    _collect_migration_policy_violations,
    _ensure_alembic_version_table_capacity_for_connection,
    _max_revision_id_length,
    check_migration_policy,
    check_schema_drift,
    inspect_migration_state,
    run_upgrade,
)
from app.db.migration_url import to_sync_database_url
from app.db.models import Base


def _db_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path}"


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


def test_max_revision_id_length_exceeds_alembic_default(tmp_path: Path) -> None:
    db_path = tmp_path / "length-check.db"
    config = _build_alembic_config(_db_url(db_path))

    assert _max_revision_id_length(config) > 32

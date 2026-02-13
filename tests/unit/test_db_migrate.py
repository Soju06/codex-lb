from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, text

from app.db.backup import create_sqlite_pre_migration_backup, list_sqlite_pre_migration_backups
from app.db.migrate import check_schema_drift, inspect_migration_state, run_upgrade
from app.db.migration_url import to_sync_database_url


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

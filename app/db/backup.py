from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _backup_name(source: Path, timestamp: datetime) -> str:
    return f"{source.stem}.pre-migrate-{timestamp.strftime('%Y%m%dT%H%M%SZ')}{source.suffix}"


def _next_backup_path(source: Path, timestamp: datetime) -> Path:
    base_name = _backup_name(source, timestamp)
    candidate = source.parent / base_name
    if not candidate.exists():
        return candidate

    sequence = 1
    while True:
        name = f"{source.stem}.pre-migrate-{timestamp.strftime('%Y%m%dT%H%M%SZ')}-{sequence}{source.suffix}"
        candidate = source.parent / name
        if not candidate.exists():
            return candidate
        sequence += 1


def list_sqlite_pre_migration_backups(source: Path) -> list[Path]:
    pattern = f"{source.stem}.pre-migrate-*{source.suffix}"
    return sorted((path for path in source.parent.glob(pattern) if path.is_file()))


def _sqlite_backup(source: Path, backup_path: Path) -> None:
    source_mode = source.stat().st_mode
    with sqlite3.connect(source) as source_conn:
        with sqlite3.connect(backup_path) as backup_conn:
            source_conn.backup(backup_conn)
            backup_conn.execute("PRAGMA journal_mode=DELETE")
    backup_path.chmod(source_mode)


def create_sqlite_pre_migration_backup(
    source: Path,
    *,
    max_files: int,
    now: datetime | None = None,
) -> Path:
    if max_files < 1:
        raise ValueError("max_files must be >= 1")
    if not source.exists():
        raise FileNotFoundError(f"sqlite database not found: {source}")

    timestamp = now or datetime.now(timezone.utc)
    backup_path = _next_backup_path(source, timestamp)

    _sqlite_backup(source, backup_path)

    backups = list_sqlite_pre_migration_backups(source)
    excess = len(backups) - max_files
    if excess > 0:
        for old_backup in backups[:excess]:
            old_backup.unlink()

    return backup_path

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import app.db.recover as recover_module
import app.db.sqlite_utils as sqlite_utils_module
from app.db.backup import create_sqlite_pre_migration_backup


class _TrackedConnection(sqlite3.Connection):
    __slots__ = ("closed",)

    def __init__(self, database: str) -> None:
        super().__init__(database)
        self.closed = False

    def close(self) -> None:
        self.closed = True
        super().close()


def _track_connections(monkeypatch: pytest.MonkeyPatch) -> list[_TrackedConnection]:
    connections: list[_TrackedConnection] = []

    def connect(database: str | Path) -> sqlite3.Connection:
        connection = _TrackedConnection(str(database))
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite_utils_module.sqlite3, "connect", connect)
    return connections


def _close_connections(connections: list[_TrackedConnection]) -> None:
    for connection in connections:
        connection.close()


def test_backup_closes_connections_before_rotating_old_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "store.db"
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        connection.execute("INSERT INTO items (name) VALUES ('alpha')")

    connections = _track_connections(monkeypatch)
    base_time = datetime(2026, 7, 29, tzinfo=timezone.utc)

    try:
        first_backup = create_sqlite_pre_migration_backup(db_path, max_files=1, now=base_time)
        second_backup = create_sqlite_pre_migration_backup(
            db_path,
            max_files=1,
            now=base_time + timedelta(minutes=1),
        )

        assert not first_backup.exists()
        assert second_backup.exists()
        assert connections
        assert all(connection.closed for connection in connections)
    finally:
        _close_connections(connections)


def test_recover_cli_closes_connections_before_replacing_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "store.db"
    output_path = tmp_path / "recovered.db"
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        connection.execute("INSERT INTO items (name) VALUES ('alpha')")

    connections = _track_connections(monkeypatch)

    try:
        exit_code = recover_module.main(
            [
                "--db",
                str(db_path),
                "--output",
                str(output_path),
                "--replace",
            ]
        )

        corrupt_backups = list(tmp_path.glob("store.db.corrupt-*"))
        assert exit_code == 0
        assert len(corrupt_backups) == 1
        assert db_path.exists()
        assert not output_path.exists()
        assert connections
        assert all(connection.closed for connection in connections)

        with closing(sqlite3.connect(db_path)) as connection:
            assert connection.execute("SELECT name FROM items").fetchall() == [("alpha",)]
    finally:
        _close_connections(connections)


def test_runstate_round_trips(tmp_path: Path) -> None:
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")

    assert sqlite_utils_module.read_sqlite_runstate(db_path) is None

    assert sqlite_utils_module.write_sqlite_runstate(db_path, sqlite_utils_module.SqliteRunState.RUNNING) is True
    assert sqlite_utils_module.read_sqlite_runstate(db_path) is sqlite_utils_module.SqliteRunState.RUNNING

    assert sqlite_utils_module.write_sqlite_runstate(db_path, sqlite_utils_module.SqliteRunState.CLEAN) is True
    assert sqlite_utils_module.read_sqlite_runstate(db_path) is sqlite_utils_module.SqliteRunState.CLEAN


def test_runstate_reads_unrecognized_content_as_unknown(tmp_path: Path) -> None:
    db_path = tmp_path / "store.db"
    sqlite_utils_module.sqlite_runstate_path(db_path).write_text("half-written", encoding="utf-8")

    assert sqlite_utils_module.read_sqlite_runstate(db_path) is None


def test_runstate_write_failure_clears_a_stale_clean_marker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A store left mid-write must never read back as cleanly closed."""
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")
    sqlite_utils_module.write_sqlite_runstate(db_path, sqlite_utils_module.SqliteRunState.CLEAN)
    assert sqlite_utils_module.read_sqlite_runstate(db_path) is sqlite_utils_module.SqliteRunState.CLEAN

    def _explode(*_args: object, **_kwargs: object) -> None:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(os, "replace", _explode)

    assert sqlite_utils_module.write_sqlite_runstate(db_path, sqlite_utils_module.SqliteRunState.RUNNING) is False

    monkeypatch.undo()
    assert sqlite_utils_module.read_sqlite_runstate(db_path) is None
    assert not sqlite_utils_module.sqlite_runstate_path(db_path).exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_runstate_clean_is_ignored_after_the_database_file_changes(tmp_path: Path) -> None:
    """Restoring a backup must not inherit the previous file's clean record."""
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite-original")
    sqlite_utils_module.write_sqlite_runstate(db_path, sqlite_utils_module.SqliteRunState.CLEAN)
    assert sqlite_utils_module.read_sqlite_runstate(db_path) is sqlite_utils_module.SqliteRunState.CLEAN

    db_path.write_bytes(b"sqlite-restored-from-a-backup")

    assert sqlite_utils_module.read_sqlite_runstate(db_path) is None


def test_runstate_running_survives_database_writes(tmp_path: Path) -> None:
    """Only the clean record is fenced; a running record stays readable."""
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")
    sqlite_utils_module.write_sqlite_runstate(db_path, sqlite_utils_module.SqliteRunState.RUNNING)

    db_path.write_bytes(b"sqlite-after-a-few-writes")

    assert sqlite_utils_module.read_sqlite_runstate(db_path) is sqlite_utils_module.SqliteRunState.RUNNING


def test_runstate_reads_invalid_utf8_as_unknown(tmp_path: Path) -> None:
    """A corrupt sidecar must not abort startup before the integrity check."""
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")
    sqlite_utils_module.sqlite_runstate_path(db_path).write_bytes(b'{"state": "clean", "\xff\xfe": 1}')

    assert sqlite_utils_module.read_sqlite_runstate(db_path) is None


def test_runstate_clean_is_ignored_after_a_timestamp_preserving_restore(tmp_path: Path) -> None:
    """`tar -x` and `cp -p` reproduce size and mtime; the inode still moves."""
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"A" * 4096)
    sqlite_utils_module.write_sqlite_runstate(db_path, sqlite_utils_module.SqliteRunState.CLEAN)
    original = db_path.stat()

    db_path.unlink()
    db_path.write_bytes(b"B" * 4096)
    os.utime(db_path, ns=(original.st_atime_ns, original.st_mtime_ns))

    restored = db_path.stat()
    assert restored.st_size == original.st_size
    assert restored.st_mtime_ns == original.st_mtime_ns
    assert sqlite_utils_module.read_sqlite_runstate(db_path) is None


def test_runstate_write_syncs_the_payload_and_the_directory_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A lost run-state transition would let the next startup skip the scan."""
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")
    synced: list[int] = []
    real_fsync = os.fsync

    def _record(fd: int) -> None:
        synced.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", _record)

    assert sqlite_utils_module.write_sqlite_runstate(db_path, sqlite_utils_module.SqliteRunState.CLEAN) is True

    # One for the sidecar payload, one for the directory entry the rename created.
    assert len(synced) == 2

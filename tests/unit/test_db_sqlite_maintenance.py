from __future__ import annotations

import errno
import json
import os
import selectors
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import app.db.recover as recover_module
import app.db.sqlite_utils as sqlite_utils_module
from app.db.backup import create_sqlite_pre_migration_backup


def _start_subprocess_lock_holder(db_path: Path) -> subprocess.Popen[str]:
    script = """
import sys
from pathlib import Path

from app.db.sqlite_utils import acquire_sqlite_runstate_lock

connection = acquire_sqlite_runstate_lock(Path(sys.argv[1]))
print("ready", flush=True)
sys.stdin.readline()
connection.rollback()
connection.close()
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(db_path)],
        cwd=Path(__file__).resolve().parents[2],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    with selectors.DefaultSelector() as ready_selector:
        ready_selector.register(process.stdout, selectors.EVENT_READ)
        assert ready_selector.select(timeout=5), "lock holder did not become ready"
    assert process.stdout.readline().strip() == "ready"
    return process


def _stop_subprocess(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    if process.stdin is not None:
        process.stdin.close()
    if process.stdout is not None:
        process.stdout.close()
    if process.stderr is not None:
        process.stderr.close()


class _TrackedConnection(sqlite3.Connection):
    __slots__ = ("closed",)

    def __init__(self, database: str) -> None:
        super().__init__(database)
        self.closed = False

    def close(self) -> None:
        self.closed = True
        super().close()


class _RollbackFailingConnection(_TrackedConnection):
    def rollback(self) -> None:
        raise sqlite3.OperationalError("simulated rollback failure")


def _track_connections(monkeypatch: pytest.MonkeyPatch) -> list[_TrackedConnection]:
    connections: list[_TrackedConnection] = []

    def connect(database: str | Path, *_args: object, **_kwargs: object) -> sqlite3.Connection:
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
    rename_checks: list[bool] = []
    unlink_checks: list[bool] = []
    path_type = type(db_path)
    real_replace = path_type.replace
    real_unlink = path_type.unlink

    def replace_without_open_sqlite_handles(path: Path, target: str | Path) -> Path:
        rename_checks.append(all(connection.closed for connection in connections))
        return real_replace(path, target)

    tracked_sidecars = {
        *(db_path.with_name(f"{db_path.name}{suffix}") for suffix in ("-wal", "-shm", "-journal")),
        *(output_path.with_name(f"{output_path.name}{suffix}") for suffix in ("-wal", "-shm", "-journal")),
    }

    def unlink_without_open_sqlite_handles(path: Path, *, missing_ok: bool = False) -> None:
        if path in tracked_sidecars:
            unlink_checks.append(all(connection.closed for connection in connections))
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(path_type, "replace", replace_without_open_sqlite_handles)
    monkeypatch.setattr(path_type, "unlink", unlink_without_open_sqlite_handles)

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
        assert rename_checks == [True, True]
        assert unlink_checks
        assert all(unlink_checks)

        with closing(sqlite3.connect(db_path)) as connection:
            assert connection.execute("SELECT name FROM items").fetchall() == [("alpha",)]
    finally:
        _close_connections(connections)


def test_recovery_lock_closes_connection_when_rollback_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "store.db"
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute("CREATE TABLE items (name TEXT NOT NULL)")

    recovery_connection = _RollbackFailingConnection(str(db_path))

    def connect_with_rollback_failure(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        return recovery_connection

    monkeypatch.setattr(recover_module.sqlite3, "connect", connect_with_rollback_failure)

    with pytest.raises(sqlite3.OperationalError, match="simulated rollback failure"):
        with recover_module._sqlite_recovery_lock(db_path):
            pass

    assert recovery_connection.closed


def test_recover_replace_removes_sqlite_sidecars_before_installing_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source WAL must not attach to the recovered database after rename."""
    db_path = tmp_path / "store.db"
    output_path = tmp_path / "recovered.db"
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute("CREATE TABLE items (name TEXT NOT NULL)")
        connection.execute("INSERT INTO items (name) VALUES ('base')")

    # Capture a valid WAL containing a row absent from the base file. Restore
    # the base bytes before recovery so the sidecars can be recreated after the
    # locked export and prove that cleanup prevents them attaching on rename.
    base_bytes = db_path.read_bytes()
    stale_connection = sqlite3.connect(db_path)
    stale_connection.execute("PRAGMA journal_mode=WAL")
    stale_connection.execute("PRAGMA wal_autocheckpoint=0")
    stale_connection.execute("INSERT INTO items (name) VALUES ('stale-after-dump')")
    stale_connection.commit()
    stale_sidecars = {suffix: Path(f"{db_path}{suffix}").read_bytes() for suffix in ("-wal", "-shm")}
    stale_connection.close()
    db_path.write_bytes(base_bytes)
    for suffix in ("-wal", "-shm", "-journal"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)

    for suffix in ("-wal", "-shm", "-journal", "-mj12345678"):
        (tmp_path / f"{output_path.name}{suffix}").write_bytes(b"stale output sidecar")

    real_load_dump = recover_module._load_dump

    def _load_dump_then_leave_source_wal(connection: sqlite3.Connection) -> str:
        dump = real_load_dump(connection)
        for suffix, contents in stale_sidecars.items():
            Path(f"{db_path}{suffix}").write_bytes(contents)
        return dump

    monkeypatch.setattr(recover_module, "_load_dump", _load_dump_then_leave_source_wal)

    path_type = type(db_path)
    real_replace = path_type.replace

    def replace_and_recreate_source_sidecar(path: Path, target: str | Path) -> Path:
        result = real_replace(path, target)
        if path == db_path and Path(target).name.startswith(f"{db_path.name}.corrupt-"):
            # Model a writer recreating a sidecar after source.replace() while
            # the source path is temporarily empty. The post-rename cleanup
            # must remove it before the recovered output is installed.
            Path(f"{path}-wal").write_bytes(b"recreated around source rename")
        return result

    monkeypatch.setattr(path_type, "replace", replace_and_recreate_source_sidecar)

    recover_module.recover_sqlite_db(recover_module.RecoveryOptions(source=db_path, output=output_path, replace=True))

    with closing(sqlite3.connect(db_path)) as connection:
        assert connection.execute("SELECT name FROM items").fetchall() == [("base",)]

    for path in (db_path, output_path):
        for suffix in ("-wal", "-shm", "-journal", "-mj12345678"):
            assert not Path(f"{path}{suffix}").exists()


@pytest.mark.parametrize("replace", [False, True])
def test_recover_blocks_writes_during_locked_snapshot_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replace: bool,
) -> None:
    """An active source connection cannot write during the locked export."""
    db_path = tmp_path / "store.db"
    output_path = tmp_path / "recovered.db"
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute("CREATE TABLE items (name TEXT NOT NULL)")
        connection.execute("INSERT INTO items (name) VALUES ('base')")

    writer = sqlite3.connect(db_path, timeout=0, isolation_level=None)
    write_attempts: list[str] = []
    writer_closed_before_lock_release = False
    real_load_dump = recover_module._load_dump

    def _load_dump_then_attempt_source_write(connection: sqlite3.Connection) -> str:
        nonlocal writer_closed_before_lock_release
        dump = real_load_dump(connection)
        try:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("INSERT INTO items (name) VALUES ('raced')")
            writer.commit()
            write_attempts.append("wrote")
        except sqlite3.OperationalError as exc:
            writer.rollback()
            write_attempts.append(str(exc).lower())
        finally:
            # The operator contract requires external writer handles to be
            # closed before the recovery lock is released.
            writer.close()
            writer_closed_before_lock_release = True
        return dump

    monkeypatch.setattr(recover_module, "_load_dump", _load_dump_then_attempt_source_write)

    try:
        outcome = recover_module.recover_sqlite_db(
            recover_module.RecoveryOptions(source=db_path, output=output_path, replace=replace)
        )
    finally:
        writer.close()

    assert write_attempts == ["database is locked"]
    assert writer_closed_before_lock_release
    assert outcome.replaced is replace
    assert db_path.exists()
    assert output_path.exists() is not replace
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("INSERT INTO items (name) VALUES ('after')")
        assert connection.execute("SELECT name FROM items ORDER BY rowid").fetchall()[-1] == ("after",)


def test_recover_replace_fails_closed_on_partial_sidecar_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A sidecar removal error must not move either database."""
    db_path = tmp_path / "store.db"
    output_path = tmp_path / "recovered.db"
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute("CREATE TABLE items (name TEXT NOT NULL)")
        connection.execute("INSERT INTO items (name) VALUES ('base')")

    blocked_sidecar = tmp_path / "store.db-mj12345678"
    blocked_sidecar.write_bytes(b"unremovable")
    for suffix in ("-wal", "-shm", "-journal"):
        (tmp_path / f"store.db{suffix}").write_bytes(b"stale")

    real_unlink = type(blocked_sidecar).unlink

    def unlink_with_partial_failure(path: Path, *, missing_ok: bool = False) -> None:
        if path == blocked_sidecar:
            raise PermissionError("simulated sidecar cleanup failure")
        real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(type(blocked_sidecar), "unlink", unlink_with_partial_failure)

    with pytest.raises(RuntimeError, match="failed to remove SQLite sidecars"):
        recover_module.recover_sqlite_db(
            recover_module.RecoveryOptions(source=db_path, output=output_path, replace=True)
        )

    assert db_path.exists()
    assert not list(tmp_path.glob("store.db.corrupt-*"))
    assert output_path.exists()
    assert blocked_sidecar.exists()
    assert not (tmp_path / "store.db-wal").exists()
    assert not (tmp_path / "store.db-shm").exists()
    assert not (tmp_path / "store.db-journal").exists()


def test_recover_replace_restores_source_when_post_move_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repeat cleanup error must roll back the source move."""
    db_path = tmp_path / "store.db"
    output_path = tmp_path / "recovered.db"
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute("CREATE TABLE items (name TEXT NOT NULL)")
        connection.execute("INSERT INTO items (name) VALUES ('base')")

    real_remove_sidecars = recover_module._remove_sqlite_sidecars
    source_cleanup_calls = 0

    def fail_post_move_cleanup(path: Path) -> None:
        nonlocal source_cleanup_calls
        if path == db_path:
            source_cleanup_calls += 1
            if source_cleanup_calls == 2:
                raise RuntimeError("simulated post-move sidecar cleanup failure")
        real_remove_sidecars(path)

    monkeypatch.setattr(recover_module, "_remove_sqlite_sidecars", fail_post_move_cleanup)

    with pytest.raises(RuntimeError, match="simulated post-move sidecar cleanup failure"):
        recover_module.recover_sqlite_db(
            recover_module.RecoveryOptions(source=db_path, output=output_path, replace=True)
        )

    assert source_cleanup_calls == 2
    assert db_path.exists()
    assert output_path.exists()
    assert not list(tmp_path.glob("store.db.corrupt-*"))
    with closing(sqlite3.connect(db_path)) as connection:
        assert connection.execute("SELECT name FROM items").fetchall() == [("base",)]


def test_recover_replace_fails_closed_when_source_is_busy(tmp_path: Path) -> None:
    """A conflicting source writer must prevent replacement before any rename."""
    db_path = tmp_path / "store.db"
    output_path = tmp_path / "recovered.db"
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute("CREATE TABLE items (name TEXT NOT NULL)")
        connection.execute("INSERT INTO items (name) VALUES ('base')")

    writer = sqlite3.connect(db_path, timeout=0, isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(RuntimeError, match="could not acquire exclusive SQLite recovery lock"):
            recover_module.recover_sqlite_db(
                recover_module.RecoveryOptions(source=db_path, output=output_path, replace=True)
            )
    finally:
        writer.rollback()
        writer.close()

    assert db_path.exists()
    assert not output_path.exists()
    assert not list(tmp_path.glob("store.db.corrupt-*"))


def test_recover_replace_restores_source_when_install_rename_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed output rename must restore the original source path."""
    db_path = tmp_path / "store.db"
    output_path = tmp_path / "recovered.db"
    with closing(sqlite3.connect(db_path)) as connection, connection:
        connection.execute("CREATE TABLE items (name TEXT NOT NULL)")
        connection.execute("INSERT INTO items (name) VALUES ('base')")

    path_type = type(db_path)
    real_replace = path_type.replace

    def fail_output_install(path: Path, target: str | Path) -> Path:
        if path == output_path and target == db_path:
            raise PermissionError("simulated output rename failure")
        return real_replace(path, target)

    monkeypatch.setattr(path_type, "replace", fail_output_install)

    with pytest.raises(RuntimeError, match="failed to install recovered SQLite database"):
        recover_module.recover_sqlite_db(
            recover_module.RecoveryOptions(source=db_path, output=output_path, replace=True)
        )

    assert db_path.exists()
    assert not list(tmp_path.glob("store.db.corrupt-*"))
    assert output_path.exists()
    with closing(sqlite3.connect(db_path)) as connection:
        assert connection.execute("SELECT name FROM items").fetchall() == [("base",)]


def test_recover_sidecar_cleanup_treats_wildcard_database_names_literally(tmp_path: Path) -> None:
    """A wildcard in the database name must not broaden master-journal cleanup."""
    db_path = tmp_path / "store*.db"
    target_master_journal = tmp_path / "store*.db-mj12345678"
    unrelated_master_journal = tmp_path / "storeOTHER.db-mj12345678"
    target_master_journal.write_bytes(b"target")
    unrelated_master_journal.write_bytes(b"unrelated")

    recover_module._remove_sqlite_sidecars(db_path)

    assert not target_master_journal.exists()
    assert unrelated_master_journal.read_bytes() == b"unrelated"


@pytest.mark.parametrize(
    ("source_name", "output_name"),
    [
        ("store.db-wal", "store.db"),
        ("store.db", "store.db-wal"),
        ("store.db-mj12345678", "store.db"),
        ("store.db", "store.db-mj12345678"),
    ],
)
@pytest.mark.parametrize("replace", [False, True])
def test_recover_rejects_source_output_sidecar_overlap(
    tmp_path: Path,
    source_name: str,
    output_name: str,
    replace: bool,
) -> None:
    """Overlapping source/output namespaces must fail before cleanup."""
    source = tmp_path / source_name
    output = tmp_path / output_name
    with closing(sqlite3.connect(source)) as connection, connection:
        connection.execute("CREATE TABLE items (name TEXT NOT NULL)")
        connection.execute("INSERT INTO items (name) VALUES ('base')")

    with pytest.raises(ValueError, match="overlaps a SQLite sidecar"):
        recover_module.recover_sqlite_db(recover_module.RecoveryOptions(source=source, output=output, replace=replace))

    assert source.exists()
    assert not output.exists()
    assert not list(tmp_path.glob("store.db.corrupt-*"))


@pytest.mark.parametrize("replace", [False, True])
def test_recover_rejects_symlink_sidecar_before_cleanup(tmp_path: Path, replace: bool) -> None:
    """A sidecar symlink must not be unlinked as part of recovery cleanup."""
    real_source = tmp_path / "real.db"
    source = tmp_path / "store.db-wal"
    output = tmp_path / "store.db"
    with closing(sqlite3.connect(real_source)) as connection, connection:
        connection.execute("CREATE TABLE items (name TEXT NOT NULL)")
        connection.execute("INSERT INTO items (name) VALUES ('base')")
    source.symlink_to(real_source)

    with pytest.raises(ValueError, match="overlaps a SQLite sidecar"):
        recover_module.recover_sqlite_db(recover_module.RecoveryOptions(source=source, output=output, replace=replace))

    assert source.is_symlink()
    assert source.resolve() == real_source
    assert not output.exists()
    assert not list(tmp_path.glob("store.db.corrupt-*"))


def test_runstate_round_trips(tmp_path: Path) -> None:
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")

    assert sqlite_utils_module.read_sqlite_runstate(db_path) is None

    assert sqlite_utils_module.write_sqlite_runstate(db_path, sqlite_utils_module.SqliteRunState.RUNNING) is True
    assert sqlite_utils_module.read_sqlite_runstate(db_path) is sqlite_utils_module.SqliteRunState.RUNNING

    assert sqlite_utils_module.write_sqlite_runstate(db_path, sqlite_utils_module.SqliteRunState.CLEAN) is True
    assert sqlite_utils_module.read_sqlite_runstate(db_path) is sqlite_utils_module.SqliteRunState.CLEAN


def test_runstate_lifetime_lock_is_exclusive_and_reusable(tmp_path: Path) -> None:
    db_path = tmp_path / "store.db"
    first = sqlite_utils_module.acquire_sqlite_runstate_lock(db_path)
    try:
        with pytest.raises(sqlite_utils_module.SqliteRunStateLockError, match="lifetime lock"):
            sqlite_utils_module.acquire_sqlite_runstate_lock(db_path)
    finally:
        sqlite_utils_module.release_sqlite_runstate_lock(first)

    second = sqlite_utils_module.acquire_sqlite_runstate_lock(db_path)
    sqlite_utils_module.release_sqlite_runstate_lock(second)
    assert sqlite_utils_module.sqlite_runstate_lock_path(db_path).exists()


@pytest.mark.skipif(os.name == "nt", reason="subprocess lock semantics are exercised on POSIX")
def test_runstate_lifetime_lock_contends_across_processes(tmp_path: Path) -> None:
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")
    holder = _start_subprocess_lock_holder(db_path)
    try:
        with pytest.raises(sqlite_utils_module.SqliteRunStateLockError, match="lifetime lock"):
            sqlite_utils_module.acquire_sqlite_runstate_lock(db_path)

        assert holder.stdin is not None
        holder.stdin.write("release\n")
        holder.stdin.flush()
        assert holder.wait(timeout=5) == 0

        replacement = sqlite_utils_module.acquire_sqlite_runstate_lock(db_path)
        sqlite_utils_module.release_sqlite_runstate_lock(replacement)
    finally:
        _stop_subprocess(holder)


@pytest.mark.skipif(os.name == "nt", reason="subprocess lock semantics are exercised on POSIX")
def test_runstate_lifetime_lock_releases_after_process_death(tmp_path: Path) -> None:
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")
    holder = _start_subprocess_lock_holder(db_path)
    try:
        holder.kill()
        assert holder.wait(timeout=5) is not None

        replacement = sqlite_utils_module.acquire_sqlite_runstate_lock(db_path)
        sqlite_utils_module.release_sqlite_runstate_lock(replacement)
    finally:
        _stop_subprocess(holder)


def test_runstate_reads_unrecognized_content_as_unknown(tmp_path: Path) -> None:
    db_path = tmp_path / "store.db"
    sqlite_utils_module.sqlite_runstate_path(db_path).write_text("half-written", encoding="utf-8")

    assert sqlite_utils_module.read_sqlite_runstate(db_path) is None


@pytest.mark.parametrize("db_exists", [True, False], ids=["database-present", "database-missing"])
def test_runstate_clean_with_null_identity_is_unknown(tmp_path: Path, db_exists: bool) -> None:
    """A legacy or hand-written null identity must never certify a clean close."""
    db_path = tmp_path / "store.db"
    if db_exists:
        db_path.write_bytes(b"sqlite")
    sqlite_utils_module.sqlite_runstate_path(db_path).write_text(
        '{"state": "clean", "identity": null}', encoding="utf-8"
    )

    assert sqlite_utils_module.read_sqlite_runstate(db_path) is None


@pytest.mark.parametrize(
    "identity",
    [
        pytest.param("not-an-object", id="scalar"),
        pytest.param({"dev": 1}, id="missing-fields"),
        pytest.param(
            {"dev": True, "ino": 1, "size": 1, "mtime_ns": 1, "ctime_ns": 1},
            id="bool-is-not-an-integer",
        ),
    ],
)
def test_runstate_running_with_malformed_identity_remains_untrusted(tmp_path: Path, identity: object) -> None:
    """A malformed identity cannot become a clean-skip input."""
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")
    sqlite_utils_module.sqlite_runstate_path(db_path).write_text(
        json.dumps({"state": "running", "identity": identity}),
        encoding="utf-8",
    )

    record = sqlite_utils_module.read_sqlite_runstate_record(db_path)
    assert record is not None
    assert record.state is sqlite_utils_module.SqliteRunState.RUNNING
    assert record.identity is None


def test_runstate_recursive_json_is_unknown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")
    sqlite_utils_module.sqlite_runstate_path(db_path).write_text("{}", encoding="utf-8")

    def _raise_recursion_error(_raw: str) -> object:
        raise RecursionError("run-state JSON nesting is too deep")

    monkeypatch.setattr(sqlite_utils_module.json, "loads", _raise_recursion_error)

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


def test_runstate_write_syncs_directory_after_replace_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Cleanup of an old clean marker is durable even when replace fails."""
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")
    sqlite_utils_module.write_sqlite_runstate(db_path, sqlite_utils_module.SqliteRunState.CLEAN)
    directory_syncs: list[Path] = []

    def _replace_failure(*_args: object, **_kwargs: object) -> None:
        raise OSError("replace failed")

    def _record_directory_sync(directory: Path) -> bool:
        directory_syncs.append(directory)
        return True

    monkeypatch.setattr(os, "replace", _replace_failure)
    monkeypatch.setattr(sqlite_utils_module, "_fsync_directory", _record_directory_sync)

    assert sqlite_utils_module.write_sqlite_runstate(db_path, sqlite_utils_module.SqliteRunState.RUNNING) is False
    assert directory_syncs == [db_path.parent]
    assert not sqlite_utils_module.sqlite_runstate_path(db_path).exists()
    assert sqlite_utils_module.read_sqlite_runstate(db_path) is None


@pytest.mark.skipif(os.name == "nt", reason="symlink creation is not available on all Windows test runners")
def test_runstate_write_does_not_follow_predictable_temp_symlink(tmp_path: Path) -> None:
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")
    victim = tmp_path / "victim"
    victim.write_text("keep me", encoding="utf-8")
    predictable_tmp = sqlite_utils_module.sqlite_runstate_path(db_path).with_name(
        f"{sqlite_utils_module.sqlite_runstate_path(db_path).name}.{os.getpid()}.tmp"
    )
    predictable_tmp.symlink_to(victim)
    try:
        assert sqlite_utils_module.write_sqlite_runstate(db_path, sqlite_utils_module.SqliteRunState.RUNNING) is True
        assert victim.read_text(encoding="utf-8") == "keep me"
    finally:
        predictable_tmp.unlink(missing_ok=True)


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
    payload_syncs: list[int] = []
    directory_syncs: list[Path] = []
    real_fsync = os.fsync

    def _record_payload(fd: int) -> None:
        payload_syncs.append(fd)
        real_fsync(fd)

    def _record_directory(directory: Path) -> bool:
        directory_syncs.append(directory)
        return True

    monkeypatch.setattr(os, "fsync", _record_payload)
    monkeypatch.setattr(sqlite_utils_module, "_fsync_directory", _record_directory)

    assert sqlite_utils_module.write_sqlite_runstate(db_path, sqlite_utils_module.SqliteRunState.CLEAN) is True

    assert len(payload_syncs) == 1
    assert directory_syncs == [sqlite_utils_module.sqlite_runstate_path(db_path).parent]


def test_runstate_write_fails_closed_when_the_directory_sync_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Storage that cannot confirm durability must not leave a trusted record."""
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")
    sqlite_utils_module.write_sqlite_runstate(db_path, sqlite_utils_module.SqliteRunState.CLEAN)

    monkeypatch.setattr(sqlite_utils_module, "_fsync_directory", lambda _directory: False)

    with pytest.raises(sqlite_utils_module.SqliteRunStateDurabilityError, match="persist removal"):
        sqlite_utils_module.write_sqlite_runstate(db_path, sqlite_utils_module.SqliteRunState.RUNNING)

    monkeypatch.undo()
    assert sqlite_utils_module.read_sqlite_runstate(db_path) is None
    assert not sqlite_utils_module.sqlite_runstate_path(db_path).exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_runstate_write_syncs_directory_again_after_cleanup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A failed post-replace sync must durably fence the cleanup unlink."""
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")
    sqlite_utils_module.write_sqlite_runstate(db_path, sqlite_utils_module.SqliteRunState.CLEAN)
    directory_syncs: list[Path] = []
    outcomes = iter([False, True])

    def _sync(directory: Path) -> bool:
        directory_syncs.append(directory)
        return next(outcomes)

    monkeypatch.setattr(sqlite_utils_module, "_fsync_directory", _sync)

    assert sqlite_utils_module.write_sqlite_runstate(db_path, sqlite_utils_module.SqliteRunState.RUNNING) is False
    assert directory_syncs == [db_path.parent, db_path.parent]
    assert sqlite_utils_module.read_sqlite_runstate(db_path) is None
    assert not list(tmp_path.glob("*.tmp"))


def test_runstate_write_aborts_when_failed_marker_cannot_be_removed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unremovable stale marker must stop startup rather than stay trusted."""
    db_path = tmp_path / "store.db"
    db_path.write_bytes(b"sqlite")
    sqlite_utils_module.write_sqlite_runstate(db_path, sqlite_utils_module.SqliteRunState.CLEAN)
    target = sqlite_utils_module.sqlite_runstate_path(db_path)
    real_unlink = Path.unlink

    def _fail_target_unlink(path: Path, *, missing_ok: bool = False) -> None:
        if path == target:
            raise OSError("sidecar is locked")
        real_unlink(path, missing_ok=missing_ok)

    def _fail_replace(*_args: object, **_kwargs: object) -> None:
        raise OSError("read-only filesystem")

    monkeypatch.setattr(os, "replace", _fail_replace)
    monkeypatch.setattr(Path, "unlink", _fail_target_unlink)

    with pytest.raises(sqlite_utils_module.SqliteRunStateDurabilityError, match="remove failed SQLite run-state files"):
        sqlite_utils_module.write_sqlite_runstate(db_path, sqlite_utils_module.SqliteRunState.RUNNING)

    assert sqlite_utils_module.read_sqlite_runstate(db_path) is sqlite_utils_module.SqliteRunState.CLEAN


def test_fsync_directory_reports_success_where_directory_handles_do_not_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Windows has no directory handle to sync, and that is not a failure."""
    attempts: list[object] = []

    def _record(*args: object, **_kwargs: object) -> int:
        attempts.append(args)
        raise AssertionError("the open must not be attempted on such a platform")

    monkeypatch.setattr(sqlite_utils_module, "_DIRECTORY_FSYNC_SUPPORTED", False)
    monkeypatch.setattr(os, "open", _record)

    assert sqlite_utils_module._fsync_directory(tmp_path) is True
    assert attempts == []


@pytest.mark.parametrize(
    "error",
    [
        PermissionError(errno.EACCES, "permission denied"),
        FileNotFoundError(errno.ENOENT, "no such directory"),
        OSError(errno.EMFILE, "too many open files"),
        OSError(errno.EIO, "input/output error"),
    ],
    ids=["eacces", "enoent", "emfile", "eio"],
)
def test_fsync_directory_reports_failure_when_the_directory_cannot_be_opened(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: OSError
) -> None:
    """A real open failure means the rename is unproven and must fail closed."""

    def _fail(*_args: object, **_kwargs: object) -> int:
        raise error

    monkeypatch.setattr(sqlite_utils_module, "_DIRECTORY_FSYNC_SUPPORTED", True)
    monkeypatch.setattr(os, "open", _fail)

    assert sqlite_utils_module._fsync_directory(tmp_path) is False


def test_fsync_directory_reports_failure_when_the_sync_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The sync path is exercised on every platform, not only where it runs."""
    placeholder = tmp_path / "placeholder"
    placeholder.write_bytes(b"")
    real_open = os.open

    def _open_placeholder(*_args: object, **_kwargs: object) -> int:
        return real_open(placeholder, os.O_RDONLY)

    def _fail(_fd: int) -> None:
        raise OSError(errno.EIO, "input/output error")

    monkeypatch.setattr(sqlite_utils_module, "_DIRECTORY_FSYNC_SUPPORTED", True)
    monkeypatch.setattr(os, "open", _open_placeholder)
    monkeypatch.setattr(os, "fsync", _fail)

    assert sqlite_utils_module._fsync_directory(tmp_path) is False

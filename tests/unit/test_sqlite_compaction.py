from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.db.compact as compact
import app.db.migrate as migrate
import app.db.recover as recover
from app.db.sqlite_utils import IntegrityCheck, SqliteIntegrityCheckMode, check_sqlite_integrity

pytestmark = pytest.mark.unit


def _database_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path}"


def _create_fragmented_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO alembic_version VALUES ('test_revision')")
        connection.execute("CREATE TABLE payloads (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO payloads(payload) VALUES (?)",
            ((f"{index}:" + "x" * 1024,) for index in range(2_000)),
        )
        connection.execute("DELETE FROM payloads WHERE id <= 1900")


def _remaining_rows(path: Path) -> int:
    with sqlite3.connect(path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM payloads").fetchone()[0])


def test_compaction_dry_run_reports_without_mutation(tmp_path: Path) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    before_stat = source.stat()
    before_names = {path.name for path in tmp_path.iterdir()}

    plan = compact.plan_sqlite_compaction(_database_url(source))

    assert plan.source == source
    assert plan.source_bytes == before_stat.st_size
    assert plan.page_size > 0
    assert plan.page_count > 0
    assert plan.freelist_pages > 0
    assert plan.reclaimable_bytes == plan.page_size * plan.freelist_pages
    expected_output_bytes = compact._incremental_auto_vacuum_output_bytes_for_pages(
        page_size=plan.page_size,
        page_count=plan.page_count,
    )
    assert plan.required_free_bytes == plan.source_bytes + 2 * expected_output_bytes + compact._MIN_FREE_SPACE_RESERVE
    assert source.stat() == before_stat
    assert {path.name for path in tmp_path.iterdir()} == before_names


def test_compaction_reclaims_space_and_preserves_backup(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    source.chmod(0o600)
    source_stat = source.stat()
    before_bytes = source.stat().st_size
    real_integrity_check = compact.check_sqlite_integrity

    def assert_compacted_permissions(
        path: Path,
        *,
        mode: SqliteIntegrityCheckMode,
        require_existing: bool = False,
    ):
        if ".compact-" in str(path):
            assert path.stat().st_mode & 0o777 == 0o600
            assert (path.stat().st_uid, path.stat().st_gid) == (source_stat.st_uid, source_stat.st_gid)
            assert path.parent.stat().st_mode & 0o777 == 0o700
        return real_integrity_check(path, mode=mode, require_existing=require_existing)

    monkeypatch.setattr(compact, "check_sqlite_integrity", assert_compacted_permissions)

    outcome = compact.execute_sqlite_compaction(
        _database_url(source),
        confirm_stopped=True,
    )

    assert outcome.source == source
    assert outcome.backup.exists()
    assert outcome.source_bytes_before == before_bytes
    assert outcome.source_bytes_after == source.stat().st_size
    assert outcome.source_bytes_after < before_bytes
    assert outcome.reclaimed_bytes == before_bytes - source.stat().st_size
    assert _remaining_rows(source) == 100
    assert _remaining_rows(outcome.backup) == 100
    with sqlite3.connect(source) as connection:
        assert connection.execute("PRAGMA auto_vacuum").fetchone()[0] == 2
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "test_revision"
    assert check_sqlite_integrity(source, mode=SqliteIntegrityCheckMode.QUICK).ok
    assert not Path(f"{source}.compact.lock").exists()


def test_compaction_restores_read_only_source_mode_after_output_mutation(tmp_path: Path) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    source.chmod(0o444)

    outcome = compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)

    assert source.stat().st_mode & 0o777 == 0o444
    assert outcome.backup.stat().st_mode & 0o777 == 0o444
    assert _remaining_rows(source) == 100


def test_compaction_requires_stopped_confirmation(tmp_path: Path) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)

    with pytest.raises(RuntimeError, match="--confirm-stopped"):
        compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=False)

    assert _remaining_rows(source) == 100
    assert list(tmp_path.glob("*.pre-compact-*")) == []


@pytest.mark.parametrize(
    "database_url,error",
    [
        ("postgresql+asyncpg://localhost/codex", "file-backed SQLite"),
        ("sqlite+aiosqlite:///:memory:", "file-backed SQLite"),
        ("sqlite+aiosqlite:///file:shared?mode=memory&cache=shared&uri=true", "file-backed SQLite"),
        ("sqlite+aiosqlite:///file::memory:?cache=shared&uri=true", "file-backed SQLite"),
    ],
)
def test_compaction_rejects_non_file_backends(database_url: str, error: str) -> None:
    with pytest.raises(RuntimeError, match=error):
        compact.plan_sqlite_compaction(database_url)


def test_compaction_rejects_missing_database(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="not found"):
        compact.plan_sqlite_compaction(_database_url(tmp_path / "missing.db"))


def test_compaction_accepts_file_backed_sqlite_uri(tmp_path: Path) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)

    plan = compact.plan_sqlite_compaction(f"sqlite+aiosqlite:///file:{source}?uri=true")

    assert plan.source == source


def test_compaction_rejects_symbolic_link_database_path(tmp_path: Path) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    link = tmp_path / "linked.db"
    try:
        link.symlink_to(source)
    except OSError:
        pytest.skip("symlink creation unavailable")

    with pytest.raises(RuntimeError, match="not a symbolic link"):
        compact.plan_sqlite_compaction(_database_url(link))


def test_compaction_backup_name_skips_dangling_symlink(tmp_path: Path) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    first = source.with_name("store.pre-compact-20260827T000000Z.db")
    try:
        first.symlink_to(tmp_path / "missing-target")
    except OSError:
        pytest.skip("symlink creation unavailable")

    candidate = compact._next_sibling(source, label="pre-compact", timestamp="20260827T000000Z")

    assert candidate.name == "store.pre-compact-20260827T000000Z-1.db"


def test_compaction_backup_name_skips_existing_sidecars(tmp_path: Path) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    sidecar = tmp_path / "store.pre-compact-20260827T000000Z.db-wal"
    sidecar.write_bytes(b"retained-wal")

    candidate = compact._next_sibling(source, label="pre-compact", timestamp="20260827T000000Z")

    assert candidate.name == "store.pre-compact-20260827T000000Z-1.db"


def test_compaction_dry_run_rejects_nonempty_wal_without_touching_it(tmp_path: Path) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    wal = Path(f"{source}-wal")
    wal.write_bytes(b"committed-wal-placeholder")
    before = wal.read_bytes()

    with pytest.raises(RuntimeError, match="checkpointed SQLite"):
        compact.plan_sqlite_compaction(_database_url(source))

    assert wal.read_bytes() == before


def test_compaction_dry_run_accepts_zeroed_persistent_journal(tmp_path: Path) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    journal = Path(f"{source}-journal")
    journal.write_bytes(bytes(1024))
    before = journal.read_bytes()

    compact.plan_sqlite_compaction(_database_url(source))

    assert journal.read_bytes() == before


def test_compaction_dry_run_rejects_potentially_hot_journal(tmp_path: Path) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    journal = Path(f"{source}-journal")
    journal.write_bytes(b"hot-data" + bytes(1016))

    with pytest.raises(RuntimeError, match="potentially hot"):
        compact.plan_sqlite_compaction(_database_url(source))


def test_compaction_dry_run_rejects_state_change_during_read(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    signature = compact._database_state_signature(source)
    signatures = iter((signature, (*signature[:-1], compact._PathSignature(exists=True, size=1))))
    monkeypatch.setattr(compact, "_database_state_signature", lambda _source: next(signatures))

    with pytest.raises(RuntimeError, match="changed during dry-run"):
        compact.plan_sqlite_compaction(_database_url(source))


def test_compaction_dry_run_reports_size_from_verified_snapshot(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    signature = (
        compact._PathSignature(exists=True, size=123),
        compact._PathSignature(exists=False),
        compact._PathSignature(exists=False),
    )
    monkeypatch.setattr(compact, "_database_state_signature", lambda _source: signature)

    plan = compact._read_plan(source)

    assert plan.source_bytes == 123


def test_compaction_fails_when_uid_gid_cannot_be_preserved(tmp_path: Path, monkeypatch) -> None:
    compacted = tmp_path / "compacted.db"
    compacted.write_bytes(b"sqlite")
    current = compacted.stat()
    source_stat = SimpleNamespace(
        st_mode=current.st_mode,
        st_uid=current.st_uid + 1,
        st_gid=current.st_gid,
    )
    monkeypatch.setattr(
        compact.os,
        "chown",
        lambda *_args: (_ for _ in ()).throw(PermissionError("not permitted")),
    )

    with pytest.raises(RuntimeError, match="cannot preserve SQLite database uid/gid"):
        compact._preserve_file_metadata(compacted, source_stat)


def test_compaction_rejects_insufficient_space_without_artifacts(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    disk_usage_type = type(compact.shutil.disk_usage(tmp_path))
    monkeypatch.setattr(compact.shutil, "disk_usage", lambda _path: disk_usage_type(100, 100, 0))

    with pytest.raises(RuntimeError, match="insufficient free space"):
        compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)

    assert _remaining_rows(source) == 100
    assert {path.name for path in tmp_path.iterdir()} == {"store.db"}


def test_compaction_free_space_budget_includes_pending_wal(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    wal = Path(f"{source}-wal")
    wal.write_bytes(b"pending-wal-data" * 1024)
    source_bytes = source.stat().st_size
    required_free_bytes = 2 * (source_bytes + wal.stat().st_size) + compact._MIN_FREE_SPACE_RESERVE
    disk_usage_type = type(compact.shutil.disk_usage(tmp_path))
    monkeypatch.setattr(
        compact.shutil,
        "disk_usage",
        lambda _path: disk_usage_type(required_free_bytes, required_free_bytes, required_free_bytes - 1),
    )
    monkeypatch.setattr(
        compact,
        "_checkpoint_wal",
        lambda _connection: (_ for _ in ()).throw(AssertionError("checkpoint should not start")),
    )

    with pytest.raises(RuntimeError, match="insufficient free space"):
        compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)

    assert source.exists()
    assert wal.exists()
    assert not Path(f"{source}.compact.lock").exists()


def test_compaction_rechecks_space_before_auto_vacuum(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    disk_usage_type = type(compact.shutil.disk_usage(tmp_path))
    ample_free_bytes = 4 * source.stat().st_size + compact._MIN_FREE_SPACE_RESERVE
    free_bytes = iter((ample_free_bytes, ample_free_bytes, 0))
    monkeypatch.setattr(
        compact.shutil,
        "disk_usage",
        lambda _path: disk_usage_type(ample_free_bytes, ample_free_bytes, next(free_bytes)),
    )

    with pytest.raises(RuntimeError, match="insufficient free space"):
        compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)

    assert _remaining_rows(source) == 100
    assert list(tmp_path.glob("*.pre-compact-*")) == []


def test_compaction_rechecks_sqlite_temporary_file_space(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    sqlite_temp_directory = tmp_path / "sqlite-temp"
    sqlite_temp_directory.mkdir()
    _create_fragmented_database(source)
    disk_usage_type = type(compact.shutil.disk_usage(tmp_path))
    ample_free_bytes = 4 * source.stat().st_size + compact._MIN_FREE_SPACE_RESERVE
    monkeypatch.setenv("SQLITE_TMPDIR", str(sqlite_temp_directory))
    monkeypatch.setattr(compact, "_same_filesystem", lambda _first, _second: False)
    monkeypatch.setattr(
        compact.shutil,
        "disk_usage",
        lambda path: disk_usage_type(
            ample_free_bytes,
            ample_free_bytes,
            0 if Path(path) == sqlite_temp_directory else ample_free_bytes,
        ),
    )

    with pytest.raises(RuntimeError, match="SQLite temporary files"):
        compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)

    assert _remaining_rows(source) == 100
    assert list(tmp_path.glob("*.pre-compact-*")) == []


def test_compaction_combines_auto_vacuum_space_on_shared_filesystem(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    disk_usage_type = type(compact.shutil.disk_usage(tmp_path))
    output_bytes = 1024
    initial_free_bytes = 2 * source.stat().st_size + compact._MIN_FREE_SPACE_RESERVE
    free_bytes = iter((initial_free_bytes, initial_free_bytes, output_bytes + compact._MIN_FREE_SPACE_RESERVE))
    monkeypatch.setattr(compact, "_incremental_auto_vacuum_output_bytes", lambda _connection: output_bytes)
    monkeypatch.setattr(compact, "_same_filesystem", lambda _first, _second: True)
    monkeypatch.setattr(
        compact.shutil,
        "disk_usage",
        lambda _path: disk_usage_type(initial_free_bytes, initial_free_bytes, next(free_bytes)),
    )

    with pytest.raises(RuntimeError, match="insufficient free space"):
        compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)

    assert _remaining_rows(source) == 100
    assert list(tmp_path.glob("*.pre-compact-*")) == []


def test_compaction_rejects_busy_checkpoint_and_existing_lock(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    monkeypatch.setattr(
        compact,
        "_checkpoint_wal",
        lambda _connection: (_ for _ in ()).throw(RuntimeError("checkpoint is busy")),
    )

    with pytest.raises(RuntimeError, match="checkpoint is busy"):
        compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)
    assert not Path(f"{source}.compact.lock").exists()

    lock_path = Path(f"{source}.compact.lock")
    lock_path.write_text("held", encoding="utf-8")
    with pytest.raises(RuntimeError, match="lock already exists"):
        compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)
    assert _remaining_rows(source) == 100


def test_compaction_lock_cleanup_does_not_unlink_replacement(tmp_path: Path) -> None:
    lock_path = tmp_path / "store.db.compact.lock"
    descriptor = compact.os.open(lock_path, compact.os.O_CREAT | compact.os.O_EXCL | compact.os.O_WRONLY, 0o600)
    try:
        lock_path.unlink()
        lock_path.write_text("replacement", encoding="utf-8")

        compact._unlink_owned_lock(lock_path, descriptor)

        assert lock_path.read_text(encoding="utf-8") == "replacement"
    finally:
        compact.os.close(descriptor)


def test_compaction_cleans_lock_when_source_stat_fails_after_lock(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    lock_path = Path(f"{source}.compact.lock")
    real_stat = Path.stat

    def fail_source_stat_after_lock(path: Path, *args, **kwargs):
        if path == source and lock_path.exists():
            raise FileNotFoundError("injected source removal")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fail_source_stat_after_lock)

    with pytest.raises(FileNotFoundError, match="injected source removal"):
        compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)

    assert not lock_path.exists()
    assert list(tmp_path.glob(".store.db.compact-*")) == []


def test_compaction_sidecar_backup_includes_rollback_journal(tmp_path: Path) -> None:
    source = tmp_path / "store.db"
    backup = tmp_path / "store.pre-compact.db"
    journal = Path(f"{source}-journal")
    journal.write_bytes(b"persistent-journal")

    moved = compact._backup_sidecars(source, backup)

    assert moved == [(journal, Path(f"{backup}-journal"))]
    assert not journal.exists()
    assert Path(f"{backup}-journal").read_bytes() == b"persistent-journal"


def test_compaction_sidecar_backup_moves_dangling_sidecar_link(tmp_path: Path) -> None:
    source = tmp_path / "store.db"
    backup = tmp_path / "store.pre-compact.db"
    journal = Path(f"{source}-journal")
    backup_journal = Path(f"{backup}-journal")
    try:
        journal.symlink_to(tmp_path / "missing-journal")
    except OSError:
        pytest.skip("symlink creation unavailable")

    moved = compact._backup_sidecars(source, backup)

    assert moved == [(journal, backup_journal)]
    assert not os.path.lexists(journal)
    assert backup_journal.is_symlink()
    assert os.readlink(backup_journal) == str(tmp_path / "missing-journal")


def test_compaction_sidecar_backup_restores_after_interrupted_move(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    backup = tmp_path / "store.pre-compact.db"
    journal = Path(f"{source}-journal")
    backup_journal = Path(f"{backup}-journal")
    journal.write_bytes(b"persistent-journal")
    real_replace = compact._replace_path

    def move_then_interrupt(candidate: Path, target: Path) -> None:
        real_replace(candidate, target)
        if candidate == journal and target == backup_journal:
            raise KeyboardInterrupt

    monkeypatch.setattr(compact, "_replace_path", move_then_interrupt)

    with pytest.raises(KeyboardInterrupt):
        compact._backup_sidecars(source, backup)

    assert journal.read_bytes() == b"persistent-journal"
    assert not backup_journal.exists()


def test_compaction_sidecar_backup_refuses_late_target_collision(tmp_path: Path) -> None:
    source = tmp_path / "store.db"
    backup = tmp_path / "store.pre-compact.db"
    journal = Path(f"{source}-journal")
    backup_journal = Path(f"{backup}-journal")
    journal.write_bytes(b"source-journal")
    backup_journal.write_bytes(b"existing-backup-journal")

    with pytest.raises(FileExistsError):
        compact._backup_sidecars(source, backup)

    assert journal.read_bytes() == b"source-journal"
    assert backup_journal.read_bytes() == b"existing-backup-journal"


def test_compaction_rejects_source_path_replacement_before_install(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    original = tmp_path / "original.db"
    replacement = tmp_path / "replacement.db"
    _create_fragmented_database(source)
    _create_fragmented_database(replacement)
    replacement_bytes = replacement.read_bytes()

    def replace_source_during_sidecar_handling(_source: Path, _backup: Path) -> list[tuple[Path, Path]]:
        source.replace(original)
        replacement.replace(source)
        return []

    monkeypatch.setattr(compact, "_backup_sidecars", replace_source_during_sidecar_handling)

    with pytest.raises(RuntimeError, match="source path changed before compacted replacement"):
        compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)

    assert source.read_bytes() == replacement_bytes
    assert original.exists()
    assert list(tmp_path.glob("*.pre-compact-*")) == []
    assert not Path(f"{source}.compact.lock").exists()


def test_recovery_replace_respects_active_compaction_lock(tmp_path: Path) -> None:
    source = tmp_path / "store.db"
    output = tmp_path / "recovered.db"
    _create_fragmented_database(source)
    lock_path = Path(f"{source}.compact.lock")
    lock_path.write_text("held", encoding="utf-8")

    with pytest.raises(RuntimeError, match="compaction lock already exists"):
        recover.recover_sqlite_db(recover.RecoveryOptions(source=source, output=output, replace=True))

    assert source.exists()
    assert _remaining_rows(source) == 100


def test_compaction_rejects_external_write_and_corrupt_output(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    versions = iter((1, 2))
    monkeypatch.setattr(compact, "_data_version", lambda _connection: next(versions))

    with pytest.raises(RuntimeError, match="changed during compaction"):
        compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)
    assert _remaining_rows(source) == 100
    assert list(tmp_path.glob("*.pre-compact-*")) == []

    monkeypatch.setattr(compact, "_data_version", lambda _connection: 1)
    real_integrity_check = compact.check_sqlite_integrity

    def reject_compacted(path: Path, *, mode: SqliteIntegrityCheckMode, require_existing: bool = False):
        if ".compact-" in str(path):
            return IntegrityCheck(ok=False, details="injected corruption")
        return real_integrity_check(path, mode=mode, require_existing=require_existing)

    monkeypatch.setattr(compact, "check_sqlite_integrity", reject_compacted)
    with pytest.raises(RuntimeError, match="compacted SQLite quick_check failed"):
        compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)
    assert _remaining_rows(source) == 100
    assert list(tmp_path.glob("*.pre-compact-*")) == []


def test_compaction_does_not_create_database_after_source_disappears(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    real_integrity_check = compact.check_sqlite_integrity

    def remove_source_after_quick_check(path: Path, *, mode: SqliteIntegrityCheckMode, require_existing: bool = False):
        result = real_integrity_check(path, mode=mode, require_existing=require_existing)
        if path == source:
            source.unlink()
        return result

    monkeypatch.setattr(compact, "check_sqlite_integrity", remove_source_after_quick_check)

    with pytest.raises(RuntimeError, match="source path changed before compaction"):
        compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)

    assert not source.exists()
    assert list(tmp_path.glob("*.pre-compact-*")) == []
    assert not Path(f"{source}.compact.lock").exists()


def test_compaction_does_not_recreate_source_disappearing_during_integrity_check(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    real_exists = Path.exists
    removed_source = False

    def remove_source_after_existence_check(path: Path) -> bool:
        nonlocal removed_source
        exists = real_exists(path)
        if path == source and exists and not removed_source:
            removed_source = True
            source.unlink()
        return exists

    monkeypatch.setattr(Path, "exists", remove_source_after_existence_check)

    with pytest.raises(RuntimeError, match="source SQLite quick_check failed"):
        compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)

    assert removed_source
    assert not source.exists()
    assert list(tmp_path.glob("*.pre-compact-*")) == []
    assert not Path(f"{source}.compact.lock").exists()


def test_compaction_does_not_create_database_when_source_disappears_before_open(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    real_matches_identity = compact._path_matches_identity
    checked_source = False

    def remove_source_after_identity_check(path: Path, identity: tuple[int, int]) -> bool:
        nonlocal checked_source
        matches = real_matches_identity(path, identity)
        if path == source and not checked_source:
            checked_source = True
            source.unlink()
        return matches

    monkeypatch.setattr(compact, "_path_matches_identity", remove_source_after_identity_check)

    with pytest.raises(sqlite3.OperationalError):
        compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)

    assert not source.exists()
    assert list(tmp_path.glob("*.pre-compact-*")) == []
    assert not Path(f"{source}.compact.lock").exists()


def test_compaction_rejects_source_swap_during_existing_open(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    original = tmp_path / "original.db"
    replacement = tmp_path / "replacement.db"
    displaced_replacement = tmp_path / "displaced-replacement.db"
    _create_fragmented_database(source)
    _create_fragmented_database(replacement)
    real_existing_sqlite_connection = compact._existing_sqlite_connection

    @contextmanager
    def swap_source_while_opening(path: Path):
        source.replace(original)
        replacement.replace(source)
        with real_existing_sqlite_connection(path) as connection:
            source.replace(displaced_replacement)
            original.replace(source)
            yield connection

    monkeypatch.setattr(compact, "_existing_sqlite_connection", swap_source_while_opening)

    with pytest.raises(RuntimeError, match="source path changed before compaction"):
        compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)

    assert source.exists()
    assert _remaining_rows(source) == 100
    assert not Path(f"{source}.compact.lock").exists()


def test_compaction_rejects_live_path_replacement_after_install(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    external = tmp_path / "external.db"
    displaced = tmp_path / "displaced.db"
    _create_fragmented_database(source)
    _create_fragmented_database(external)
    external_bytes = external.read_bytes()
    real_fsync_directory = compact._fsync_directory
    directory_syncs = 0

    def replace_live_path_after_install(path: Path) -> None:
        nonlocal directory_syncs
        real_fsync_directory(path)
        if path == source.parent:
            directory_syncs += 1
            if directory_syncs == 2:
                source.replace(displaced)
                external.replace(source)

    monkeypatch.setattr(compact, "_fsync_directory", replace_live_path_after_install)

    with pytest.raises(RuntimeError, match="source path changed after compacted replacement"):
        compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)

    backups = list(tmp_path.glob("*.pre-compact-*"))
    assert source.read_bytes() == external_bytes
    assert len(backups) == 1
    assert _remaining_rows(backups[0]) == 100
    assert not Path(f"{source}.compact.lock").exists()


def test_compaction_rejects_live_symlink_to_replacement_after_install(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    displaced = tmp_path / "displaced.db"
    _create_fragmented_database(source)
    real_fsync_directory = compact._fsync_directory
    directory_syncs = 0

    def replace_live_path_with_symlink_after_install(path: Path) -> None:
        nonlocal directory_syncs
        real_fsync_directory(path)
        if path == source.parent:
            directory_syncs += 1
            if directory_syncs == 2:
                source.replace(displaced)
                source.symlink_to(displaced)

    monkeypatch.setattr(compact, "_fsync_directory", replace_live_path_with_symlink_after_install)

    with pytest.raises(RuntimeError, match="source path changed after compacted replacement"):
        compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)

    assert source.is_symlink()
    assert len(list(tmp_path.glob("*.pre-compact-*"))) == 1
    assert not Path(f"{source}.compact.lock").exists()


def test_compaction_restores_backup_when_live_path_disappears_after_install(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    real_fsync_directory = compact._fsync_directory
    directory_syncs = 0

    def remove_live_path_after_install(path: Path) -> None:
        nonlocal directory_syncs
        real_fsync_directory(path)
        if path == source.parent:
            directory_syncs += 1
            if directory_syncs == 2:
                source.unlink()

    monkeypatch.setattr(compact, "_fsync_directory", remove_live_path_after_install)

    with pytest.raises(RuntimeError, match="source path changed after compacted replacement"):
        compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)

    assert source.exists()
    assert _remaining_rows(source) == 100
    assert list(tmp_path.glob("*.pre-compact-*")) == []
    assert not Path(f"{source}.compact.lock").exists()


def test_compaction_rejects_source_path_replacement_during_compaction(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    replacement = tmp_path / "replacement.db"
    _create_fragmented_database(source)
    _create_fragmented_database(replacement)
    real_integrity_check = compact.check_sqlite_integrity

    def replace_source_before_final_validation(
        path: Path,
        *,
        mode: SqliteIntegrityCheckMode,
        require_existing: bool = False,
    ):
        result = real_integrity_check(path, mode=mode, require_existing=require_existing)
        if ".compact-" in str(path):
            replacement.replace(source)
        return result

    monkeypatch.setattr(compact, "check_sqlite_integrity", replace_source_before_final_validation)

    with pytest.raises(RuntimeError, match="source path changed during compaction"):
        compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)

    assert source.exists()
    assert list(tmp_path.glob("*.pre-compact-*")) == []


def test_compaction_blocks_concurrent_writer_before_replacement(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    real_replace = compact._replace_path

    def assert_writer_is_blocked(candidate: Path, target: Path) -> None:
        if ".compact-" in str(candidate) and target == source:
            writer = sqlite3.connect(source, timeout=0)
            try:
                writer.execute("PRAGMA busy_timeout=0")
                with pytest.raises(sqlite3.OperationalError, match="database is locked"):
                    writer.execute("INSERT INTO payloads(payload) VALUES ('concurrent-write')")
            finally:
                writer.close()
        real_replace(candidate, target)

    monkeypatch.setattr(compact, "_replace_path", assert_writer_is_blocked)

    compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)

    assert _remaining_rows(source) == 100


def test_compaction_blocks_concurrent_writer_after_replacement(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    real_fsync_file = compact._fsync_file
    source_fsync_count = 0

    def assert_replacement_writer_is_blocked(path: Path) -> None:
        nonlocal source_fsync_count
        if path == source:
            source_fsync_count += 1
            if source_fsync_count == 2:
                writer = sqlite3.connect(source, timeout=0)
                try:
                    writer.execute("PRAGMA busy_timeout=0")
                    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
                        writer.execute("INSERT INTO payloads(payload) VALUES ('concurrent-write')")
                finally:
                    writer.close()
        real_fsync_file(path)

    monkeypatch.setattr(compact, "_fsync_file", assert_replacement_writer_is_blocked)

    compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)

    assert _remaining_rows(source) == 100


def test_compaction_blocks_concurrent_writer_during_replacement_install(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    real_replace = compact._replace_path

    def assert_writer_is_blocked_after_rename(candidate: Path, target: Path) -> None:
        real_replace(candidate, target)
        if ".compact-" in str(candidate) and target == source:
            writer = sqlite3.connect(source, timeout=0)
            try:
                writer.execute("PRAGMA busy_timeout=0")
                with pytest.raises(sqlite3.OperationalError, match="database is locked"):
                    writer.execute("INSERT INTO payloads(payload) VALUES ('concurrent-write')")
            finally:
                writer.close()

    monkeypatch.setattr(compact, "_replace_path", assert_writer_is_blocked_after_rename)

    compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)

    assert _remaining_rows(source) == 100


def test_compaction_keeps_replacement_lock_while_fsyncing(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    real_replace = compact._replace_path
    real_fsync_descriptor = compact._fsync_descriptor
    replacement_installed = False

    def mark_replacement_install(candidate: Path, target: Path) -> None:
        nonlocal replacement_installed
        real_replace(candidate, target)
        if ".compact-" in str(candidate) and target == source:
            replacement_installed = True

    def assert_writer_is_blocked_during_replacement_fsync(descriptor: int) -> None:
        if replacement_installed:
            writer = sqlite3.connect(source, timeout=0)
            try:
                writer.execute("PRAGMA busy_timeout=0")
                with pytest.raises(sqlite3.OperationalError, match="database is locked"):
                    writer.execute("INSERT INTO payloads(payload) VALUES ('concurrent-write')")
            finally:
                writer.close()
        real_fsync_descriptor(descriptor)

    monkeypatch.setattr(compact, "_replace_path", mark_replacement_install)
    monkeypatch.setattr(compact, "_fsync_descriptor", assert_writer_is_blocked_during_replacement_fsync)

    compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)

    assert _remaining_rows(source) == 100


def test_compaction_restores_source_when_install_rename_fails(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    real_replace = compact._replace_path

    def fail_compacted_install(candidate: Path, target: Path) -> None:
        if ".compact-" in str(candidate) and target == source:
            assert source.exists()
            backups = list(tmp_path.glob("*.pre-compact-*"))
            assert len(backups) == 1
            assert source.samefile(backups[0])
            raise OSError("injected install failure")
        real_replace(candidate, target)

    monkeypatch.setattr(compact, "_replace_path", fail_compacted_install)

    with pytest.raises(OSError, match="injected install failure"):
        compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)

    assert source.exists()
    assert _remaining_rows(source) == 100
    assert list(tmp_path.glob("*.pre-compact-*")) == []
    assert not Path(f"{source}.compact.lock").exists()


def test_compaction_restores_sidecars_when_installation_is_interrupted(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    journal = Path(f"{source}-journal")
    real_replace = compact._replace_path

    def move_sidecar_before_install(_source: Path, backup: Path) -> list[tuple[Path, Path]]:
        journal.write_bytes(b"persistent-journal")
        backup_journal = Path(f"{backup}-journal")
        real_replace(journal, backup_journal)
        return [(journal, backup_journal)]

    def interrupt_compacted_install(candidate: Path, target: Path) -> None:
        if ".compact-" in str(candidate) and target == source:
            raise KeyboardInterrupt
        real_replace(candidate, target)

    monkeypatch.setattr(compact, "_replace_path", interrupt_compacted_install)
    monkeypatch.setattr(compact, "_backup_sidecars", move_sidecar_before_install)

    with pytest.raises(KeyboardInterrupt):
        compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)

    assert source.exists()
    assert journal.read_bytes() == b"persistent-journal"
    assert list(tmp_path.glob("*.pre-compact-*")) == []
    assert not Path(f"{source}.compact.lock").exists()


def test_compaction_restores_source_when_interrupted_after_installation(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    before = source.read_bytes()
    real_replace = compact._replace_path

    def interrupt_after_compacted_install(candidate: Path, target: Path) -> None:
        real_replace(candidate, target)
        if ".compact-" in str(candidate) and target == source:
            raise KeyboardInterrupt

    monkeypatch.setattr(compact, "_replace_path", interrupt_after_compacted_install)

    with pytest.raises(KeyboardInterrupt):
        compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)

    assert source.read_bytes() == before
    assert list(tmp_path.glob("*.pre-compact-*")) == []
    assert not Path(f"{source}.compact.lock").exists()


def test_compaction_restores_original_when_post_install_fsync_fails(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    before = source.read_bytes()
    real_replace = compact._replace_path
    replacement_installed = False
    failure_injected = False

    def mark_replacement_install(candidate: Path, target: Path) -> None:
        nonlocal replacement_installed
        real_replace(candidate, target)
        if ".compact-" in str(candidate) and target == source:
            replacement_installed = True

    def fail_replacement_fsync(_descriptor: int) -> None:
        nonlocal failure_injected
        if replacement_installed and not failure_injected:
            failure_injected = True
            raise OSError("injected replacement fsync failure")

    monkeypatch.setattr(compact, "_replace_path", mark_replacement_install)
    monkeypatch.setattr(compact, "_fsync_descriptor", fail_replacement_fsync)

    with pytest.raises(OSError, match="injected replacement fsync failure"):
        compact.execute_sqlite_compaction(_database_url(source), confirm_stopped=True)

    assert source.read_bytes() == before
    assert list(tmp_path.glob("*.pre-compact-*")) == []
    assert not Path(f"{source}.compact.lock").exists()


def test_compact_cli_dry_run_prints_plan(tmp_path: Path, monkeypatch, capsys) -> None:
    source = tmp_path / "store.db"
    _create_fragmented_database(source)
    monkeypatch.setattr(
        sys,
        "argv",
        ["codex-lb-db", "--db-url", _database_url(source), "compact", "--dry-run"],
    )

    migrate.main()

    output = capsys.readouterr().out
    assert f"source={source}" in output
    assert "reclaimable_bytes=" in output
    assert "required_free_bytes=" in output


def test_compact_cli_execute_does_not_call_dry_run_planner(monkeypatch, capsys) -> None:
    outcome = SimpleNamespace(
        backup=Path("/tmp/store.pre-compact.db"),
        source_bytes_after=123,
        reclaimed_bytes=456,
    )
    execute_calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        compact,
        "plan_sqlite_compaction",
        lambda _url: (_ for _ in ()).throw(AssertionError("dry-run planner called")),
    )

    def execute_mock(database_url: str, *, confirm_stopped: bool):
        execute_calls.append((database_url, confirm_stopped))
        return outcome

    monkeypatch.setattr(compact, "execute_sqlite_compaction", execute_mock)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "codex-lb-db",
            "--db-url",
            "sqlite+aiosqlite:////tmp/store.db",
            "compact",
            "--execute",
            "--confirm-stopped",
        ],
    )

    migrate.main()

    output = capsys.readouterr().out
    assert "backup=/tmp/store.pre-compact.db" in output
    assert "source_bytes_after=123" in output
    assert "reclaimed_bytes=456" in output
    assert execute_calls == [("sqlite+aiosqlite:////tmp/store.db", True)]

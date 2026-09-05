from __future__ import annotations

import os
import shutil
import sqlite3
import stat
import tempfile
import urllib.parse
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from app.db.sqlite_utils import (
    SqliteIntegrityCheckMode,
    check_sqlite_integrity,
    sqlite_connection,
    sqlite_db_path_from_url,
)

_INCREMENTAL_AUTO_VACUUM = 2
_MIN_FREE_SPACE_RESERVE = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SqliteCompactionPlan:
    source: Path
    source_bytes: int
    page_size: int
    page_count: int
    freelist_pages: int
    reclaimable_bytes: int
    auto_vacuum: int
    free_bytes: int
    required_free_bytes: int


@dataclass(frozen=True, slots=True)
class SqliteCompactionOutcome:
    source: Path
    backup: Path
    source_bytes_before: int
    source_bytes_after: int
    reclaimed_bytes: int


@dataclass(frozen=True, slots=True)
class _SchemaIdentity:
    application_id: int
    user_version: int
    alembic_revisions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _PathSignature:
    exists: bool
    size: int = 0
    mtime_ns: int = 0
    inode: int = 0


class _FileMetadata(Protocol):
    @property
    def st_mode(self) -> int: ...

    @property
    def st_uid(self) -> int: ...

    @property
    def st_gid(self) -> int: ...

    @property
    def st_dev(self) -> int: ...

    @property
    def st_ino(self) -> int: ...


def _resolve_source(database_url: str) -> Path:
    source = sqlite_db_path_from_url(database_url)
    if source is None:
        raise RuntimeError("compaction requires a file-backed SQLite database URL")
    if source.is_symlink():
        raise RuntimeError("compaction requires a direct SQLite database path, not a symbolic link")
    resolved = source.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"sqlite database not found: {resolved}")
    return resolved


def _path_signature(path: Path) -> _PathSignature:
    try:
        path_stat = path.stat()
    except FileNotFoundError:
        return _PathSignature(exists=False)
    return _PathSignature(
        exists=True,
        size=path_stat.st_size,
        mtime_ns=path_stat.st_mtime_ns,
        inode=path_stat.st_ino,
    )


def _path_stat_for_identity(path: Path, expected_identity: tuple[int, int]) -> os.stat_result | None:
    try:
        path_stat = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(path_stat.st_mode) or (path_stat.st_dev, path_stat.st_ino) != expected_identity:
        return None
    return path_stat


def _path_matches_identity(path: Path, expected_identity: tuple[int, int]) -> bool:
    return _path_stat_for_identity(path, expected_identity) is not None


def _directory_signature(path: Path) -> tuple[int, int, int, int]:
    path_stat = path.stat()
    return (path_stat.st_dev, path_stat.st_ino, path_stat.st_mtime_ns, path_stat.st_ctime_ns)


def _database_state_signature(source: Path) -> tuple[_PathSignature, ...]:
    return tuple(_path_signature(path) for path in (source, Path(f"{source}-wal"), Path(f"{source}-journal")))


def _rollback_journal_may_be_hot(journal: Path) -> bool:
    try:
        if journal.stat().st_size <= 512:
            return False
        with journal.open("rb") as handle:
            return any(handle.read(8))
    except FileNotFoundError:
        return False


def _read_plan(source: Path) -> SqliteCompactionPlan:
    before_signature = _database_state_signature(source)
    wal = Path(f"{source}-wal")
    if _path_signature(wal).size > 0:
        raise RuntimeError("dry-run requires a checkpointed SQLite database without WAL data")
    journal = Path(f"{source}-journal")
    if _rollback_journal_may_be_hot(journal):
        raise RuntimeError("dry-run cannot safely inspect a potentially hot SQLite rollback journal")
    connection = sqlite3.connect(f"{source.as_uri()}?mode=ro&immutable=1", uri=True)
    try:
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        freelist_pages = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
        auto_vacuum = int(connection.execute("PRAGMA auto_vacuum").fetchone()[0])
    finally:
        connection.close()
    if _database_state_signature(source) != before_signature:
        raise RuntimeError("SQLite database or sidecar changed during dry-run; stop writers and retry")
    source_bytes = before_signature[0].size
    free_bytes = shutil.disk_usage(source.parent).free
    required_free_bytes = 2 * source_bytes + _MIN_FREE_SPACE_RESERVE
    if auto_vacuum != _INCREMENTAL_AUTO_VACUUM:
        output_bytes = _incremental_auto_vacuum_output_bytes_for_pages(
            page_size=page_size,
            page_count=page_count,
        )
        required_free_bytes = source_bytes + 2 * output_bytes + _MIN_FREE_SPACE_RESERVE
    return SqliteCompactionPlan(
        source=source,
        source_bytes=source_bytes,
        page_size=page_size,
        page_count=page_count,
        freelist_pages=freelist_pages,
        reclaimable_bytes=page_size * freelist_pages,
        auto_vacuum=auto_vacuum,
        free_bytes=free_bytes,
        required_free_bytes=required_free_bytes,
    )


def plan_sqlite_compaction(database_url: str) -> SqliteCompactionPlan:
    return _read_plan(_resolve_source(database_url))


def _timestamp(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def _next_sibling(source: Path, *, label: str, timestamp: str) -> Path:
    suffix = source.suffix or ".db"
    candidate = source.with_name(f"{source.stem}.{label}-{timestamp}{suffix}")
    sequence = 1
    while any(
        os.path.lexists(Path(f"{candidate}{sidecar_suffix}")) for sidecar_suffix in ("", "-wal", "-shm", "-journal")
    ):
        candidate = source.with_name(f"{source.stem}.{label}-{timestamp}-{sequence}{suffix}")
        sequence += 1
    return candidate


def _schema_identity(connection: sqlite3.Connection) -> _SchemaIdentity:
    application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
    user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    has_alembic = (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alembic_version' LIMIT 1"
        ).fetchone()
        is not None
    )
    revisions = (
        tuple(str(row[0]) for row in connection.execute("SELECT version_num FROM alembic_version ORDER BY version_num"))
        if has_alembic
        else ()
    )
    return _SchemaIdentity(
        application_id=application_id,
        user_version=user_version,
        alembic_revisions=revisions,
    )


def _checkpoint_wal(connection: sqlite3.Connection) -> None:
    row = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if row is not None and int(row[0]) != 0:
        raise RuntimeError("SQLite WAL checkpoint is busy; stop every application replica")


@contextmanager
def _existing_sqlite_connection(path: Path) -> Iterator[sqlite3.Connection]:
    encoded_path = urllib.parse.quote(str(path), safe="/:")
    connection = sqlite3.connect(f"file:{encoded_path}?mode=rw&nofollow=1", uri=True)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _data_version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA data_version").fetchone()[0])


def _incremental_auto_vacuum_output_bytes_for_pages(*, page_size: int, page_count: int) -> int:
    pointer_map_interval = max(1, page_size // 5)
    pointer_map_pages = (page_count + pointer_map_interval - 1) // pointer_map_interval + 1
    return (page_count + pointer_map_pages) * page_size


def _incremental_auto_vacuum_output_bytes(connection: sqlite3.Connection) -> int:
    return _incremental_auto_vacuum_output_bytes_for_pages(
        page_size=int(connection.execute("PRAGMA page_size").fetchone()[0]),
        page_count=int(connection.execute("PRAGMA page_count").fetchone()[0]),
    )


def _sqlite_temporary_directory() -> Path:
    candidates = [Path(value) for name in ("SQLITE_TMPDIR", "TMPDIR") if (value := os.environ.get(name))]
    candidates.extend((Path("/var/tmp"), Path("/usr/tmp"), Path("/tmp"), Path.cwd()))
    for candidate in candidates:
        if candidate.is_dir() and os.access(candidate, os.W_OK | os.X_OK):
            return candidate
    raise RuntimeError("no writable SQLite temporary-file directory is available")


def _same_filesystem(first: Path, second: Path) -> bool:
    return first.stat().st_dev == second.stat().st_dev


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _open_sync_descriptor(path: Path) -> int:
    return os.open(path, os.O_RDONLY)


def _fsync_descriptor(descriptor: int) -> None:
    os.fsync(descriptor)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_path(source: Path, target: Path) -> None:
    source.replace(target)


def _link_path(source: Path, target: Path) -> None:
    os.link(source, target)


def _reserve_path(target: Path) -> None:
    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)


def _preserve_file_metadata(path: Path, source_stat: _FileMetadata) -> None:
    source_mode = stat.S_IMODE(source_stat.st_mode)
    current_stat = path.stat()
    if (current_stat.st_uid, current_stat.st_gid) != (source_stat.st_uid, source_stat.st_gid):
        try:
            os.chown(path, source_stat.st_uid, source_stat.st_gid)
        except (AttributeError, OSError) as exc:
            raise RuntimeError("cannot preserve SQLite database uid/gid on compacted file") from exc
    # chown may clear setuid/setgid bits, so restore the mode last.
    path.chmod(source_mode)
    preserved_stat = path.stat()
    if stat.S_IMODE(preserved_stat.st_mode) != source_mode or (
        preserved_stat.st_uid,
        preserved_stat.st_gid,
    ) != (source_stat.st_uid, source_stat.st_gid):
        raise RuntimeError("compacted SQLite file metadata does not match the source")


def _restore_sidecar(source: Path, backup: Path) -> None:
    if not os.path.lexists(backup):
        return
    if os.path.lexists(source):
        backup.unlink()
    else:
        _replace_path(backup, source)


def _move_sidecar_to_reserved_target(source: Path, target: Path, moved: list[tuple[Path, Path]]) -> None:
    reserved = False
    try:
        _reserve_path(target)
        reserved = True
        _replace_path(source, target)
        moved.append((source, target))
    except BaseException:
        if reserved:
            _restore_sidecar(source, target)
        raise


def _backup_sidecars(source: Path, backup: Path) -> list[tuple[Path, Path]]:
    moved: list[tuple[Path, Path]] = []
    try:
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(f"{source}{suffix}")
            if os.path.lexists(sidecar):
                _move_sidecar_to_reserved_target(sidecar, Path(f"{backup}{suffix}"), moved)
    except BaseException:
        _restore_sidecars(moved)
        raise
    return moved


def _restore_sidecars(moved: list[tuple[Path, Path]]) -> None:
    for source, backup in reversed(moved):
        _restore_sidecar(source, backup)


def _unlink_owned_lock(lock_path: Path, lock_descriptor: int) -> None:
    held_stat = os.fstat(lock_descriptor)
    try:
        current_stat = lock_path.lstat()
    except FileNotFoundError:
        return
    if (current_stat.st_dev, current_stat.st_ino) == (held_stat.st_dev, held_stat.st_ino):
        lock_path.unlink()


def sqlite_maintenance_lock_path(source: Path) -> Path:
    return source.with_name(f"{source.name}.compact.lock")


def acquire_sqlite_maintenance_lock(source: Path) -> tuple[Path, int]:
    lock_path = sqlite_maintenance_lock_path(source)
    try:
        lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(f"compaction lock already exists: {lock_path}") from exc
    try:
        os.write(lock_descriptor, f"pid={os.getpid()}\n".encode())
        os.fsync(lock_descriptor)
    except BaseException:
        try:
            _unlink_owned_lock(lock_path, lock_descriptor)
        finally:
            os.close(lock_descriptor)
        raise
    return lock_path, lock_descriptor


def release_sqlite_maintenance_lock(lock_path: Path, lock_descriptor: int) -> None:
    try:
        _unlink_owned_lock(lock_path, lock_descriptor)
    finally:
        os.close(lock_descriptor)


def execute_sqlite_compaction(
    database_url: str,
    *,
    confirm_stopped: bool,
    now: datetime | None = None,
) -> SqliteCompactionOutcome:
    if not confirm_stopped:
        raise RuntimeError("--execute requires --confirm-stopped after every application replica is stopped")
    if os.name == "nt":
        raise RuntimeError("safe SQLite compaction execution is unsupported on Windows")
    source = _resolve_source(database_url)
    pending_wal_bytes = _path_signature(Path(f"{source}-wal")).size
    source_bytes = source.stat().st_size + pending_wal_bytes
    free_bytes = shutil.disk_usage(source.parent).free
    # A checkpoint can fold committed WAL pages into the main database before
    # VACUUM INTO starts, so reserve against the logical source size first.
    required_free_bytes = 2 * source_bytes + _MIN_FREE_SPACE_RESERVE
    if free_bytes < required_free_bytes:
        raise RuntimeError(f"insufficient free space for compaction: required={required_free_bytes} free={free_bytes}")

    timestamp = _timestamp(now)
    backup: Path | None = None
    temporary_directory = Path(tempfile.mkdtemp(prefix=f".{source.name}.compact-", dir=source.parent))
    temporary = temporary_directory / "compacted.db"
    try:
        lock_path, lock_descriptor = acquire_sqlite_maintenance_lock(source)
    except BaseException:
        shutil.rmtree(temporary_directory)
        raise
    backup_created = False
    replacement_installed = False
    replacement_connection: sqlite3.Connection | None = None
    source_sync_descriptor: int | None = None
    temporary_sync_descriptor: int | None = None
    replacement_identity: tuple[int, int] | None = None
    completed = False
    moved_sidecars: list[tuple[Path, Path]] = []
    try:
        original_source_stat = source.stat()
        original_source_identity = (original_source_stat.st_dev, original_source_stat.st_ino)
        source_sync_descriptor = _open_sync_descriptor(source)
        source_integrity = check_sqlite_integrity(
            source,
            mode=SqliteIntegrityCheckMode.QUICK,
            require_existing=True,
        )
        if not source_integrity.ok:
            raise RuntimeError(f"source SQLite quick_check failed: {source_integrity.details}")
        if not _path_matches_identity(source, original_source_identity):
            raise RuntimeError("source path changed before compaction")
        source_parent_signature = _directory_signature(source.parent)

        with _existing_sqlite_connection(source) as source_connection:
            if (
                not _path_matches_identity(source, original_source_identity)
                or _directory_signature(source.parent) != source_parent_signature
            ):
                raise RuntimeError("source path changed before compaction")
            source_connection.execute("PRAGMA busy_timeout=0")
            _checkpoint_wal(source_connection)
            source_bytes = os.fstat(source_sync_descriptor).st_size
            free_bytes = shutil.disk_usage(source.parent).free
            required_free_bytes = 2 * source_bytes + _MIN_FREE_SPACE_RESERVE
            if free_bytes < required_free_bytes:
                raise RuntimeError(
                    f"insufficient free space for compaction: required={required_free_bytes} free={free_bytes}"
                )
            source_identity = _schema_identity(source_connection)
            data_version = _data_version(source_connection)
            previous_umask = os.umask(0o077)
            try:
                source_connection.execute("VACUUM INTO ?", (str(temporary),))
            finally:
                os.umask(previous_umask)
            if _data_version(source_connection) != data_version:
                raise RuntimeError("source database changed during compaction; keep the application stopped")
            with sqlite_connection(temporary) as compacted_connection:
                compacted_connection.execute("PRAGMA journal_mode=DELETE")
                if int(compacted_connection.execute("PRAGMA auto_vacuum").fetchone()[0]) != _INCREMENTAL_AUTO_VACUUM:
                    output_bytes = _incremental_auto_vacuum_output_bytes(compacted_connection)
                    required_free_bytes = output_bytes + _MIN_FREE_SPACE_RESERVE
                    sqlite_temporary_directory = _sqlite_temporary_directory()
                    free_bytes = shutil.disk_usage(source.parent).free
                    if _same_filesystem(source.parent, sqlite_temporary_directory):
                        combined_required_free_bytes = 2 * output_bytes + _MIN_FREE_SPACE_RESERVE
                        if free_bytes < combined_required_free_bytes:
                            raise RuntimeError(
                                "insufficient free space for compaction: "
                                f"required={combined_required_free_bytes} free={free_bytes}"
                            )
                    else:
                        if free_bytes < required_free_bytes:
                            raise RuntimeError(
                                "insufficient free space for compaction: "
                                f"required={required_free_bytes} free={free_bytes}"
                            )
                        temporary_free_bytes = shutil.disk_usage(sqlite_temporary_directory).free
                        if temporary_free_bytes < required_free_bytes:
                            raise RuntimeError(
                                "insufficient free space for SQLite temporary files: "
                                f"required={required_free_bytes} free={temporary_free_bytes}"
                            )
                    compacted_connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
                    compacted_connection.execute("VACUUM")
                compacted_identity = _schema_identity(compacted_connection)
            if compacted_identity != source_identity:
                raise RuntimeError("compacted SQLite schema identity does not match the source")
            _preserve_file_metadata(temporary, original_source_stat)
            compacted_integrity = check_sqlite_integrity(temporary, mode=SqliteIntegrityCheckMode.QUICK)
            if not compacted_integrity.ok:
                raise RuntimeError(f"compacted SQLite quick_check failed: {compacted_integrity.details}")
            temporary_sync_descriptor = _open_sync_descriptor(temporary)
            replacement_stat = os.fstat(temporary_sync_descriptor)
            replacement_identity = (replacement_stat.st_dev, replacement_stat.st_ino)
            # Block SQLite writers until the verified replacement is durable. A
            # file lock alone cannot prevent a process with an open SQLite
            # connection from committing between the final checks and rename.
            source_connection.execute("BEGIN EXCLUSIVE")
            if _data_version(source_connection) != data_version:
                raise RuntimeError("source database changed during compaction; keep the application stopped")
            source_stat = _path_stat_for_identity(source, original_source_identity)
            if source_stat is None:
                raise RuntimeError("source path changed during compaction")
            source_signature = (
                source_stat.st_dev,
                source_stat.st_ino,
                source_stat.st_size,
                source_stat.st_mtime_ns,
            )
            _fsync_descriptor(source_sync_descriptor)
            _fsync_descriptor(temporary_sync_descriptor)
            current_source_stat = source.lstat()
            if (
                current_source_stat.st_dev,
                current_source_stat.st_ino,
                current_source_stat.st_size,
                current_source_stat.st_mtime_ns,
            ) != source_signature:
                raise RuntimeError("source database changed before compacted replacement")
            wal_path = Path(f"{source}-wal")
            if wal_path.exists() and wal_path.stat().st_size > 0:
                raise RuntimeError("non-empty SQLite WAL remains after checkpoint")
            # Preserve the original inode first, then install the verified
            # output with one atomic replace. The live source path is never
            # absent, and the exclusive SQLite transaction remains held.
            backup = _next_sibling(source, label="pre-compact", timestamp=timestamp)
            _link_path(source, backup)
            backup_created = True
            if not _path_matches_identity(backup, original_source_identity):
                raise RuntimeError("source path changed while creating compaction backup")
            _fsync_directory(source.parent)
            try:
                moved_sidecars = _backup_sidecars(source, backup)
                if not _path_matches_identity(source, original_source_identity):
                    raise RuntimeError("source path changed before compacted replacement")
                replacement_connection = sqlite3.connect(str(temporary))
                replacement_connection.execute("PRAGMA busy_timeout=0")
                replacement_connection.execute("BEGIN EXCLUSIVE")
                try:
                    if not _path_matches_identity(source, original_source_identity):
                        raise RuntimeError("source path changed before compacted replacement")
                    _replace_path(temporary, source)
                finally:
                    replacement_installed = not temporary.exists()
            except BaseException:
                if not replacement_installed:
                    _restore_sidecars(moved_sidecars)
                    if backup_created:
                        backup.unlink(missing_ok=True)
                        backup_created = False
                raise
            _fsync_descriptor(temporary_sync_descriptor)
            _fsync_directory(source.parent)
            installed_stat = _path_stat_for_identity(source, replacement_identity)
            if installed_stat is None:
                raise RuntimeError("source path changed after compacted replacement")
            after_bytes = installed_stat.st_size
        completed = True
        assert backup is not None
        return SqliteCompactionOutcome(
            source=source,
            backup=backup,
            source_bytes_before=source_bytes,
            source_bytes_after=after_bytes,
            reclaimed_bytes=max(source_bytes - after_bytes, 0),
        )
    finally:
        if temporary.exists():
            temporary.unlink()
        if (
            not completed
            and replacement_installed
            and backup is not None
            and backup.exists()
            and replacement_identity is not None
            and _path_matches_identity(source, replacement_identity)
        ):
            _replace_path(backup, source)
            backup_created = False
            _restore_sidecars(moved_sidecars)
            if source_sync_descriptor is not None:
                _fsync_descriptor(source_sync_descriptor)
            _fsync_directory(source.parent)
        elif (
            not completed
            and replacement_installed
            and backup is not None
            and backup.exists()
            and not os.path.lexists(source)
        ):
            _replace_path(backup, source)
            backup_created = False
            _restore_sidecars(moved_sidecars)
            if source_sync_descriptor is not None:
                _fsync_descriptor(source_sync_descriptor)
            _fsync_directory(source.parent)
        if not completed and backup_created and not replacement_installed and backup is not None:
            backup.unlink(missing_ok=True)
        shutil.rmtree(temporary_directory)
        try:
            if replacement_connection is not None:
                replacement_connection.close()
        finally:
            try:
                if temporary_sync_descriptor is not None:
                    os.close(temporary_sync_descriptor)
            finally:
                try:
                    if source_sync_descriptor is not None:
                        os.close(source_sync_descriptor)
                finally:
                    release_sqlite_maintenance_lock(lock_path, lock_descriptor)


__all__ = [
    "SqliteCompactionOutcome",
    "SqliteCompactionPlan",
    "acquire_sqlite_maintenance_lock",
    "execute_sqlite_compaction",
    "plan_sqlite_compaction",
    "release_sqlite_maintenance_lock",
    "sqlite_maintenance_lock_path",
]

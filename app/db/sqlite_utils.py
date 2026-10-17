from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import urllib.parse
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TypedDict, cast


@dataclass(slots=True)
class IntegrityCheck:
    ok: bool
    details: str | None


class SqliteIntegrityCheckMode(str, Enum):
    QUICK = "quick"
    FULL = "full"


class SqliteRunState(str, Enum):
    """How the previous process left the SQLite store."""

    RUNNING = "running"
    CLEAN = "clean"


@dataclass(slots=True, frozen=True)
class SqliteFileIdentity:
    """The filesystem identity captured by a run-state transition."""

    dev: int
    ino: int
    size: int
    mtime_ns: int
    ctime_ns: int

    def as_payload(self) -> SqliteFileIdentityPayload:
        return {
            "dev": self.dev,
            "ino": self.ino,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "ctime_ns": self.ctime_ns,
        }

    @classmethod
    def from_payload(cls, payload: object) -> SqliteFileIdentity | None:
        if not isinstance(payload, dict) or payload.keys() != _SQLITE_FILE_IDENTITY_KEYS:
            return None
        typed_payload = cast(SqliteFileIdentityPayload, cast(object, payload))
        values = (
            typed_payload["dev"],
            typed_payload["ino"],
            typed_payload["size"],
            typed_payload["mtime_ns"],
            typed_payload["ctime_ns"],
        )
        if any(type(value) is not int for value in values):
            return None
        return cls(
            dev=typed_payload["dev"],
            ino=typed_payload["ino"],
            size=typed_payload["size"],
            mtime_ns=typed_payload["mtime_ns"],
            ctime_ns=typed_payload["ctime_ns"],
        )


class SqliteFileIdentityPayload(TypedDict):
    dev: int
    ino: int
    size: int
    mtime_ns: int
    ctime_ns: int


_SQLITE_FILE_IDENTITY_KEYS = frozenset({"dev", "ino", "size", "mtime_ns", "ctime_ns"})


@dataclass(slots=True, frozen=True)
class SqliteRunStateRecord:
    """A run-state transition and the database identity captured with it."""

    state: SqliteRunState
    identity: SqliteFileIdentity | None


@contextmanager
def sqlite_connection(path: str | Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(str(path))
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _sqlite_path_uses_sqlalchemy_windows_escapes(path: str) -> bool:
    lower_path = path.lower()
    if (
        len(lower_path) >= 5
        and lower_path[1:4] == "%3a"
        and lower_path[0].isalpha()
        and (lower_path[4:7] in ("%5c", "%2f") or lower_path[4] in ("\\", "/"))
    ):
        return True
    return lower_path.startswith("%5c%5c")


def _sqlite_path_is_raw_windows_drive(path: str) -> bool:
    return len(path) >= 3 and path[1] == ":" and path[0].isalpha() and path[2] in ("\\", "/")


def _sqlite_path_is_raw_windows_unc(path: str) -> bool:
    return path.startswith("\\\\")


def _decode_sqlalchemy_windows_sqlite_path(path: str) -> str:
    if not _sqlite_path_uses_sqlalchemy_windows_escapes(path):
        return path
    return urllib.parse.unquote(path)


def _sqlite_uri_query(url: str, path_start: int) -> dict[str, list[str]]:
    """Return decoded query values from a SQLite URL's URI suffix."""
    query_start = url.find("?", path_start)
    if query_start < 0:
        return {}
    fragment_start = url.find("#", query_start + 1)
    query_end = len(url) if fragment_start < 0 else fragment_start
    return urllib.parse.parse_qs(url[query_start + 1 : query_end], keep_blank_values=True)


def _sqlite_uri_query_value_is_true(value: str) -> bool:
    """Match the truthy values accepted by SQLAlchemy's SQLite dialect."""
    return value.strip().lower() in {"1", "t", "true", "y", "yes", "on"}


def _sqlite_uri_enabled(url: str, path_start: int) -> bool:
    query = _sqlite_uri_query(url, path_start)
    return any(_sqlite_uri_query_value_is_true(value) for value in query.get("uri", []))


def _sqlite_uri_is_memory(url: str, path_start: int, path: str) -> bool:
    """Return whether a SQLite URI denotes an in-memory database.

    A ``file:`` name is a filesystem path until the SQLite dialect is put in
    URI mode. Only then do ``mode=memory`` and SQLite's ``file::memory:`` URI
    have their in-memory meaning; otherwise the path retains its normal
    file-backed semantics for sidecars and startup checks.
    """
    if not path.startswith("file:") or not _sqlite_uri_enabled(url, path_start):
        return False
    query = _sqlite_uri_query(url, path_start)
    if any(value.strip().lower() == "memory" for value in query.get("mode", [])):
        return True
    return urllib.parse.unquote(path) == "file::memory:"


def _sqlite_uri_file_path(path: str) -> str | None:
    """Resolve a supported file-backed SQLite URI to its filesystem path."""
    parsed = urllib.parse.urlsplit(path)
    if parsed.scheme != "file":
        return path
    if parsed.netloc not in {"", "localhost"}:
        return None
    resolved = urllib.parse.unquote(parsed.path)
    # SQLite treats the tilde in ``file:~/store.db`` literally; unlike a
    # normal application path it is not a shell shorthand for the home
    # directory. Likewise, ``file:///C:/...`` uses one leading slash for the
    # authority-less URI syntax rather than as part of the Windows drive path.
    if len(resolved) >= 4 and resolved[0] == "/" and resolved[1].isalpha() and resolved[2] == ":":
        resolved = resolved[1:]
    return resolved


def sqlite_db_path_from_url(url: str) -> Path | None:
    if not (url.startswith("sqlite+aiosqlite:") or url.startswith("sqlite:")):
        return None

    marker = ":///"
    marker_index = url.find(marker)
    if marker_index < 0:
        return None

    path_start = marker_index + len(marker)
    path = url[path_start:]
    if _sqlite_path_is_raw_windows_drive(path) or _sqlite_path_is_raw_windows_unc(path):
        # Raw Windows drive and UNC paths are filesystem paths, not URL-encoded
        # forms: a `#` is a legal path character there (e.g. the decoded output
        # of `normalize_sqlite_url()`), so it must not be stripped as a URL
        # fragment separator.
        path = path.partition("?")[0]
    else:
        path = path.partition("?")[0]
        path = path.partition("#")[0]

    # SQLAlchemy's `URL.render_as_string()` percent-encodes Windows drive and
    # UNC SQLite paths (e.g. `sqlite:///C%3A%5CUsers%5C...%5Cstore.db`). Decode
    # those recognizable rendered Windows forms before opening the filesystem
    # path. Do not unquote arbitrary `%xx` sequences here: settings builds the
    # default SQLite URL directly from `data_dir`, so a valid literal path such
    # as `/var/lib/codex%20lb/store.db` must remain literal.
    path = _decode_sqlalchemy_windows_sqlite_path(path)

    if not path or path == ":memory:" or _sqlite_uri_is_memory(url, path_start, path):
        return None

    uri_file_path = path.startswith("file:") and _sqlite_uri_enabled(url, path_start)
    if uri_file_path:
        path = _sqlite_uri_file_path(path)
        if not path:
            return None

    # URI paths are already the exact SQLite target. In particular, a leading
    # ``~`` is literal in SQLite's ``file:`` URI syntax and must not be
    # expanded as it would be for a regular filesystem path.
    return Path(path) if uri_file_path else Path(path).expanduser()


def sqlite_url_is_memory(url: str) -> bool:
    """Return whether a SQLite URL opens an in-memory database."""
    if not (url.startswith("sqlite+aiosqlite:") or url.startswith("sqlite:")):
        return False

    marker = ":///"
    marker_index = url.find(marker)
    if marker_index < 0:
        # SQLAlchemy's ``sqlite:///``-less form (``sqlite://``) is its
        # in-memory URL.
        return True

    path_start = marker_index + len(marker)
    path = url[path_start:]
    if _sqlite_path_is_raw_windows_drive(path) or _sqlite_path_is_raw_windows_unc(path):
        path = path.partition("?")[0]
    else:
        path = path.partition("?")[0].partition("#")[0]
    path = _decode_sqlalchemy_windows_sqlite_path(path)
    return not path or path == ":memory:" or _sqlite_uri_is_memory(url, path_start, path)


def normalize_sqlite_url(url: str) -> str:
    if not (url.startswith("sqlite+aiosqlite:") or url.startswith("sqlite:")):
        return url

    marker = ":///"
    marker_index = url.find(marker)
    if marker_index < 0:
        return url

    path_start = marker_index + len(marker)
    suffix_index = len(url)
    for separator in ("?", "#"):
        separator_index = url.find(separator, path_start)
        if separator_index >= 0:
            suffix_index = min(suffix_index, separator_index)

    path = url[path_start:suffix_index]
    if not path or path == ":memory:":
        return url

    decoded_path = _decode_sqlalchemy_windows_sqlite_path(path)
    return f"{url[:path_start]}{decoded_path}{url[suffix_index:]}"


def _integrity_check_pragma(mode: SqliteIntegrityCheckMode) -> str:
    if mode == SqliteIntegrityCheckMode.QUICK:
        return "PRAGMA quick_check;"
    return "PRAGMA integrity_check;"


def check_sqlite_integrity(
    path: Path,
    *,
    mode: SqliteIntegrityCheckMode = SqliteIntegrityCheckMode.FULL,
) -> IntegrityCheck:
    if not path.exists():
        return IntegrityCheck(ok=True, details=None)

    try:
        with sqlite_connection(path) as conn:
            cursor = conn.execute(_integrity_check_pragma(mode))
            rows = [row[0] for row in cursor.fetchall()]
    except sqlite3.DatabaseError as exc:
        return IntegrityCheck(ok=False, details=str(exc))

    if len(rows) == 1 and rows[0] == "ok":
        return IntegrityCheck(ok=True, details=None)

    if not rows:
        return IntegrityCheck(ok=False, details=f"{mode.value}_check returned no rows")

    details = "; ".join(str(row) for row in rows)
    return IntegrityCheck(ok=False, details=details)


def integrity_check_pragma_name(mode: SqliteIntegrityCheckMode) -> str:
    return "quick_check" if mode == SqliteIntegrityCheckMode.QUICK else "integrity_check"


def sqlite_runstate_path(db_path: Path) -> Path:
    """Sidecar file recording how the previous process left ``db_path``."""
    return db_path.with_name(f"{db_path.name}.runstate")


def sqlite_runstate_lock_path(db_path: Path) -> Path:
    """Persistent SQLite sentinel used to fence one process onto ``db_path``."""
    return db_path.with_name(f"{db_path.name}.runstate.lock")


class SqliteRunStateLockError(RuntimeError):
    """The process could not obtain exclusive ownership of a SQLite store."""


class SqliteRunStateDurabilityError(OSError):
    """The failed run-state marker could not be durably invalidated."""


def acquire_sqlite_runstate_lock(db_path: Path) -> sqlite3.Connection:
    """Hold an exclusive sentinel transaction for the lifetime of one process.

    The sentinel is deliberately never unlinked. A ``BEGIN IMMEDIATE`` on its
    own SQLite file is portable, vanishes automatically when the process dies,
    and cannot be confused with the main database's application transactions.
    """
    lock_path = sqlite_runstate_lock_path(db_path)
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(str(lock_path), timeout=0, isolation_level=None)
        connection.execute("BEGIN IMMEDIATE")
    except (OSError, sqlite3.Error) as exc:
        if connection is not None:
            connection.close()
        raise SqliteRunStateLockError(
            f"Could not acquire the SQLite lifetime lock {lock_path}; "
            "another process may already be using this database"
        ) from exc
    return connection


def release_sqlite_runstate_lock(connection: sqlite3.Connection) -> None:
    """Release a lifetime sentinel lock without deleting its persistent file."""
    try:
        connection.rollback()
    except sqlite3.Error:
        # Closing still releases the lock when a rollback is interrupted or
        # the underlying sentinel has become unavailable.
        pass
    finally:
        connection.close()


def _sqlite_file_identity(db_path: Path) -> SqliteFileIdentity | None:
    """Identify the database file well enough to detect that it was replaced.

    Size and mtime alone are not enough: a restore that preserves timestamps
    (``tar -x``, ``cp -p``, ``rsync -a``) can reproduce both. The inode and
    device catch the replacement itself, and ctime catches an in-place
    metadata change. Any of these shifting for a benign reason only costs an
    extra scan, which is the safe direction.
    """
    try:
        stat_result = db_path.stat()
    except OSError:
        return None
    return SqliteFileIdentity(
        dev=stat_result.st_dev,
        ino=stat_result.st_ino,
        size=stat_result.st_size,
        mtime_ns=stat_result.st_mtime_ns,
        ctime_ns=stat_result.st_ctime_ns,
    )


# Windows cannot obtain a directory handle through ``os.open``: the underlying
# CreateFileW call refuses a directory and the failure surfaces as EACCES,
# which errno cannot tell apart from an ordinary permission denial. Decide by
# platform instead, so that on POSIX every open failure can be treated as the
# real failure it is.
_DIRECTORY_FSYNC_SUPPORTED = os.name == "posix"


def _fsync_directory(directory: Path) -> bool:
    """Persist a directory entry so a rename survives power loss.

    Returns ``False`` whenever a sync was expected and did not happen, so the
    caller can refuse to leave behind a record whose durability is unproven.
    A missing path, a permission denial, descriptor exhaustion, and an I/O
    error all count.

    Where a directory handle is not obtainable at all the sync is not
    attempted and this reports success. There is nothing this code can verify
    on such a platform, and failing closed would mean no Windows deployment
    could ever record a clean shutdown; rename durability there is the
    platform's guarantee to make.
    """
    if not _DIRECTORY_FSYNC_SUPPORTED:
        return True
    try:
        directory_fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return False
    try:
        os.fsync(directory_fd)
    except OSError:
        return False
    finally:
        os.close(directory_fd)
    return True


def read_sqlite_runstate(db_path: Path) -> SqliteRunState | None:
    """Return the recorded run state, or ``None`` when it cannot be trusted.

    ``None`` means "unknown" and callers MUST treat it as potentially
    unclean. A missing sidecar covers both a first run and an upgrade from a
    build that never wrote one, so the conservative reading is the only safe
    one.

    A ``clean`` record is honoured only while the database file still matches
    the device, inode, size, mtime, and ctime captured when the record was
    written. Restoring a backup or swapping the file in by hand therefore
    reads back as unknown rather than inheriting the previous file's clean
    record. Startup callers must acquire the matching lifetime lock before
    using this result to skip a check.
    """
    record = read_sqlite_runstate_record(db_path)
    return record.state if record is not None else None


def read_sqlite_runstate_record(db_path: Path) -> SqliteRunStateRecord | None:
    """Read the run state and its captured database identity.

    Startup uses the complete record to compare the prior ``clean`` identity
    with the newly persisted ``running`` identity. A clean record without a
    verifiable identity is unknown; a running record remains readable so the
    startup decision can fail closed on a missing identity.
    """
    try:
        raw = sqlite_runstate_path(db_path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    try:
        payload = json.loads(raw)
        state = SqliteRunState(payload["state"])
        identity = SqliteFileIdentity.from_payload(payload.get("identity"))
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return None
    if state is SqliteRunState.CLEAN:
        current_identity = _sqlite_file_identity(db_path)
        if identity is None or current_identity is None or identity != current_identity:
            return None
    return SqliteRunStateRecord(state=state, identity=identity)


def _invalidate_failed_sqlite_runstate(target: Path, tmp: Path | None) -> None:
    """Remove an untrusted run-state write and persist that removal.

    A failed replacement is safe to recover from only when both the temporary
    file and any previous target are gone and the parent directory sync
    confirms those removals. Raise a distinct error when that proof is
    unavailable so startup cannot continue while an older ``clean`` marker
    might still be trusted after a power loss.
    """
    cleanup_error: OSError | None = None
    for cleanup in (tmp, target):
        if cleanup is None:
            continue
        try:
            cleanup.unlink(missing_ok=True)
        except OSError as exc:
            cleanup_error = cleanup_error or exc
    if cleanup_error is not None:
        raise SqliteRunStateDurabilityError(
            f"could not remove failed SQLite run-state files for {target}"
        ) from cleanup_error
    try:
        directory_synced = _fsync_directory(target.parent)
    except OSError as exc:
        raise SqliteRunStateDurabilityError(
            f"could not persist removal of failed SQLite run-state files for {target}"
        ) from exc
    if not directory_synced:
        raise SqliteRunStateDurabilityError(f"could not persist removal of failed SQLite run-state files for {target}")


def write_sqlite_runstate(db_path: Path, state: SqliteRunState) -> bool:
    """Record ``state`` atomically. Returns ``False`` if it could not be recorded.

    The payload and the directory entry are both fsynced, so a power loss
    cannot retain an earlier ``clean`` record while losing the ``running``
    transition that replaced it. A directory sync that is attempted and fails
    is treated as a failed write, because a record whose durability could not
    be established must not be trusted. In WAL mode the main database file can keep
    its size and mtime across a long run, so the sidecar cannot rely on the
    file identity alone to invalidate a lost transition.

    A failed write must never leave a stale ``clean`` sidecar behind, because
    that would tell the next startup to skip the integrity check for a store
    this process may have left mid-write. The fallback is to remove the
    temporary and target entries, then sync the directory. If that
    invalidation cannot be confirmed, ``SqliteRunStateDurabilityError`` is
    raised so a startup caller can fail closed instead of trusting the marker.
    The caller is responsible for holding the lifetime lock around state
    changes.
    """
    target = sqlite_runstate_path(db_path)
    tmp: Path | None = None
    tmp_fd: int | None = None
    identity = _sqlite_file_identity(db_path)
    payload = json.dumps({"state": state.value, "identity": identity.as_payload() if identity else None})
    try:
        tmp_fd, tmp_name = tempfile.mkstemp(prefix=f"{target.name}.", suffix=".tmp", dir=target.parent)
        tmp = Path(tmp_name)
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as handle:
            tmp_fd = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        if not _fsync_directory(target.parent):
            raise OSError("could not sync the run-state directory entry")
        return True
    except OSError:
        if tmp_fd is not None:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
        for cleanup in (tmp, target):
            if cleanup is None:
                continue
            try:
                cleanup.unlink(missing_ok=True)
            except OSError:
                pass
        # The target cleanup can remove an older clean marker even when the
        # replacement itself failed. A caller must distinguish a durably
        # invalidated marker from cleanup whose durability is unknown.
        _invalidate_failed_sqlite_runstate(target, tmp)
        return False

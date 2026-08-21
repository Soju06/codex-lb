from __future__ import annotations

import json
import os
import sqlite3
import urllib.parse
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


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


def sqlite_db_path_from_url(url: str) -> Path | None:
    if not (url.startswith("sqlite+aiosqlite:") or url.startswith("sqlite:")):
        return None

    marker = ":///"
    marker_index = url.find(marker)
    if marker_index < 0:
        return None

    path = url[marker_index + len(marker) :]
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

    if not path or path == ":memory:":
        return None

    return Path(path).expanduser()


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


def _sqlite_file_identity(db_path: Path) -> dict[str, int] | None:
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
    return {
        "dev": stat_result.st_dev,
        "ino": stat_result.st_ino,
        "size": stat_result.st_size,
        "mtime_ns": stat_result.st_mtime_ns,
        "ctime_ns": stat_result.st_ctime_ns,
    }


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
    the size and mtime captured when the record was written. Restoring a
    backup or swapping the file in by hand therefore reads back as unknown
    rather than inheriting the previous file's clean record.
    """
    try:
        raw = sqlite_runstate_path(db_path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    try:
        record = json.loads(raw)
        state = SqliteRunState(record["state"])
    except (ValueError, TypeError, KeyError):
        return None
    if state is SqliteRunState.CLEAN and record.get("identity") != _sqlite_file_identity(db_path):
        return None
    return state


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
    sidecar entirely, which reads back as unknown and forces the check.
    """
    target = sqlite_runstate_path(db_path)
    tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    payload = json.dumps({"state": state.value, "identity": _sqlite_file_identity(db_path)})
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        if not _fsync_directory(target.parent):
            raise OSError("could not sync the run-state directory entry")
        return True
    except OSError:
        for cleanup in (tmp, target):
            try:
                cleanup.unlink(missing_ok=True)
            except OSError:
                pass
        return False

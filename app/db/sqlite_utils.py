from __future__ import annotations

import sqlite3
import urllib.parse
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


def sqlite_db_path_from_url(url: str) -> Path | None:
    if not (url.startswith("sqlite+aiosqlite:") or url.startswith("sqlite:")):
        return None

    marker = ":///"
    marker_index = url.find(marker)
    if marker_index < 0:
        return None

    path = url[marker_index + len(marker) :]
    path = path.partition("?")[0]
    path = path.partition("#")[0]

    # SQLAlchemy`s `URL.render_as_string()` percent-encodes the database path on
    # Windows (e.g. `sqlite:///C%3A%5CUsers%5C...%5Cstore.db`). Without decoding
    # it here, the literal percent-escaped string reaches `sqlite3.connect()`,
    # which either fails with "unable to open database file" or creates a stray
    # 0-byte database next to the working directory. Decode the path before
    # turning it into a `Path` so the real file is opened.
    path = urllib.parse.unquote(path)

    if not path or path == ":memory:":
        return None

    return Path(path).expanduser()


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
        with sqlite3.connect(str(path)) as conn:
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

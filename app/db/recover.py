from __future__ import annotations

import argparse
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from app.core.config.settings import get_settings
from app.db.sqlite_utils import IntegrityCheck, check_sqlite_integrity, sqlite_db_path_from_url

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RecoveryOptions:
    source: Path
    output: Path
    replace: bool


@dataclass(slots=True)
class RecoveryOutcome:
    source: Path
    output: Path
    replaced: bool
    integrity: IntegrityCheck


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _default_output_path(source: Path) -> Path:
    suffix = source.suffix or ".db"
    return source.with_name(f"{source.stem}.recover-{_timestamp()}{suffix}")


def _load_dump(source: Path) -> str:
    try:
        with sqlite3.connect(str(source)) as conn:
            return "\n".join(conn.iterdump())
    except sqlite3.DatabaseError as exc:
        message = f"failed to read sqlite dump: {exc}"
        raise RuntimeError(message) from exc


def _write_dump(output: Path, dump: str) -> None:
    try:
        with sqlite3.connect(str(output)) as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.executescript(dump)
            conn.execute("PRAGMA foreign_keys=ON")
    except sqlite3.DatabaseError as exc:
        message = f"failed to write sqlite dump: {exc}"
        raise RuntimeError(message) from exc


def recover_sqlite_db(options: RecoveryOptions) -> RecoveryOutcome:
    if not options.source.exists():
        raise FileNotFoundError(f"sqlite database not found: {options.source}")
    if options.output.exists():
        raise FileExistsError(f"output database already exists: {options.output}")

    integrity = check_sqlite_integrity(options.source)
    if not integrity.ok:
        logger.warning("SQLite integrity check failed details=%s", integrity.details)
    else:
        logger.info("SQLite integrity check OK. Proceeding with export/import.")

    dump = _load_dump(options.source)
    _write_dump(options.output, dump)

    if options.replace:
        backup = options.source.with_name(f"{options.source.name}.corrupt-{_timestamp()}")
        options.source.replace(backup)
        options.output.replace(options.source)
        return RecoveryOutcome(
            source=backup,
            output=options.source,
            replaced=True,
            integrity=integrity,
        )

    return RecoveryOutcome(
        source=options.source,
        output=options.output,
        replaced=False,
        integrity=integrity,
    )


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recover a sqlite database via .dump/.executescript.")
    parser.add_argument("--db", help="Path to sqlite database (defaults to settings.database_url)")
    parser.add_argument(
        "--output",
        help="Output sqlite database path (default: <db>.recover-<timestamp>.db)",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace the source database after recovery (source is renamed with .corrupt-<timestamp>)",
    )
    return parser.parse_args(args=argv)


def _resolve_source_path(db_path: str | None) -> Path:
    if db_path:
        return Path(db_path).expanduser()
    settings = get_settings()
    resolved = sqlite_db_path_from_url(settings.database_url)
    if resolved is None:
        raise RuntimeError("database_url is not a sqlite file path")
    return resolved


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    source = _resolve_source_path(args.db)
    output = Path(args.output).expanduser() if args.output else _default_output_path(source)
    outcome = recover_sqlite_db(RecoveryOptions(source=source, output=output, replace=bool(args.replace)))
    if outcome.replaced:
        logger.info("Recovered database written to %s (original saved at %s)", outcome.output, outcome.source)
    else:
        logger.info("Recovered database written to %s", outcome.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

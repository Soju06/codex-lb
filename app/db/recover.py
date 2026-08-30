from __future__ import annotations

import argparse
import glob
import logging
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Sequence

from app.core.config.settings import get_settings
from app.db.sqlite_utils import IntegrityCheck, check_sqlite_integrity, sqlite_connection, sqlite_db_path_from_url

logger = logging.getLogger(__name__)

_SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


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


def _sqlite_sidecar_paths(db_path: Path) -> tuple[Path, ...]:
    fixed = tuple(db_path.with_name(f"{db_path.name}{suffix}") for suffix in _SQLITE_SIDECAR_SUFFIXES)
    master = tuple(sorted(db_path.parent.glob(f"{glob.escape(db_path.name)}-mj*")))
    return (*fixed, *master)


def _is_sqlite_sidecar_path(database: Path, candidate: Path) -> bool:
    database_parent = os.path.normcase(str(database.parent))
    candidate_parent = os.path.normcase(str(candidate.parent))
    if database_parent != candidate_parent:
        return False

    database_name = os.path.normcase(database.name)
    candidate_name = os.path.normcase(candidate.name)
    fixed_names = {f"{database_name}{suffix}" for suffix in _SQLITE_SIDECAR_SUFFIXES}
    return candidate_name in fixed_names or candidate_name.startswith(f"{database_name}-mj")


def _validate_recovery_paths(source: Path, output: Path) -> None:
    # Check lexical names before resolving symlinks. A sidecar path can itself
    # be a symlink to a real database; resolving it first would hide the
    # source/output overlap and let cleanup unlink the caller's symlink.
    lexical_source = Path(os.path.abspath(source.expanduser()))
    lexical_output = Path(os.path.abspath(output.expanduser()))
    if _is_sqlite_sidecar_path(lexical_source, lexical_output):
        raise ValueError(f"output path overlaps a SQLite sidecar of source: {source} -> {output}")
    if _is_sqlite_sidecar_path(lexical_output, lexical_source):
        raise ValueError(f"source path overlaps a SQLite sidecar of output: {source} -> {output}")

    normalized_source = lexical_source.resolve(strict=False)
    normalized_output = lexical_output.resolve(strict=False)
    if normalized_source == normalized_output:
        raise ValueError(f"source and output paths must differ: {source}")
    if _is_sqlite_sidecar_path(normalized_source, normalized_output):
        raise ValueError(f"output path overlaps a SQLite sidecar of source: {source} -> {output}")
    if _is_sqlite_sidecar_path(normalized_output, normalized_source):
        raise ValueError(f"source path overlaps a SQLite sidecar of output: {source} -> {output}")


def _remove_sqlite_sidecars(db_path: Path) -> None:
    failures: list[tuple[Path, OSError]] = []
    for sidecar in _sqlite_sidecar_paths(db_path):
        try:
            sidecar.unlink(missing_ok=True)
        except OSError as exc:
            failures.append((sidecar, exc))
    if failures:
        details = "; ".join(f"{path}: {error}" for path, error in failures)
        raise RuntimeError(f"failed to remove SQLite sidecars: {details}")


@contextmanager
def _sqlite_recovery_lock(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Fence source export and output import with an exclusive SQLite lock.

    The lock blocks active writers while the source dump is generated and the
    recovered file is imported. The connection is closed when this context
    exits, before sidecars are removed or any filesystem rename, because
    Windows rejects file mutations with an open handle. Sidecar cleanup and
    the renames therefore require the caller to keep external writers
    quiescent during the bounded post-probe window.
    """
    connection = sqlite3.connect(str(db_path), timeout=0, isolation_level=None)
    acquired = False
    try:
        try:
            connection.execute("BEGIN EXCLUSIVE")
            acquired = True
        except sqlite3.Error as exc:
            raise RuntimeError(f"could not acquire exclusive SQLite recovery lock for {db_path}: {exc}") from exc
        yield connection
    finally:
        try:
            if acquired:
                connection.rollback()
        finally:
            # Do not let a rollback failure strand the recovery handle. The
            # rollback exception is preserved while close remains guaranteed.
            connection.close()


def _replace_recovered_database(source: Path, output: Path, backup: Path) -> None:
    source.replace(backup)
    try:
        # A writer can recreate source sidecars after the probe closes and just
        # before the source rename. Remove those orphaned entries while the
        # source path is empty, before installing the recovered database.
        _remove_sqlite_sidecars(source)
        output.replace(source)
    except (OSError, RuntimeError) as exc:
        try:
            backup.replace(source)
        except OSError as restore_exc:
            raise RuntimeError(
                f"failed to install recovered SQLite database at {source}: {exc}; "
                f"failed to restore the original database from {backup}: {restore_exc}"
            ) from exc
        raise RuntimeError(f"failed to install recovered SQLite database at {source}: {exc}") from exc


def _load_dump(connection: sqlite3.Connection) -> str:
    try:
        return "\n".join(connection.iterdump())
    except sqlite3.DatabaseError as exc:
        message = f"failed to read sqlite dump: {exc}"
        raise RuntimeError(message) from exc


def _write_dump(output: Path, dump: str) -> None:
    try:
        with sqlite_connection(output) as conn:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.executescript(dump)
            conn.execute("PRAGMA foreign_keys=ON")
    except sqlite3.DatabaseError as exc:
        message = f"failed to write sqlite dump: {exc}"
        raise RuntimeError(message) from exc


def recover_sqlite_db(options: RecoveryOptions) -> RecoveryOutcome:
    _validate_recovery_paths(options.source, options.output)
    if not options.source.exists():
        raise FileNotFoundError(f"sqlite database not found: {options.source}")
    if options.output.exists():
        raise FileExistsError(f"output database already exists: {options.output}")

    integrity = check_sqlite_integrity(options.source)
    if not integrity.ok:
        logger.warning("SQLite integrity check failed details=%s", integrity.details)
    else:
        logger.info("SQLite integrity check OK. Proceeding with export/import.")

    _remove_sqlite_sidecars(options.output)
    with _sqlite_recovery_lock(options.source) as source_connection:
        dump = _load_dump(source_connection)
        _write_dump(options.output, dump)

    if options.replace:
        # Recovery-owned SQLite handles are closed before any sidecar unlink.
        _remove_sqlite_sidecars(options.output)
        _remove_sqlite_sidecars(options.source)
        backup = options.source.with_name(f"{options.source.name}.corrupt-{_timestamp()}")
        _replace_recovered_database(options.source, options.output, backup)
        return RecoveryOutcome(
            source=backup,
            output=options.source,
            replaced=True,
            integrity=integrity,
        )

    _remove_sqlite_sidecars(options.output)

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

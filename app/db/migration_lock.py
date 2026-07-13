from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.db.sqlite_utils import sqlite_db_path_from_url

logger = logging.getLogger(__name__)

MIGRATION_LOCK_KEY = "codex_lb:migrations"
SQLITE_MIGRATION_LOCK_SUFFIX = ".migrate-lock"
_POLL_INTERVAL_SECONDS = 2.0
_WAIT_LOG_INTERVAL_SECONDS = 10.0
_TIMEOUT_SETTING_HINT = "database_migration_lock_timeout_seconds (CODEX_LB_DATABASE_MIGRATION_LOCK_TIMEOUT_SECONDS)"


def sqlite_migration_lock_path(db_path: Path) -> Path:
    return db_path.with_name(db_path.name + SQLITE_MIGRATION_LOCK_SUFFIX)


def _timeout_message(description: str, timeout_seconds: float) -> str:
    return (
        f"Timed out after {timeout_seconds:.1f}s waiting for the {description} "
        f"(key={MIGRATION_LOCK_KEY!r}). Another process is migrating the same database and has not "
        f"finished; if its migrations legitimately take longer, raise {_TIMEOUT_SETTING_HINT}."
    )


def _acquire_with_timeout(
    try_acquire: Callable[[], bool],
    *,
    timeout_seconds: float,
    description: str,
) -> None:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be greater than 0")

    started_at = time.monotonic()
    next_log_after = _WAIT_LOG_INTERVAL_SECONDS
    while True:
        if try_acquire():
            return
        elapsed = time.monotonic() - started_at
        if elapsed >= timeout_seconds:
            raise TimeoutError(_timeout_message(description, timeout_seconds))
        if elapsed >= next_log_after:
            logger.info(
                "Waiting for %s held by another process elapsed=%.1fs timeout=%.1fs",
                description,
                elapsed,
                timeout_seconds,
            )
            next_log_after += _WAIT_LOG_INTERVAL_SECONDS
        time.sleep(min(_POLL_INTERVAL_SECONDS, timeout_seconds - elapsed))


@contextmanager
def _postgresql_migration_lock(sync_database_url: str, *, timeout_seconds: float) -> Iterator[None]:
    # A dedicated session-level advisory lock connection: run_upgrade spans many
    # transactions across several short-lived engines, so the lock must outlive
    # any single transaction and is held on this connection for the whole
    # sequence. AUTOCOMMIT keeps the holder out of "idle in transaction".
    # PostgreSQL releases session advisory locks automatically if the holder
    # dies, so a crashed migrator never wedges its peers.
    engine = create_engine(sync_database_url, future=True)
    try:
        with engine.connect() as connection:
            connection = connection.execution_options(isolation_level="AUTOCOMMIT")

            def _try_acquire() -> bool:
                acquired = connection.execute(
                    text("SELECT pg_try_advisory_lock(hashtext(:key))"),
                    {"key": MIGRATION_LOCK_KEY},
                ).scalar()
                return bool(acquired)

            _acquire_with_timeout(
                _try_acquire,
                timeout_seconds=timeout_seconds,
                description="PostgreSQL advisory migration lock",
            )
            try:
                yield
            finally:
                connection.execute(
                    text("SELECT pg_advisory_unlock(hashtext(:key))"),
                    {"key": MIGRATION_LOCK_KEY},
                )
    finally:
        engine.dispose()


@contextmanager
def _sqlite_migration_lock(db_path: Path, *, timeout_seconds: float) -> Iterator[None]:
    # Holding a BEGIN IMMEDIATE write transaction on a sentinel SQLite file is
    # the mutex: SQLite's RESERVED lock is exclusive across processes sharing
    # the volume and vanishes on process death. The sentinel must be a separate
    # file — a write transaction on the main database would deadlock against
    # Alembic's own DDL connection and block concurrent app reads. It is never
    # unlinked (avoids unlink/reopen races) and stays behind as a harmless
    # zero-row SQLite file.
    lock_path = sqlite_migration_lock_path(db_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(lock_path), timeout=0, isolation_level=None)
    try:

        def _try_acquire() -> bool:
            try:
                connection.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "locked" in message or "busy" in message:
                    return False
                raise
            return True

        _acquire_with_timeout(
            _try_acquire,
            timeout_seconds=timeout_seconds,
            description=f"SQLite sentinel migration lock ({lock_path})",
        )
        try:
            yield
        finally:
            connection.rollback()
    finally:
        connection.close()


@contextmanager
def migration_lock(sync_database_url: str, *, timeout_seconds: float) -> Iterator[None]:
    """Cross-process mutex serializing schema upgrades/stamps for one database.

    PostgreSQL: session-level advisory lock held on a dedicated connection.
    File-backed SQLite: exclusive write transaction on a sentinel SQLite file
    adjacent to the database. In-memory SQLite is a no-op (the database is
    process-private, there is no peer), as are dialects without a portable
    cross-process primitive.
    """
    backend = make_url(sync_database_url).get_backend_name()
    if backend == "postgresql":
        with _postgresql_migration_lock(sync_database_url, timeout_seconds=timeout_seconds):
            yield
        return
    if backend == "sqlite":
        db_path = sqlite_db_path_from_url(sync_database_url)
        if db_path is None:
            yield
            return
        with _sqlite_migration_lock(db_path, timeout_seconds=timeout_seconds):
            yield
        return
    yield

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator, Awaitable, Callable, Protocol, TypeVar

import anyio
from anyio import to_thread
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config.settings import get_settings
from app.db.sqlite_utils import check_sqlite_integrity, sqlite_db_path_from_url

if TYPE_CHECKING:
    from app.db.migrate import MigrationRunResult, MigrationState

_settings = get_settings()

logger = logging.getLogger(__name__)

_SQLITE_BUSY_TIMEOUT_MS = 5_000
_SQLITE_BUSY_TIMEOUT_SECONDS = _SQLITE_BUSY_TIMEOUT_MS / 1000


def _is_sqlite_url(url: str) -> bool:
    return url.startswith("sqlite+aiosqlite:///") or url.startswith("sqlite:///")


def _is_sqlite_memory_url(url: str) -> bool:
    return _is_sqlite_url(url) and ":memory:" in url


def _configure_sqlite_engine(engine: Engine, *, enable_wal: bool) -> None:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection: sqlite3.Connection, _: object) -> None:
        cursor: sqlite3.Cursor = dbapi_connection.cursor()
        try:
            if enable_wal:
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
        finally:
            cursor.close()


if _is_sqlite_url(_settings.database_url):
    is_sqlite_memory = _is_sqlite_memory_url(_settings.database_url)
    if is_sqlite_memory:
        engine = create_async_engine(
            _settings.database_url,
            echo=False,
            connect_args={"timeout": _SQLITE_BUSY_TIMEOUT_SECONDS},
        )
    else:
        engine = create_async_engine(
            _settings.database_url,
            echo=False,
            pool_size=_settings.database_pool_size,
            max_overflow=_settings.database_max_overflow,
            pool_timeout=_settings.database_pool_timeout_seconds,
            connect_args={"timeout": _SQLITE_BUSY_TIMEOUT_SECONDS},
        )
    _configure_sqlite_engine(engine.sync_engine, enable_wal=not is_sqlite_memory)
else:
    engine = create_async_engine(
        _settings.database_url,
        echo=False,
        pool_size=_settings.database_pool_size,
        max_overflow=_settings.database_max_overflow,
        pool_timeout=_settings.database_pool_timeout_seconds,
    )

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

_T = TypeVar("_T")


class _SqliteBackupCreator(Protocol):
    def __call__(self, source: Path, *, max_files: int) -> Path: ...


def _ensure_sqlite_dir(url: str) -> None:
    if not (url.startswith("sqlite+aiosqlite:") or url.startswith("sqlite:")):
        return

    marker = ":///"
    marker_index = url.find(marker)
    if marker_index < 0:
        return

    # Works for both relative (sqlite+aiosqlite:///./db.sqlite) and absolute
    # paths (sqlite+aiosqlite:////var/lib/app/db.sqlite).
    path = url[marker_index + len(marker) :]
    path = path.partition("?")[0]
    path = path.partition("#")[0]

    if not path or path == ":memory:":
        return

    Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)


async def _shielded(awaitable: Awaitable[_T]) -> _T:
    with anyio.CancelScope(shield=True):
        return await awaitable


async def _safe_rollback(session: AsyncSession) -> None:
    if not session.in_transaction():
        return
    try:
        await _shielded(session.rollback())
    except BaseException:
        return


async def _safe_close(session: AsyncSession) -> None:
    try:
        await _shielded(session.close())
    except BaseException:
        return


def _load_migration_entrypoints() -> tuple[
    Callable[[str], "MigrationState"],
    Callable[[str], Awaitable["MigrationRunResult"]],
]:
    from app.db.migrate import inspect_migration_state, run_startup_migrations

    return inspect_migration_state, run_startup_migrations


def _load_sqlite_backup_creator() -> _SqliteBackupCreator:
    from app.db.backup import create_sqlite_pre_migration_backup

    return create_sqlite_pre_migration_backup


async def get_session() -> AsyncIterator[AsyncSession]:
    session = SessionLocal()
    try:
        yield session
    except BaseException:
        await _safe_rollback(session)
        raise
    finally:
        if session.in_transaction():
            await _safe_rollback(session)
        await _safe_close(session)


async def init_db() -> None:
    _ensure_sqlite_dir(_settings.database_url)
    sqlite_path = sqlite_db_path_from_url(_settings.database_url)
    if sqlite_path is not None:
        integrity = check_sqlite_integrity(sqlite_path)
        if not integrity.ok:
            details = integrity.details or "unknown error"
            logger.error("SQLite integrity check failed path=%s details=%s", sqlite_path, details)
            if "locked" in details.lower():
                message = (
                    f"SQLite integrity check failed for {sqlite_path} ({details}). "
                    "Another instance may be running. Stop it and retry."
                )
            else:
                message = (
                    f"SQLite integrity check failed for {sqlite_path} ({details}). "
                    "The database appears corrupted or the filesystem is unhealthy. "
                    "Stop the app and run "
                    f'`python -m app.db.recover --db "{sqlite_path}" --replace` '
                    "or restore a backup from the same directory."
                )
            raise RuntimeError(message)

    if not _settings.database_migrate_on_startup:
        logger.info("Startup database migration is disabled")
        return

    try:
        inspect_migration_state, run_startup_migrations = _load_migration_entrypoints()
    except ModuleNotFoundError as exc:
        if exc.name != "app.db.migrate":
            raise
        logger.exception("Failed to import migration entrypoint module=app.db.migrate")
        raise RuntimeError("Database migration entrypoint app.db.migrate is unavailable") from exc
    except ImportError as exc:
        logger.exception("Failed to import database migration entrypoints from app.db.migrate")
        raise RuntimeError("Database migration entrypoint app.db.migrate is invalid") from exc

    if sqlite_path is not None and _settings.database_sqlite_pre_migrate_backup_enabled and sqlite_path.exists():
        migration_state = await to_thread.run_sync(
            lambda: inspect_migration_state(_settings.database_url),
        )
        if migration_state.needs_upgrade:
            try:
                create_sqlite_pre_migration_backup = _load_sqlite_backup_creator()
            except ModuleNotFoundError as exc:
                if exc.name != "app.db.backup":
                    raise
                logger.exception("Failed to import SQLite backup module=app.db.backup")
                raise RuntimeError("SQLite backup module app.db.backup is unavailable") from exc

            backup_path = await to_thread.run_sync(
                lambda: create_sqlite_pre_migration_backup(
                    sqlite_path,
                    max_files=_settings.database_sqlite_pre_migrate_backup_max_files,
                ),
            )
            logger.info(
                "Created SQLite pre-migration backup path=%s target_revision=%s",
                backup_path,
                migration_state.head_revision,
            )

    try:
        result = await run_startup_migrations(_settings.database_url)
        if result.bootstrap.stamped_revision is not None:
            logger.info(
                "Bootstrapped legacy migrations stamped_revision=%s legacy_rows=%s",
                result.bootstrap.stamped_revision,
                result.bootstrap.legacy_row_count,
            )
        if result.current_revision is not None:
            logger.info("Database migration complete revision=%s", result.current_revision)
    except Exception:
        logger.exception("Failed to apply database migrations")
        if _settings.database_migrations_fail_fast:
            raise


async def close_db() -> None:
    await engine.dispose()

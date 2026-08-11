from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, AsyncIterator, Awaitable, Callable, Protocol, TypeVar

import anyio
from anyio import to_thread
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config.settings import get_settings
from app.db.sqlite_utils import (
    SqliteIntegrityCheckMode,
    check_sqlite_integrity,
    normalize_sqlite_url,
    sqlite_db_path_from_url,
)

if TYPE_CHECKING:
    from app.db.migrate import MigrationRunResult, MigrationState

_settings = get_settings()

logger = logging.getLogger(__name__)

_SQLITE_BUSY_TIMEOUT_MS = 30_000
_SQLITE_BUSY_TIMEOUT_SECONDS = _SQLITE_BUSY_TIMEOUT_MS / 1000

# PostgreSQL pool checkout timeout and connection recycle window. Fixed
# application constants (issue #1340): recycle keeps pooled connections
# younger than any reasonable server/proxy keep-alive boundary, and neither
# value is an operator decision. Pool sizing stays configurable via
# ``database_pool_size`` / ``database_max_overflow``.
_POSTGRES_POOL_TIMEOUT_SECONDS = 30.0
_POSTGRES_POOL_RECYCLE_SECONDS = 1800
_database_url = normalize_sqlite_url(_settings.database_url)


class _PostgresPooledEngineRole(StrEnum):
    REQUEST_PATH = "request_path"
    BACKGROUND_TASK = "background_task"


# The owned launcher pins one worker per replica. Derive its two-engine
# PostgreSQL budget from the roles that the real creation paths must declare.
_POSTGRES_POOLED_ENGINE_ROLES = tuple(_PostgresPooledEngineRole)
_POSTGRES_POOLED_ENGINES_PER_WORKER = len(_POSTGRES_POOLED_ENGINE_ROLES)


def _is_sqlite_url(url: str) -> bool:
    return url.startswith("sqlite+aiosqlite:///") or url.startswith("sqlite:///")


def _is_sqlite_memory_url(url: str) -> bool:
    return _is_sqlite_url(url) and ":memory:" in url


def _postgres_async_connect_args(url: str) -> dict[str, object] | None:
    if not url.startswith("postgresql+asyncpg://"):
        return None
    # Pin the asyncpg session time zone to UTC. The application persists naive
    # UTC datetimes (see app.core.utils.time.utcnow) into timestamptz columns.
    # asyncpg binds a naive datetime using the connection's session time zone,
    # so if that time zone follows the container's TZ (e.g. Europe/Amsterdam)
    # PostgreSQL shifts every written timestamp away from real UTC. That skew is
    # silent but corrupts every wall-clock comparison the coordinator relies on:
    # ring-membership heartbeats look perpetually stale, leader election and the
    # bridge-session cleanup stop running, and account/stream lease expiry is
    # mis-evaluated. Forcing UTC keeps stored timestamps correct regardless of
    # the container time zone.
    connect_args: dict[str, object] = {"server_settings": {"timezone": "UTC"}}
    if os.environ.get("CODEX_LB_TEST_DATABASE_URL"):
        connect_args["prepared_statement_cache_size"] = 0
    return connect_args


def _postgres_async_engine_kwargs(url: str) -> dict[str, object]:
    """Engine kwargs shared by the main and background PostgreSQL engines.

    The background engine always derives its pool sizing from the main pool
    settings; it exists to isolate background-task checkouts, not to be sized
    independently.
    """
    connect_args = _postgres_async_connect_args(url)
    kwargs: dict[str, object] = {"connect_args": connect_args or {}}
    if os.environ.get("CODEX_LB_TEST_DATABASE_URL") and url.startswith("postgresql+asyncpg://"):
        kwargs["poolclass"] = NullPool
    else:
        kwargs["pool_size"] = _settings.database_pool_size
        kwargs["max_overflow"] = _settings.database_max_overflow
        kwargs["pool_timeout"] = _POSTGRES_POOL_TIMEOUT_SECONDS
        # Detect server-side connection drops (idle timeout, restart, network reset)
        # before the first real query, and cycle long-lived connections so they
        # never reach an upstream keep-alive boundary. SQLite paths do not need
        # either knob — aiosqlite has no analogous server-side disconnect.
        kwargs["pool_pre_ping"] = True
        kwargs["pool_recycle"] = _POSTGRES_POOL_RECYCLE_SECONDS
    return kwargs


def _create_postgres_async_engine(url: str, *, role: _PostgresPooledEngineRole) -> AsyncEngine:
    if not isinstance(role, _PostgresPooledEngineRole):
        raise TypeError(f"PostgreSQL engine role must be declared in {_PostgresPooledEngineRole.__name__}")
    return create_async_engine(
        url,
        echo=False,
        **_postgres_async_engine_kwargs(url),
    )


def _sqlite_file_async_engine_kwargs() -> dict[str, object]:
    return {
        "poolclass": NullPool,
        "connect_args": {"timeout": _SQLITE_BUSY_TIMEOUT_SECONDS},
    }


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


def _create_main_engine(url: str) -> AsyncEngine:
    if not _is_sqlite_url(url):
        return _create_postgres_async_engine(
            url,
            role=_PostgresPooledEngineRole.REQUEST_PATH,
        )

    is_sqlite_memory = _is_sqlite_memory_url(url)
    if is_sqlite_memory:
        main_engine = create_async_engine(
            url,
            echo=False,
            connect_args={"timeout": _SQLITE_BUSY_TIMEOUT_SECONDS},
        )
    else:
        main_engine = create_async_engine(
            url,
            echo=False,
            **_sqlite_file_async_engine_kwargs(),
        )
    _configure_sqlite_engine(main_engine.sync_engine, enable_wal=not is_sqlite_memory)
    return main_engine


engine = _create_main_engine(_database_url)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

_background_engine: AsyncEngine | None = None
_background_session_factory: async_sessionmaker[AsyncSession] | None = None
_sqlite_writer_lock: anyio.Lock | None = None

_T = TypeVar("_T")


class _SqliteBackupCreator(Protocol):
    def __call__(self, source: Path, *, max_files: int) -> Path: ...


def _ensure_sqlite_dir(url: str) -> None:
    sqlite_path = sqlite_db_path_from_url(url)
    if sqlite_path is None:
        return

    sqlite_path.parent.mkdir(parents=True, exist_ok=True)


def _startup_sqlite_check_mode(raw_mode: str) -> SqliteIntegrityCheckMode | None:
    if raw_mode == "off":
        return None
    return SqliteIntegrityCheckMode(raw_mode)


async def _shielded(awaitable: Awaitable[object]) -> None:
    task = asyncio.ensure_future(awaitable)
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


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


async def close_session(session: AsyncSession) -> None:
    if session.in_transaction():
        await _safe_rollback(session)
    await _safe_close(session)


def detach_session_objects(session: AsyncSession) -> None:
    """Detach loaded ORM rows that must outlive this session boundary."""
    session.expunge_all()


def _load_migration_entrypoints() -> tuple[
    Callable[[str], "MigrationState"],
    Callable[[str], Awaitable["MigrationRunResult"]],
    Callable[[str], tuple[str, ...]],
]:
    from app.db.migrate import check_schema_drift, inspect_migration_state, run_startup_migrations

    return inspect_migration_state, run_startup_migrations, check_schema_drift


def _load_sqlite_backup_creator() -> _SqliteBackupCreator:
    from app.db.backup import create_sqlite_pre_migration_backup

    return create_sqlite_pre_migration_backup


def init_background_db(url: str | None = None) -> None:
    """Initialize a separate DB engine for background tasks.

    The background engine isolates background-task checkouts from the request
    pool; its pool sizing always derives from ``database_pool_size`` /
    ``database_max_overflow``.

    Args:
        url: Database URL. If None, uses settings.database_url.
    """
    global _background_engine, _background_session_factory
    db_url = normalize_sqlite_url(url or _settings.database_url)

    if _is_sqlite_url(db_url):
        is_sqlite_memory = _is_sqlite_memory_url(db_url)
        if is_sqlite_memory:
            # Reuse the main engine for in-memory SQLite — creating a second
            # engine would open a separate, empty in-memory database with no
            # schema, causing "no such table" errors in background tasks.
            _background_engine = engine
            _background_session_factory = SessionLocal
            return
        _background_engine = create_async_engine(
            db_url,
            echo=False,
            **_sqlite_file_async_engine_kwargs(),
        )
        _configure_sqlite_engine(_background_engine.sync_engine, enable_wal=not is_sqlite_memory)
    else:
        _background_engine = _create_postgres_async_engine(
            db_url,
            role=_PostgresPooledEngineRole.BACKGROUND_TASK,
        )

    _background_session_factory = async_sessionmaker(_background_engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def get_background_session() -> AsyncIterator[AsyncSession]:
    """Session provider for background tasks, schedulers, and auth dependencies.

    Uses the separate background pool if initialized, otherwise falls back to main pool.
    """
    factory = _background_session_factory or SessionLocal
    session = factory()
    try:
        yield session
    except BaseException:
        await _safe_rollback(session)
        raise
    finally:
        await close_session(session)


async def relax_commit_durability(session: AsyncSession) -> None:
    """Relax commit durability for the current telemetry write transaction.

    High-frequency append-only telemetry writes (request-log inserts, usage
    history appends) dominate the slow-query profile on PostgreSQL because
    every commit waits for a WAL fsync. Those rows are pure observability:
    losing the final unflushed WAL window (bounded by three times
    ``wal_writer_delay`` — up to ~600 ms at the default 200 ms setting) keeps
    accounting semantics identical. For such transactions this helper emits
    ``SET LOCAL synchronous_commit = off`` so the commit returns without
    waiting for the WAL flush.

    ``SET LOCAL`` is transaction-scoped: PostgreSQL reverts it automatically
    at COMMIT/ROLLBACK, so nothing leaks onto the pooled connection. Executed
    outside a transaction, PostgreSQL merely emits a WARNING and the setting
    never applies — calling this through an ``AsyncSession`` is what makes it
    safe, because session autobegin opens the write transaction at this very
    statement when none is open yet.

    No-op on SQLite (durability there is governed by ``PRAGMA synchronous``).
    MUST NOT be used for configuration writes (accounts, API keys, settings,
    limits management) or for API-key usage-reservation accounting (creation,
    settlement, stale release): on external/HA PostgreSQL a server failover
    does not kill in-flight application requests, so an acked-but-lost
    reservation commit would desynchronize the accounting ledger from
    requests that still complete. Those paths keep full durability.
    """
    bind = session.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return
    await session.execute(text("SET LOCAL synchronous_commit = off"))


@asynccontextmanager
async def sqlite_writer_section() -> AsyncIterator[None]:
    """Serialize local SQLite write transactions without throttling upstream work."""
    global _sqlite_writer_lock
    database_url = normalize_sqlite_url(_settings.database_url)
    if not _is_sqlite_url(database_url) or _is_sqlite_memory_url(database_url):
        yield
        return
    if _sqlite_writer_lock is None:
        _sqlite_writer_lock = anyio.Lock()
    async with _sqlite_writer_lock:
        yield


async def get_session() -> AsyncIterator[AsyncSession]:
    session = SessionLocal()
    try:
        yield session
    except BaseException:
        await _safe_rollback(session)
        raise
    finally:
        await close_session(session)


async def init_db() -> None:
    database_url = normalize_sqlite_url(_settings.database_url)
    _ensure_sqlite_dir(database_url)
    sqlite_path = sqlite_db_path_from_url(database_url)
    if sqlite_path is not None:
        check_mode = _startup_sqlite_check_mode(_settings.database_sqlite_startup_check_mode)
        if check_mode is not None:
            integrity = check_sqlite_integrity(sqlite_path, mode=check_mode)
            if not integrity.ok:
                details = integrity.details or "unknown error"
                pragma_name = "quick_check" if check_mode == SqliteIntegrityCheckMode.QUICK else "integrity_check"
                logger.error(
                    "SQLite %s failed path=%s details=%s",
                    pragma_name,
                    sqlite_path,
                    details,
                )
                if "locked" in details.lower():
                    message = (
                        f"SQLite {pragma_name} failed for {sqlite_path} ({details}). "
                        "Another instance may be running. Stop it and retry."
                    )
                else:
                    message = (
                        f"SQLite {pragma_name} failed for {sqlite_path} ({details}). "
                        "The database appears corrupted or the filesystem is unhealthy. "
                        "Stop the app and run "
                        f'`python -m app.db.recover --db "{sqlite_path}" --replace` '
                        "or restore a backup from the same directory."
                    )
                raise RuntimeError(message)

    try:
        inspect_migration_state, run_startup_migrations, check_schema_drift = _load_migration_entrypoints()
    except ModuleNotFoundError as exc:
        if exc.name != "app.db.migrate":
            raise
        logger.exception("Failed to import migration entrypoint module=app.db.migrate")
        raise RuntimeError("Database migration entrypoint app.db.migrate is unavailable") from exc
    except ImportError as exc:
        logger.exception("Failed to import database migration entrypoints from app.db.migrate")
        raise RuntimeError("Database migration entrypoint app.db.migrate is invalid") from exc

    if not _settings.database_migrate_on_startup:
        migration_state = await to_thread.run_sync(
            lambda: inspect_migration_state(database_url),
        )
        if migration_state.needs_upgrade:
            current_revision = migration_state.current_revision or "none"
            if migration_state.is_ahead:
                unknown_revisions = ",".join(migration_state.unknown_revisions)
                message = (
                    "Startup database migration is disabled and database schema revision(s) "
                    f"{unknown_revisions} are not known to this build (head={migration_state.head_revision}). "
                    "The schema was likely migrated by a newer version; deploy a matching or newer image, "
                    "or run an Alembic downgrade to a revision this build knows."
                )
            else:
                message = (
                    "Startup database migration is disabled but database schema is behind Alembic head "
                    f"(current={current_revision}, head={migration_state.head_revision}). "
                    "Run the dedicated migration job or `python -m app.db.migrate upgrade` before starting the app."
                )
            logger.error(message)
            raise RuntimeError(message)

        logger.info("Startup database migration is disabled and database schema is current")
        return

    if sqlite_path is not None and _settings.database_sqlite_pre_migrate_backup_enabled and sqlite_path.exists():
        migration_state = await to_thread.run_sync(
            lambda: inspect_migration_state(database_url),
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
        result = await run_startup_migrations(database_url)
        if result.bootstrap.stamped_revision is not None:
            logger.info(
                "Bootstrapped legacy migrations stamped_revision=%s legacy_rows=%s",
                result.bootstrap.stamped_revision,
                result.bootstrap.legacy_row_count,
            )
        if result.current_revision is not None:
            logger.info("Database migration complete revision=%s", result.current_revision)
        drift = await to_thread.run_sync(lambda: check_schema_drift(database_url))
        if drift:
            drift_details = "; ".join(drift)
            raise RuntimeError(f"Schema drift detected after startup migrations: {drift_details}")
    except Exception:
        logger.exception("Failed to apply database migrations")
        if _settings.database_migrations_fail_fast:
            raise


async def close_db() -> None:
    await engine.dispose()
    if _background_engine is not None:
        await _background_engine.dispose()

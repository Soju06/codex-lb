from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
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
# A write transaction holding SQLite's single writer slot past the busy
# timeout is exactly the holder that makes every other writer surface
# "database is locked" (issue #1682); the watchdog below reports it with the
# statements it ran, since the stall is nondeterministic and self-recovers.
_SQLITE_LONG_WRITE_TRANSACTION_WARN_SECONDS = _SQLITE_BUSY_TIMEOUT_SECONDS
_SQLITE_WRITE_STATEMENT_PREFIXES = (
    "insert",
    "update",
    "delete",
    "replace",
    "create",
    "drop",
    "alter",
    "vacuum",
    # BEGIN IMMEDIATE/EXCLUSIVE acquire the writer slot with no DML at all
    # (e.g. AccountsRepository._acquire_sqlite_merge_lock); a plain deferred
    # BEGIN does not and stays untracked.
    "begin immediate",
    "begin exclusive",
)
_SQLITE_WATCHDOG_STATEMENT_PREVIEW_CHARS = 300

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

    _install_sqlite_long_write_watchdog(engine)


def _current_task_name_best_effort() -> str:
    # Sync engine events run inside the greenlet driving the async engine on
    # the event loop thread, so the owning task is normally visible; never let
    # diagnostics raise into the query path.
    try:
        task = asyncio.current_task()
    except RuntimeError:
        return "<no-loop>"
    if task is None:
        return "<no-task>"
    return task.get_name()


def _install_sqlite_long_write_watchdog(engine: Engine) -> None:
    """Report write transactions that outlive the SQLite busy timeout.

    In WAL mode a transaction takes the single writer slot at its first write
    statement, not at BEGIN, so the window is measured from the first write to
    commit/rollback. The report fires when the holder finally ends — the stall
    in issue #1682 self-recovers, so identifying the holder post-hoc is the
    point; a live sampler is not needed to attribute it.
    """

    @event.listens_for(engine, "after_cursor_execute")
    def _track_write_statements(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        # after_cursor_execute, not before: the first write statement may wait
        # up to busy_timeout for the writer slot before failing, and timing
        # from before the execute would report that victim as the holder. The
        # slot is only held once a write statement has SUCCEEDED, so the clock
        # starts there.
        stripped = statement.lstrip().lower()
        if not stripped.startswith(_SQLITE_WRITE_STATEMENT_PREFIXES):
            return
        info = getattr(conn, "info", None)
        if info is None:
            return
        preview = statement[:_SQLITE_WATCHDOG_STATEMENT_PREVIEW_CHARS]
        if "sqlite_write_started_at" not in info:
            info["sqlite_write_started_at"] = time.monotonic()
            info["sqlite_first_write_statement"] = preview
            info["sqlite_write_task"] = _current_task_name_best_effort()
        info["sqlite_last_write_statement"] = preview

    def _finalize_pending_report(info: dict[str, object]) -> None:
        pending = info.pop("sqlite_write_pending_report", None)
        if not isinstance(pending, tuple):
            return
        started_at, outcome, first_statement, last_statement, task_name = pending
        held_seconds = time.monotonic() - float(started_at)
        if held_seconds < _SQLITE_LONG_WRITE_TRANSACTION_WARN_SECONDS:
            return
        logger.warning(
            "sqlite_long_write_transaction held_seconds=%.1f outcome=%s task=%s first_statement=%r "
            "last_statement=%r — this writer starved every other writer past busy_timeout (issue #1682)",
            held_seconds,
            outcome,
            task_name,
            first_statement,
            last_statement,
        )

    def _mark_transaction_ending(conn: object, *, outcome: str) -> None:
        # ConnectionEvents.commit/rollback fire BEFORE the DBAPI call, and a
        # wedged rollback is exactly the holder this watchdog hunts — reporting
        # here would exclude the wedge itself from the measured hold. Stash the
        # report and finalize it at the first proof the DBAPI transaction ended:
        # the connection's next transaction beginning, or the connection going
        # back to the pool.
        info = getattr(conn, "info", None)
        if info is None:
            return
        started_at = info.pop("sqlite_write_started_at", None)
        first_statement = info.pop("sqlite_first_write_statement", None)
        last_statement = info.pop("sqlite_last_write_statement", None)
        task_name = info.pop("sqlite_write_task", None)
        if started_at is None:
            # A rollback after a commit whose DBAPI call raised: the pending
            # report already holds outcome=commit, but the transaction is in
            # fact ending by rollback — rewrite the outcome so the report does
            # not claim a durable commit that never happened.
            pending = info.get("sqlite_write_pending_report")
            if outcome == "rollback" and isinstance(pending, tuple) and pending[1] == "commit":
                info["sqlite_write_pending_report"] = (pending[0], "commit_failed_rollback", *pending[2:])
            return
        info["sqlite_write_pending_report"] = (started_at, outcome, first_statement, last_statement, task_name)

    @event.listens_for(engine, "commit")
    def _mark_on_commit(conn: object) -> None:
        _mark_transaction_ending(conn, outcome="commit")

    @event.listens_for(engine, "rollback")
    def _mark_on_rollback(conn: object) -> None:
        _mark_transaction_ending(conn, outcome="rollback")

    @event.listens_for(engine, "begin")
    def _finalize_on_next_begin(conn: object) -> None:
        info = getattr(conn, "info", None)
        if info is not None:
            _finalize_pending_report(info)

    @event.listens_for(engine, "checkin")
    def _finalize_on_checkin(dbapi_connection: object, connection_record: object) -> None:
        info = getattr(connection_record, "info", None)
        if info is not None:
            _finalize_pending_report(info)

    @event.listens_for(engine, "handle_error")
    def _flip_outcome_on_failed_end(exception_context: object) -> None:
        # A DBAPI commit that raises still ends the transaction, but by
        # rollback; the pending report marked at the commit event must not
        # claim a durable commit that never happened.
        connection = getattr(exception_context, "connection", None)
        info = getattr(connection, "info", None) if connection is not None else None
        if info is None:
            return
        pending = info.get("sqlite_write_pending_report")
        if isinstance(pending, tuple) and pending[1] == "commit":
            info["sqlite_write_pending_report"] = (pending[0], "commit_failed_rollback", *pending[2:])


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
    cancellation: asyncio.CancelledError | None = None
    with anyio.CancelScope(shield=True):
        while True:
            try:
                await asyncio.shield(task)
                break
            except asyncio.CancelledError as exc:
                if task.cancelled():
                    raise
                cancellation = cancellation or exc
    if cancellation is not None:
        raise cancellation


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
    async def _close() -> None:
        if session.in_transaction():
            await _safe_rollback(session)
        await _safe_close(session)

    await _shielded(_close())


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

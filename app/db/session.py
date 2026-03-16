from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator, Awaitable, Callable, TypeVar

import anyio
from anyio import to_thread
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config.settings import get_settings

if TYPE_CHECKING:
    from app.db.migrate import MigrationRunResult

_settings = get_settings()

logger = logging.getLogger(__name__)


def _runtime_database_url(database_url: str) -> str:
    parsed = make_url(database_url)
    if parsed.drivername != "postgresql+asyncpg":
        return database_url

    query = dict(parsed.query)
    sslmode = query.pop("sslmode", None)
    if sslmode is not None and "ssl" not in query:
        query["ssl"] = sslmode
    query.pop("channel_binding", None)
    normalized = parsed.set(query=query)
    return normalized.render_as_string(hide_password=False)


engine = create_async_engine(
    _runtime_database_url(_settings.database_url),
    echo=False,
    pool_size=_settings.database_pool_size,
    max_overflow=_settings.database_max_overflow,
    pool_timeout=_settings.database_pool_timeout_seconds,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

_T = TypeVar("_T")


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
    Callable[[str], Awaitable["MigrationRunResult"]],
    Callable[[str], tuple[str, ...]],
]:
    from app.db.migrate import check_schema_drift, run_startup_migrations

    return run_startup_migrations, check_schema_drift


@asynccontextmanager
async def get_background_session() -> AsyncIterator[AsyncSession]:
    """Session provider for background tasks, schedulers, and auth dependencies."""
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
    if not _settings.database_migrate_on_startup:
        logger.info("Startup database migration is disabled")
        return

    migration_url = _settings.database_migration_url
    if not migration_url:
        raise RuntimeError(
            "CODEX_LB_DATABASE_MIGRATION_URL is required when database migrations on startup are enabled"
        )

    try:
        run_startup_migrations, check_schema_drift = _load_migration_entrypoints()
    except ModuleNotFoundError as exc:
        if exc.name != "app.db.migrate":
            raise
        logger.exception("Failed to import migration entrypoint module=app.db.migrate")
        raise RuntimeError("Database migration entrypoint app.db.migrate is unavailable") from exc
    except ImportError as exc:
        logger.exception("Failed to import database migration entrypoints from app.db.migrate")
        raise RuntimeError("Database migration entrypoint app.db.migrate is invalid") from exc

    try:
        result = await run_startup_migrations(migration_url)
        if result.bootstrap.stamped_revision is not None:
            logger.info(
                "Bootstrapped legacy migrations stamped_revision=%s legacy_rows=%s",
                result.bootstrap.stamped_revision,
                result.bootstrap.legacy_row_count,
            )
        if result.current_revision is not None:
            logger.info("Database migration complete revision=%s", result.current_revision)
        drift = await to_thread.run_sync(lambda: check_schema_drift(migration_url))
        if drift:
            drift_details = "; ".join(drift)
            raise RuntimeError(f"Schema drift detected after startup migrations: {drift_details}")
    except Exception:
        logger.exception("Failed to apply database migrations")
        if _settings.database_migrations_fail_fast:
            raise


async def close_db() -> None:
    await engine.dispose()

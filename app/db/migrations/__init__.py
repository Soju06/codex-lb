from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.migrations.versions import (
    add_accounts_chatgpt_account_id,
    add_accounts_reset_at,
    add_dashboard_settings,
    add_dashboard_settings_totp,
    add_request_logs_reasoning_effort,
    normalize_account_plan_types,
)

_CREATE_MIGRATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    name TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
)
"""

_INSERT_MIGRATION = """
INSERT INTO schema_migrations (name, applied_at)
VALUES (:name, :applied_at)
ON CONFLICT(name) DO NOTHING
RETURNING name
"""


@dataclass(frozen=True)
class Migration:
    name: str
    run: Callable[[AsyncSession], Awaitable[None]]


MIGRATIONS: Final[tuple[Migration, ...]] = (
    Migration("001_normalize_account_plan_types", normalize_account_plan_types.run),
    Migration("002_add_request_logs_reasoning_effort", add_request_logs_reasoning_effort.run),
    Migration("003_add_accounts_reset_at", add_accounts_reset_at.run),
    Migration("004_add_accounts_chatgpt_account_id", add_accounts_chatgpt_account_id.run),
    Migration("005_add_dashboard_settings", add_dashboard_settings.run),
    Migration("006_add_dashboard_settings_totp", add_dashboard_settings_totp.run),
)


async def run_migrations(session: AsyncSession) -> int:
    await _ensure_schema_migrations(session)
    applied_count = 0
    for migration in MIGRATIONS:
        applied_now = await _apply_migration(session, migration)
        if applied_now:
            applied_count += 1
    return applied_count


async def _apply_migration(session: AsyncSession, migration: Migration) -> bool:
    async with _migration_transaction(session):
        result = await session.execute(
            text(_INSERT_MIGRATION),
            {
                "name": migration.name,
                "applied_at": _utcnow_iso(),
            },
        )
        inserted = result.scalar_one_or_none()
        if inserted is None:
            return False
        await migration.run(session)
    return True


async def _ensure_schema_migrations(session: AsyncSession) -> None:
    async with _migration_transaction(session):
        await session.execute(text(_CREATE_MIGRATIONS_TABLE))


@asynccontextmanager
async def _migration_transaction(session: AsyncSession):
    if session.in_transaction():
        async with session.begin_nested():
            yield
    else:
        async with session.begin():
            yield


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

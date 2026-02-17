from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def run(session: AsyncSession) -> None:
    bind = session.get_bind()
    dialect = getattr(getattr(bind, "dialect", None), "name", None)
    if dialect == "sqlite":
        await _sqlite_add_column_if_missing(
            session,
            "dashboard_settings",
            "import_without_overwrite",
            "BOOLEAN NOT NULL DEFAULT 0",
        )
    elif dialect == "postgresql":
        await session.execute(
            text(
                "ALTER TABLE dashboard_settings "
                "ADD COLUMN IF NOT EXISTS import_without_overwrite BOOLEAN NOT NULL DEFAULT FALSE"
            ),
        )


async def _sqlite_add_column_if_missing(
    session: AsyncSession,
    table: str,
    column: str,
    column_type: str,
) -> None:
    result = await session.execute(text(f"PRAGMA table_info({table})"))
    rows = result.fetchall()
    existing = {row[1] for row in rows if len(row) > 1}
    if column in existing:
        return
    await session.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}"))

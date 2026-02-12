from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session


def _dashboard_settings_columns(session: Session) -> set[str]:
    inspector = inspect(session.connection())
    if not inspector.has_table("dashboard_settings"):
        return set()
    return {column["name"] for column in inspector.get_columns("dashboard_settings")}


async def run(session: AsyncSession) -> None:
    columns = await session.run_sync(_dashboard_settings_columns)
    if not columns:
        return

    if "password_hash" not in columns:
        await session.execute(text("ALTER TABLE dashboard_settings ADD COLUMN password_hash TEXT"))

    if "api_key_auth_enabled" not in columns:
        await session.execute(
            text("ALTER TABLE dashboard_settings ADD COLUMN api_key_auth_enabled BOOLEAN NOT NULL DEFAULT FALSE")
        )

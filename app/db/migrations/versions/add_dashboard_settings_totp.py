from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session


def _dashboard_settings_columns(session: Session) -> tuple[set[str], str]:
    connection = session.connection()
    inspector = inspect(connection)
    if not inspector.has_table("dashboard_settings"):
        return set(), connection.dialect.name
    columns = {column["name"] for column in inspector.get_columns("dashboard_settings")}
    return columns, connection.dialect.name


async def run(session: AsyncSession) -> None:
    columns, dialect = await session.run_sync(_dashboard_settings_columns)
    if not columns:
        return

    if "totp_required_on_login" not in columns:
        await session.execute(
            text("ALTER TABLE dashboard_settings ADD COLUMN totp_required_on_login BOOLEAN NOT NULL DEFAULT FALSE")
        )

    if "totp_secret_encrypted" not in columns:
        secret_type = "BLOB" if dialect == "sqlite" else "BYTEA"
        await session.execute(text(f"ALTER TABLE dashboard_settings ADD COLUMN totp_secret_encrypted {secret_type}"))

    if "totp_last_verified_step" not in columns:
        await session.execute(text("ALTER TABLE dashboard_settings ADD COLUMN totp_last_verified_step INTEGER"))

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session


def _settings_table_exists(session: Session) -> bool:
    inspector = inspect(session.connection())
    return inspector.has_table("dashboard_settings")


def _settings_columns(session: Session) -> set[str]:
    inspector = inspect(session.connection())
    if not inspector.has_table("dashboard_settings"):
        return set()
    return {column["name"] for column in inspector.get_columns("dashboard_settings")}


async def run(session: AsyncSession) -> None:
    exists = await session.run_sync(_settings_table_exists)
    if not exists:
        return

    # Avoid ORM access here so this migration can run even if newer columns
    # have been added to the model but not yet migrated into the DB.
    existing = await session.execute(text("SELECT 1 FROM dashboard_settings WHERE id = :id"), {"id": 1})
    if existing.scalar_one_or_none() is not None:
        return

    columns = await session.run_sync(_settings_columns)
    insert_columns = ["id", "sticky_threads_enabled", "prefer_earlier_reset_accounts"]
    params: dict[str, int | bool] = {
        "id": 1,
        "sticky_threads_enabled": False,
        "prefer_earlier_reset_accounts": False,
    }
    if "totp_required_on_login" in columns:
        insert_columns.append("totp_required_on_login")
        params["totp_required_on_login"] = False

    column_list = ", ".join(insert_columns)
    values_list = ", ".join(f":{name}" for name in insert_columns)
    await session.execute(
        text(f"INSERT INTO dashboard_settings ({column_list}) VALUES ({values_list})"),
        params,
    )

from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session


def _usage_history_column_state(session: Session) -> tuple[bool, bool]:
    inspector = inspect(session.connection())
    if not inspector.has_table("usage_history"):
        return False, False
    columns = {column["name"] for column in inspector.get_columns("usage_history")}
    return True, "window_label" in columns


async def run(session: AsyncSession) -> None:
    has_table, has_column = await session.run_sync(_usage_history_column_state)
    if not has_table or has_column:
        return
    await session.execute(text("ALTER TABLE usage_history ADD COLUMN window_label VARCHAR"))

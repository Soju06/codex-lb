from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session


def _request_logs_column_state(session: Session) -> tuple[bool, bool]:
    conn = session.connection()
    inspector = inspect(conn)
    if not inspector.has_table("request_logs"):
        return False, False
    columns = {column["name"] for column in inspector.get_columns("request_logs")}
    return True, "reasoning_effort" in columns


async def run(session: AsyncSession) -> None:
    has_table, has_column = await session.run_sync(_request_logs_column_state)
    if not has_table or has_column:
        return
    await session.execute(text("ALTER TABLE request_logs ADD COLUMN reasoning_effort VARCHAR"))

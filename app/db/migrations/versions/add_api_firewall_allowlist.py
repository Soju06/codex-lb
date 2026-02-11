from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session


def _has_firewall_table(session: Session) -> bool:
    inspector = inspect(session.connection())
    return inspector.has_table("api_firewall_allowlist")


async def run(session: AsyncSession) -> None:
    has_table = await session.run_sync(_has_firewall_table)
    if has_table:
        return
    await session.execute(
        text(
            """
            CREATE TABLE api_firewall_allowlist (
                ip_address VARCHAR PRIMARY KEY,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
    )

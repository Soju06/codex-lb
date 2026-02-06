from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.db.models import DashboardSettings


def _settings_table_exists(session: Session) -> bool:
    inspector = inspect(session.connection())
    return inspector.has_table("dashboard_settings")


async def run(session: AsyncSession) -> None:
    exists = await session.run_sync(_settings_table_exists)
    if not exists:
        return

    row = await session.get(DashboardSettings, 1)
    if row is not None:
        return

    session.add(
        DashboardSettings(
            id=1,
            sticky_threads_enabled=False,
            prefer_earlier_reset_accounts=False,
            totp_required_on_login=False,
            totp_secret_encrypted=None,
            totp_last_verified_step=None,
        )
    )
    await session.flush()

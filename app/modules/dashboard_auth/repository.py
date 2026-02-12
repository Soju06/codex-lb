from __future__ import annotations

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DashboardSettings
from app.modules.settings.repository import SettingsRepository

_SETTINGS_ID = 1


class DashboardAuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._settings_repository = SettingsRepository(session)

    async def get_settings(self) -> DashboardSettings:
        return await self._settings_repository.get_or_create()

    async def set_totp_secret(self, secret_encrypted: bytes | None) -> DashboardSettings:
        row = await self._settings_repository.get_or_create()
        row.totp_secret_encrypted = secret_encrypted
        row.totp_last_verified_step = None
        if secret_encrypted is None:
            row.totp_required_on_login = False
        await self._settings_repository.commit_refresh(row)
        return row

    async def set_password_hash(self, password_hash: str) -> DashboardSettings:
        row = await self._settings_repository.get_or_create()
        row.password_hash = password_hash
        await self._settings_repository.commit_refresh(row)
        return row

    async def get_password_hash(self) -> str | None:
        row = await self._settings_repository.get_or_create()
        return row.password_hash

    async def clear_password_and_totp(self) -> DashboardSettings:
        row = await self._settings_repository.get_or_create()
        row.password_hash = None
        row.totp_required_on_login = False
        row.totp_secret_encrypted = None
        row.totp_last_verified_step = None
        await self._settings_repository.commit_refresh(row)
        return row

    async def try_advance_totp_last_verified_step(self, step: int) -> bool:
        await self._settings_repository.get_or_create()
        result = await self._session.execute(
            update(DashboardSettings)
            .where(DashboardSettings.id == _SETTINGS_ID)
            .where(
                or_(
                    DashboardSettings.totp_last_verified_step.is_(None),
                    DashboardSettings.totp_last_verified_step < step,
                )
            )
            .values(totp_last_verified_step=step)
        )
        await self._session.commit()
        return bool(result.rowcount and result.rowcount > 0)

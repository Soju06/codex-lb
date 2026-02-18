from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DashboardSettings

_SETTINGS_ID = 1


class SettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(self) -> DashboardSettings:
        existing = await self._session.get(DashboardSettings, _SETTINGS_ID)
        if existing is not None:
            return existing

        row = DashboardSettings(
            id=_SETTINGS_ID,
            sticky_threads_enabled=False,
            prefer_earlier_reset_accounts=False,
            import_without_overwrite=False,
            totp_required_on_login=False,
            password_hash=None,
            api_key_auth_enabled=False,
            totp_secret_encrypted=None,
            totp_last_verified_step=None,
        )
        self._session.add(row)
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            existing = await self._session.get(DashboardSettings, _SETTINGS_ID)
            if existing is None:
                raise
            return existing
        await self._session.refresh(row)
        return row

    async def update(
        self,
        *,
        sticky_threads_enabled: bool | None = None,
        prefer_earlier_reset_accounts: bool | None = None,
        import_without_overwrite: bool | None = None,
        totp_required_on_login: bool | None = None,
        api_key_auth_enabled: bool | None = None,
    ) -> DashboardSettings:
        settings = await self.get_or_create()
        if sticky_threads_enabled is not None:
            settings.sticky_threads_enabled = sticky_threads_enabled
        if prefer_earlier_reset_accounts is not None:
            settings.prefer_earlier_reset_accounts = prefer_earlier_reset_accounts
        if import_without_overwrite is not None:
            settings.import_without_overwrite = import_without_overwrite
        if totp_required_on_login is not None:
            settings.totp_required_on_login = totp_required_on_login
        if api_key_auth_enabled is not None:
            settings.api_key_auth_enabled = api_key_auth_enabled
        await self.commit_refresh(settings)
        return settings

    async def commit_refresh(self, settings: DashboardSettings) -> None:
        await self._session.commit()
        await self._session.refresh(settings)

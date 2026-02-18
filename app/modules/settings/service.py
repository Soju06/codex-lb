from __future__ import annotations

from dataclasses import dataclass

from app.modules.settings.repository import SettingsRepository


@dataclass(frozen=True, slots=True)
class DashboardSettingsData:
    sticky_threads_enabled: bool
    prefer_earlier_reset_accounts: bool
    import_without_overwrite: bool
    totp_required_on_login: bool
    totp_configured: bool
    api_key_auth_enabled: bool


@dataclass(frozen=True, slots=True)
class DashboardSettingsUpdateData:
    sticky_threads_enabled: bool
    prefer_earlier_reset_accounts: bool
    import_without_overwrite: bool
    totp_required_on_login: bool
    api_key_auth_enabled: bool


class SettingsService:
    def __init__(self, repository: SettingsRepository) -> None:
        self._repository = repository

    async def get_settings(self) -> DashboardSettingsData:
        row = await self._repository.get_or_create()
        return DashboardSettingsData(
            sticky_threads_enabled=row.sticky_threads_enabled,
            prefer_earlier_reset_accounts=row.prefer_earlier_reset_accounts,
            import_without_overwrite=row.import_without_overwrite,
            totp_required_on_login=row.totp_required_on_login,
            totp_configured=row.totp_secret_encrypted is not None,
            api_key_auth_enabled=row.api_key_auth_enabled,
        )

    async def update_settings(self, payload: DashboardSettingsUpdateData) -> DashboardSettingsData:
        current = await self._repository.get_or_create()
        if payload.totp_required_on_login and current.totp_secret_encrypted is None:
            raise ValueError("Configure TOTP before enabling login enforcement")
        row = await self._repository.update(
            sticky_threads_enabled=payload.sticky_threads_enabled,
            prefer_earlier_reset_accounts=payload.prefer_earlier_reset_accounts,
            import_without_overwrite=payload.import_without_overwrite,
            totp_required_on_login=payload.totp_required_on_login,
            api_key_auth_enabled=payload.api_key_auth_enabled,
        )
        return DashboardSettingsData(
            sticky_threads_enabled=row.sticky_threads_enabled,
            prefer_earlier_reset_accounts=row.prefer_earlier_reset_accounts,
            import_without_overwrite=row.import_without_overwrite,
            totp_required_on_login=row.totp_required_on_login,
            totp_configured=row.totp_secret_encrypted is not None,
            api_key_auth_enabled=row.api_key_auth_enabled,
        )

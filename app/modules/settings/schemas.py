from __future__ import annotations

from app.modules.shared.schemas import DashboardModel


class DashboardSettingsResponse(DashboardModel):
    sticky_threads_enabled: bool
    prefer_earlier_reset_accounts: bool
    import_without_overwrite: bool
    totp_required_on_login: bool
    totp_configured: bool
    api_key_auth_enabled: bool


class DashboardSettingsUpdateRequest(DashboardModel):
    sticky_threads_enabled: bool
    prefer_earlier_reset_accounts: bool
    import_without_overwrite: bool | None = None
    totp_required_on_login: bool | None = None
    api_key_auth_enabled: bool | None = None

from __future__ import annotations

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


class DashboardSettingsResponse(DashboardModel):
    sticky_threads_enabled: bool
    prefer_earlier_reset_accounts: bool
    routing_strategy: str = Field(pattern=r"^(usage_weighted|round_robin)$")
    global_model_force_enabled: bool
    global_model_force_model: str | None = None
    global_model_force_reasoning_effort: str | None = Field(
        default=None,
        pattern=r"^(low|normal|medium|high|xhigh)$",
    )
    import_without_overwrite: bool
    totp_required_on_login: bool
    totp_configured: bool
    api_key_auth_enabled: bool


class DashboardSettingsUpdateRequest(DashboardModel):
    sticky_threads_enabled: bool
    prefer_earlier_reset_accounts: bool
    routing_strategy: str | None = Field(default=None, pattern=r"^(usage_weighted|round_robin)$")
    global_model_force_enabled: bool | None = None
    global_model_force_model: str | None = Field(default=None, max_length=256)
    global_model_force_reasoning_effort: str | None = Field(
        default=None,
        pattern=r"^(low|normal|medium|high|xhigh)$",
    )
    import_without_overwrite: bool | None = None
    totp_required_on_login: bool | None = None
    api_key_auth_enabled: bool | None = None

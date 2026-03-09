from __future__ import annotations

from pydantic import Field
from pydantic import field_validator

from app.core.config.proxy import normalize_http_proxy_url
from app.modules.shared.schemas import DashboardModel


class DashboardSettingsResponse(DashboardModel):
    sticky_threads_enabled: bool
    prefer_earlier_reset_accounts: bool
    routing_strategy: str = Field(pattern=r"^(usage_weighted|round_robin)$")
    import_without_overwrite: bool
    http_proxy_url: str | None = None
    totp_required_on_login: bool
    totp_configured: bool
    api_key_auth_enabled: bool


class DashboardSettingsUpdateRequest(DashboardModel):
    sticky_threads_enabled: bool
    prefer_earlier_reset_accounts: bool
    routing_strategy: str | None = Field(default=None, pattern=r"^(usage_weighted|round_robin)$")
    import_without_overwrite: bool | None = None
    http_proxy_url: str | None = None
    totp_required_on_login: bool | None = None
    api_key_auth_enabled: bool | None = None

    @field_validator("http_proxy_url", mode="before")
    @classmethod
    def _normalize_http_proxy_url(cls, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            return normalize_http_proxy_url(value)
        raise TypeError("http_proxy_url must be a string")
class RuntimeConnectAddressResponse(DashboardModel):
    connect_address: str
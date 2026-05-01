from __future__ import annotations

from pydantic import Field

from app.modules.shared.schemas import DashboardModel


class DashboardSettingsResponse(DashboardModel):
    sticky_threads_enabled: bool
    upstream_stream_transport: str = Field(pattern=r"^(default|auto|http|websocket)$")
    prefer_earlier_reset_accounts: bool
    routing_strategy: str = Field(pattern=r"^(usage_weighted|round_robin|capacity_weighted|relative_availability)$")
    relative_availability_power: float = Field(gt=0.0)
    relative_availability_top_k: int = Field(ge=1, le=20)
    openai_cache_affinity_max_age_seconds: int = Field(gt=0)
    dashboard_session_ttl_seconds: int = Field(ge=3600)
    http_responses_session_bridge_prompt_cache_idle_ttl_seconds: int = Field(gt=0)
    http_responses_session_bridge_gateway_safe_mode: bool
    sticky_reallocation_budget_threshold_pct: float = Field(ge=0.0, le=100.0)
    import_without_overwrite: bool
    totp_required_on_login: bool
    totp_configured: bool
    api_key_auth_enabled: bool


class DashboardSettingsUpdateRequest(DashboardModel):
    sticky_threads_enabled: bool
    upstream_stream_transport: str | None = Field(
        default=None,
        pattern=r"^(default|auto|http|websocket)$",
    )
    prefer_earlier_reset_accounts: bool
    routing_strategy: str | None = Field(
        default=None,
        pattern=r"^(usage_weighted|round_robin|capacity_weighted|relative_availability)$",
    )
    relative_availability_power: float | None = Field(default=None, gt=0.0)
    relative_availability_top_k: int | None = Field(default=None, ge=1, le=20)
    openai_cache_affinity_max_age_seconds: int | None = Field(default=None, gt=0)
    dashboard_session_ttl_seconds: int | None = Field(default=None, ge=3600)
    http_responses_session_bridge_prompt_cache_idle_ttl_seconds: int | None = Field(default=None, gt=0)
    http_responses_session_bridge_gateway_safe_mode: bool | None = None
    sticky_reallocation_budget_threshold_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    import_without_overwrite: bool | None = None
    totp_required_on_login: bool | None = None
    api_key_auth_enabled: bool | None = None


class RuntimeConnectAddressResponse(DashboardModel):
    connect_address: str

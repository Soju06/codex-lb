from __future__ import annotations

from pydantic import Field, field_validator

from app.modules.shared.schemas import DashboardModel

_DEFAULT_WEEKLY_PACE_WORKING_DAYS = "0,1,2,3,4,5,6"


def _normalize_weekly_pace_working_days(value: str | None) -> str | None:
    if value is None:
        return None
    tokens = [part.strip() for part in value.split(",") if part.strip()]
    if not tokens:
        raise ValueError("weekly_pace_working_days must include at least one day")
    try:
        days = sorted({int(token) for token in tokens})
    except ValueError as exc:
        raise ValueError("weekly_pace_working_days must contain weekday numbers") from exc
    if any(day < 0 or day > 6 for day in days):
        raise ValueError("weekly_pace_working_days must use 0-6 weekday numbers")
    return ",".join(str(day) for day in days)


class AdditionalQuotaPolicy(DashboardModel):
    quota_key: str
    display_label: str
    routing_policy: str = Field(pattern=r"^(inherit|burn_first|normal|preserve)$")
    model_ids: list[str] = Field(default_factory=list)


class DashboardSettingsResponse(DashboardModel):
    sticky_threads_enabled: bool
    upstream_stream_transport: str = Field(pattern=r"^(default|auto|http|websocket)$")
    upstream_proxy_routing_enabled: bool
    upstream_proxy_default_pool_id: str | None = None
    prefer_earlier_reset_accounts: bool
    prefer_earlier_reset_window: str = Field(pattern=r"^(primary|secondary)$")
    routing_strategy: str = Field(
        pattern=r"^(usage_weighted|round_robin|capacity_weighted|relative_availability|fill_first|sequential_drain|reset_drain|single_account|ordered_fallback)$"
    )
    relative_availability_power: float = Field(gt=0.0)
    relative_availability_top_k: int = Field(ge=1, le=20)
    single_account_id: str | None = None
    manual_account_priority_ids: list[str] = Field(default_factory=list)
    openai_cache_affinity_max_age_seconds: int = Field(gt=0)
    dashboard_session_ttl_seconds: int = Field(ge=3600)
    http_responses_session_bridge_prompt_cache_idle_ttl_seconds: int = Field(gt=0)
    http_responses_session_bridge_gateway_safe_mode: bool
    sticky_reallocation_budget_threshold_pct: float = Field(ge=0.0, le=100.0)
    sticky_reallocation_primary_budget_threshold_pct: float = Field(ge=0.0, le=100.0)
    sticky_reallocation_secondary_budget_threshold_pct: float = Field(ge=0.0, le=100.0)
    warmup_model: str = Field(min_length=1)
    import_without_overwrite: bool
    totp_required_on_login: bool
    totp_configured: bool
    api_key_auth_enabled: bool
    limit_warmup_enabled: bool
    limit_warmup_windows: str = Field(pattern=r"^(primary|secondary|both)$")
    limit_warmup_model: str = Field(min_length=1, max_length=128)
    limit_warmup_prompt: str = Field(min_length=1, max_length=512)
    limit_warmup_cooldown_seconds: int = Field(ge=60)
    limit_warmup_min_available_percent: float = Field(gt=0.0, le=100.0)
    weekly_pace_working_days: str = _DEFAULT_WEEKLY_PACE_WORKING_DAYS
    additional_quota_routing_policies: dict[str, str] = Field(default_factory=dict)
    additional_quota_policies: list[AdditionalQuotaPolicy] = Field(default_factory=list)


class DashboardSettingsUpdateRequest(DashboardModel):
    sticky_threads_enabled: bool | None = None
    upstream_stream_transport: str | None = Field(
        default=None,
        pattern=r"^(default|auto|http|websocket)$",
    )
    upstream_proxy_routing_enabled: bool | None = None
    upstream_proxy_default_pool_id: str | None = None
    prefer_earlier_reset_accounts: bool | None = None
    prefer_earlier_reset_window: str | None = Field(default=None, pattern=r"^(primary|secondary)$")
    routing_strategy: str | None = Field(
        default=None,
        pattern=r"^(usage_weighted|round_robin|capacity_weighted|relative_availability|fill_first|sequential_drain|reset_drain|single_account|ordered_fallback)$",
    )
    relative_availability_power: float | None = Field(default=None, gt=0.0)
    relative_availability_top_k: int | None = Field(default=None, ge=1, le=20)
    single_account_id: str | None = Field(default=None, max_length=255)
    manual_account_priority_ids: list[str] | None = None
    openai_cache_affinity_max_age_seconds: int | None = Field(default=None, gt=0)
    dashboard_session_ttl_seconds: int | None = Field(default=None, ge=3600)
    http_responses_session_bridge_prompt_cache_idle_ttl_seconds: int | None = Field(default=None, gt=0)
    http_responses_session_bridge_gateway_safe_mode: bool | None = None
    sticky_reallocation_budget_threshold_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    sticky_reallocation_primary_budget_threshold_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    sticky_reallocation_secondary_budget_threshold_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    additional_quota_routing_policies: dict[str, str] | None = None
    warmup_model: str | None = Field(default=None, min_length=1)
    import_without_overwrite: bool | None = None
    totp_required_on_login: bool | None = None
    api_key_auth_enabled: bool | None = None
    limit_warmup_enabled: bool | None = None
    limit_warmup_windows: str | None = Field(default=None, pattern=r"^(primary|secondary|both)$")
    limit_warmup_model: str | None = Field(default=None, min_length=1, max_length=128)
    limit_warmup_prompt: str | None = Field(default=None, min_length=1, max_length=512)
    limit_warmup_cooldown_seconds: int | None = Field(default=None, ge=60)
    limit_warmup_min_available_percent: float | None = Field(default=None, gt=0.0, le=100.0)
    weekly_pace_working_days: str | None = None

    @field_validator("warmup_model")
    @classmethod
    def _normalize_warmup_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("warmup_model must not be blank")
        return normalized

    @field_validator("weekly_pace_working_days")
    @classmethod
    def _normalize_weekly_pace_days(cls, value: str | None) -> str | None:
        return _normalize_weekly_pace_working_days(value)

    @field_validator("manual_account_priority_ids")
    @classmethod
    def _normalize_manual_account_priority_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        seen: set[str] = set()
        ordered: list[str] = []
        for raw_account_id in value:
            account_id = raw_account_id.strip()
            if not account_id:
                continue
            if account_id in seen:
                continue
            seen.add(account_id)
            ordered.append(account_id)
        return ordered


class RuntimeConnectAddressResponse(DashboardModel):
    connect_address: str


class UpstreamProxyEndpointCreateRequest(DashboardModel):
    name: str = Field(min_length=1, max_length=128)
    scheme: str = Field(pattern=r"^(http|https|socks5|socks5h)$")
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=1024)
    is_active: bool = True


class UpstreamProxyEndpointResponse(DashboardModel):
    id: str
    name: str
    scheme: str
    host: str
    port: int
    username: str | None
    is_active: bool


class UpstreamProxyPoolCreateRequest(DashboardModel):
    name: str = Field(min_length=1, max_length=128)
    endpoint_ids: list[str] = Field(default_factory=list)
    is_active: bool = True


class UpstreamProxyPoolMemberRequest(DashboardModel):
    endpoint_id: str = Field(min_length=1)
    sort_order: int = 0
    weight: int = Field(default=1, ge=1)
    is_active: bool = True


class UpstreamProxyPoolResponse(DashboardModel):
    id: str
    name: str
    is_active: bool
    endpoint_ids: list[str]


class AccountProxyBindingRequest(DashboardModel):
    pool_id: str = Field(min_length=1)
    is_active: bool = True


class AccountProxyBindingResponse(DashboardModel):
    account_id: str
    pool_id: str
    is_active: bool


class UpstreamProxyAdminResponse(DashboardModel):
    routing_enabled: bool
    default_pool_id: str | None
    endpoints: list[UpstreamProxyEndpointResponse]
    pools: list[UpstreamProxyPoolResponse]
    bindings: list[AccountProxyBindingResponse]

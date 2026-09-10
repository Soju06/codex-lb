from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, StrictInt, field_validator

from app.modules.shared.schemas import DashboardModel

_DEFAULT_WEEKLY_PACE_WORKING_DAYS = "0,1,2,3,4,5,6"
_WEEKLY_PACE_SMOOTHING_MINUTES = (15, 30, 60, 120, 240)
_HTTP_DOWNSTREAM_TRANSPORT_POLICY_PATTERN = r"^(smart|always_http|always_websocket|pinned)$"


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


class SettingProvenance(DashboardModel):
    """Where an inheritable setting's effective value comes from.

    ``source`` is ``"dashboard"`` (the dashboard column is set), ``"env"`` (the
    column is NULL and the environment value differs from the code default) or
    ``"default"``. ``env_value`` is the environment value that applies while
    the column is NULL (``None`` for database-only settings); ``default`` is
    the code default. Both carry the setting's own scalar type.
    """

    source: Literal["dashboard", "env", "default"]
    env_value: int | float | str | bool | None = None
    default: int | float | str | bool | None = None


class DashboardSettingsResponse(DashboardModel):
    sticky_threads_enabled: bool
    upstream_stream_transport: str = Field(pattern=r"^(auto|http|websocket)$")
    prohibit_fast_mode: bool
    http_downstream_transport_policy: str = Field(pattern=_HTTP_DOWNSTREAM_TRANSPORT_POLICY_PATTERN)
    proxy_account_response_create_limit: int = Field(ge=0)
    proxy_account_response_create_limit_environment_value: int = Field(ge=0)
    proxy_account_response_create_limit_override: int | None = Field(default=None, ge=0)
    proxy_account_stream_limit: int = Field(ge=0)
    proxy_account_stream_limit_environment_value: int = Field(ge=0)
    proxy_account_stream_limit_override: int | None = Field(default=None, ge=0)
    proxy_account_stream_recovery_reserve: int = Field(ge=0)
    proxy_account_stream_recovery_reserve_environment_value: int = Field(ge=0)
    proxy_account_stream_recovery_reserve_override: int | None = Field(default=None, ge=0)
    proxy_api_key_fair_share_congestion_threshold_pct: int = Field(ge=0, le=100)
    proxy_api_key_fair_share_congestion_threshold_pct_environment_value: int = Field(ge=0, le=100)
    proxy_api_key_fair_share_congestion_threshold_pct_override: int | None = Field(default=None, ge=0, le=100)
    # C2-2 routing/overload: effective values (dashboard column, else the
    # environment, else the code default); ``provenance[<name>]`` says which.
    # Only the ``Settings`` bounds apply here: an inherited environment value
    # above the dashboard write cap (penalty > 100) must still be readable.
    proxy_overload_isolation_seconds: int = Field(ge=0)
    proxy_account_error_rate_weighting_enabled: bool
    proxy_account_inflight_penalty_pct: float = Field(ge=0)
    proxy_account_lease_token_weight: float = Field(ge=0)
    proxy_account_lease_ttl_seconds: float = Field(gt=0)
    # end C2-2 routing/overload
    upstream_proxy_routing_enabled: bool
    upstream_proxy_default_pool_id: str | None = None
    prefer_earlier_reset_accounts: bool
    prefer_earlier_reset_window: str = Field(pattern=r"^(primary|secondary)$")
    show_reset_credit_badges: bool
    auto_redeem_reset_credits_before_expiry: bool
    show_reset_credit_expiry_badge: bool
    routing_strategy: str = Field(
        pattern=r"^(usage_weighted|round_robin|capacity_weighted|relative_availability|fill_first|sequential_drain|reset_drain|single_account)$"
    )
    relative_availability_power: float = Field(gt=0.0)
    relative_availability_top_k: int = Field(ge=1, le=20)
    single_account_id: str | None = None
    subscription_overflow_source_id: str | None = None
    subscription_overflow_drain_until: datetime | None = None
    # Derived, read-only: the drain deadline minus the tombstone grace and one
    # day (the clear time plus the 7-day pin idle limit); ``None`` when no drain
    # is armed. The date every pinned conversation has expired by.
    subscription_overflow_pins_expire_by: datetime | None = None
    openai_cache_affinity_max_age_seconds: int = Field(gt=0)
    dashboard_session_ttl_seconds: int = Field(ge=3600)
    http_responses_session_bridge_prompt_cache_idle_ttl_seconds: int = Field(gt=0)
    http_responses_session_bridge_gateway_safe_mode: bool
    # M3 codex prewarm: effective value; ``provenance[<name>]`` says whether it
    # comes from the dashboard, the deprecated env alias or the code default.
    http_responses_session_bridge_codex_prewarm_enabled: bool
    # end M3 codex prewarm
    sticky_reallocation_budget_threshold_pct: float = Field(ge=0.0, le=100.0)
    sticky_reallocation_primary_budget_threshold_pct: float = Field(ge=0.0, le=100.0)
    sticky_reallocation_secondary_budget_threshold_pct: float = Field(ge=0.0, le=100.0)
    warmup_model: str = Field(min_length=1)
    import_without_overwrite: bool
    totp_required_on_login: bool
    totp_configured: bool
    api_key_auth_enabled: bool
    hide_upstream_quota_from_api_keys: bool
    limit_warmup_enabled: bool
    limit_warmup_windows: str = Field(pattern=r"^(primary|secondary|both)$")
    limit_warmup_model: str = Field(min_length=1, max_length=128)
    limit_warmup_prompt: str = Field(min_length=1, max_length=512)
    limit_warmup_cooldown_seconds: int = Field(ge=60)
    limit_warmup_exhausted_threshold_percent: float = Field(gt=0.0, le=100.0)
    limit_warmup_idle_threshold_percent: float = Field(gt=0.0, le=100.0)
    limit_warmup_min_available_percent: float = Field(gt=0.0, le=100.0)
    weekly_pace_working_days: str = _DEFAULT_WEEKLY_PACE_WORKING_DAYS
    weekly_pace_smoothing_minutes: int = Field(default=30)
    limit_warmup_staggered_idle_enabled: bool
    request_log_retention_days: int = Field(ge=0, le=3650)
    usage_history_retention_days: int = Field(ge=0, le=3650)
    request_log_retention_override_days: int | None = Field(default=None, ge=0, le=3650)
    usage_history_retention_override_days: int | None = Field(default=None, ge=0, le=3650)
    additional_quota_routing_policies: dict[str, str] = Field(default_factory=dict)
    additional_quota_policies: list[AdditionalQuotaPolicy] = Field(default_factory=list)
    guest_access_enabled: bool
    guest_password_configured: bool
    # C2-3 resilience toggles: effective values; ``provenance[<name>]`` says
    # whether each comes from the dashboard, the deprecated env alias or the
    # code default.
    soft_drain_enabled: bool
    deterministic_failover_enabled: bool
    circuit_breaker_enabled: bool
    # M2 background jobs: effective values; ``provenance[<name>]`` says whether
    # each comes from the dashboard, the deprecated env alias or the code
    # default. ``auth_guardian_blocked_by_topology`` is True when a multi-replica
    # ring without leader election keeps the guardian idle whatever the toggle.
    auth_guardian_enabled: bool
    auth_guardian_blocked_by_topology: bool
    automations_scheduler_enabled: bool
    rate_limit_reset_credits_refresh_enabled: bool
    # end M2 background jobs
    # M5 conversation archive: effective toggle (``provenance`` says whether it
    # comes from the dashboard, the deprecated env alias or the default) and
    # the read-only T1 archive directory of *this* replica (each replica writes
    # its own local shard; ``None`` for read-only guests).
    conversation_archive_enabled: bool
    conversation_archive_dir: str | None = None
    # end M5 conversation archive
    version: int = Field(ge=1)
    # C2-1 timeouts: effective values; ``provenance[<name>]`` says whether the
    # dashboard, the environment or the code default supplied each one. No
    # bounds here: an environment value the ``Settings`` model accepts must
    # never make ``GET /api/settings`` fail (the update request is bounded).
    upstream_connect_timeout_seconds: float
    proxy_request_budget_seconds: float
    compact_request_budget_seconds: float
    transcription_request_budget_seconds: float
    stream_idle_timeout_seconds: float
    proxy_downstream_websocket_idle_timeout_seconds: float
    sse_keepalive_interval_seconds: float
    # end C2-1 timeouts
    # M1 stream/bridge budgets: effective values, unbounded like the C2-1
    # timeouts above; ``provenance[<name>]`` names the source.
    http_responses_stream_request_budget_seconds: float
    http_responses_session_bridge_request_budget_seconds: float
    # end M1 stream/bridge budgets
    # Provenance of every inheritable setting keyed by its setting name (the
    # ``dashboard_settings`` column / ``Settings`` field name). Additive: the
    # flat ``<name>``, ``<name>_environment_value`` and ``<name>_override``
    # fields above stay as they are.
    provenance: dict[str, SettingProvenance] = Field(default_factory=dict)


class DashboardSettingsUpdateRequest(DashboardModel):
    """Partial update of the dashboard settings.

    Inheritable settings (the four account-capacity caps, the two retention
    overrides, the three resilience toggles, the Codex prewarm switch and the
    three background job toggles) are tri-state, decided by
    ``model_fields_set``: a field that is
    omitted is left unchanged, an explicit ``null`` clears the dashboard value
    so the setting returns to inheriting the environment value or code default
    (``provenance[<name>].source`` becomes ``"env"`` or ``"default"``), and a
    concrete value is stored and wins over both.
    """

    expected_version: int | None = Field(default=None, ge=1)
    sticky_threads_enabled: bool | None = None
    upstream_stream_transport: str | None = Field(
        default=None,
        pattern=r"^(auto|http|websocket)$",
    )
    prohibit_fast_mode: bool | None = None
    http_downstream_transport_policy: str | None = Field(
        default=None,
        pattern=_HTTP_DOWNSTREAM_TRANSPORT_POLICY_PATTERN,
    )
    proxy_account_response_create_limit: int | None = Field(default=None, ge=0)
    proxy_account_stream_limit: int | None = Field(default=None, ge=0)
    proxy_account_stream_recovery_reserve: int | None = Field(default=None, ge=0)
    proxy_api_key_fair_share_congestion_threshold_pct: int | None = Field(default=None, ge=0, le=100)
    # C2-2 routing/overload: tri-state like the caps above (omitted = unchanged,
    # null = inherit, value = store). Bounds mirror the ``Settings`` fields; the
    # in-flight penalty is additionally capped at 100 because it is added to a
    # percentage that saturates there.
    proxy_overload_isolation_seconds: int | None = Field(default=None, ge=0)
    proxy_account_error_rate_weighting_enabled: bool | None = None
    proxy_account_inflight_penalty_pct: float | None = Field(default=None, ge=0, le=100)
    proxy_account_lease_token_weight: float | None = Field(default=None, ge=0)
    proxy_account_lease_ttl_seconds: float | None = Field(default=None, gt=0)
    # end C2-2 routing/overload
    upstream_proxy_routing_enabled: bool | None = None
    upstream_proxy_default_pool_id: str | None = None
    prefer_earlier_reset_accounts: bool | None = None
    prefer_earlier_reset_window: str | None = Field(default=None, pattern=r"^(primary|secondary)$")
    show_reset_credit_badges: bool | None = None
    auto_redeem_reset_credits_before_expiry: bool | None = None
    show_reset_credit_expiry_badge: bool | None = None
    routing_strategy: str | None = Field(
        default=None,
        pattern=r"^(usage_weighted|round_robin|capacity_weighted|relative_availability|fill_first|sequential_drain|reset_drain|single_account)$",
    )
    relative_availability_power: float | None = Field(default=None, gt=0.0)
    relative_availability_top_k: int | None = Field(default=None, ge=1, le=20)
    single_account_id: str | None = Field(default=None, max_length=255)
    # Tri-state via ``model_fields_set``: absent = unchanged, null = off
    # (arms the drain deadline), value = designate. The drain deadline itself
    # is read-only.
    subscription_overflow_source_id: str | None = Field(default=None, max_length=255)
    openai_cache_affinity_max_age_seconds: int | None = Field(default=None, gt=0)
    dashboard_session_ttl_seconds: int | None = Field(default=None, ge=3600)
    http_responses_session_bridge_prompt_cache_idle_ttl_seconds: int | None = Field(default=None, gt=0)
    http_responses_session_bridge_gateway_safe_mode: bool | None = None
    # M3 codex prewarm: tri-state via ``model_fields_set`` (absent = unchanged,
    # null = clear the dashboard value and inherit the deprecated env alias /
    # code default, value = store).
    http_responses_session_bridge_codex_prewarm_enabled: bool | None = None
    # end M3 codex prewarm
    sticky_reallocation_budget_threshold_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    sticky_reallocation_primary_budget_threshold_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    sticky_reallocation_secondary_budget_threshold_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    additional_quota_routing_policies: dict[str, str] | None = None
    warmup_model: str | None = Field(default=None, min_length=1)
    import_without_overwrite: bool | None = None
    totp_required_on_login: bool | None = None
    api_key_auth_enabled: bool | None = None
    hide_upstream_quota_from_api_keys: bool | None = None
    limit_warmup_enabled: bool | None = None
    limit_warmup_windows: str | None = Field(default=None, pattern=r"^(primary|secondary|both)$")
    limit_warmup_model: str | None = Field(default=None, min_length=1, max_length=128)
    limit_warmup_prompt: str | None = Field(default=None, min_length=1, max_length=512)
    limit_warmup_cooldown_seconds: int | None = Field(default=None, ge=60)
    limit_warmup_exhausted_threshold_percent: float | None = Field(default=None, gt=0.0, le=100.0)
    limit_warmup_idle_threshold_percent: float | None = Field(default=None, gt=0.0, le=100.0)
    limit_warmup_min_available_percent: float | None = Field(default=None, gt=0.0, le=100.0)
    weekly_pace_working_days: str | None = None
    weekly_pace_smoothing_minutes: int | None = None
    guest_access_enabled: bool | None = None
    limit_warmup_staggered_idle_enabled: bool | None = None
    # Tri-state retention overrides: absent = unchanged, present null = clear
    # the override (back to inheriting the deprecated env alias), present
    # value = store the override.
    request_log_retention_override_days: int | None = Field(default=None, ge=0, le=3650)
    usage_history_retention_override_days: int | None = Field(default=None, ge=0, le=3650)
    # C2-3 resilience toggles: tri-state via ``model_fields_set`` (absent =
    # unchanged, null = clear the dashboard value and inherit the deprecated
    # env alias / code default, value = store).
    soft_drain_enabled: bool | None = None
    deterministic_failover_enabled: bool | None = None
    circuit_breaker_enabled: bool | None = None
    # M2 background jobs: tri-state via ``model_fields_set`` like the
    # resilience toggles.
    auth_guardian_enabled: bool | None = None
    automations_scheduler_enabled: bool | None = None
    rate_limit_reset_credits_refresh_enabled: bool | None = None
    # end M2 background jobs
    # M5 conversation archive: tri-state like the resilience toggles. ``true``
    # turns the proxy into a full prompt/response recorder; the dashboard asks
    # for confirmation first and the API audits every effective on/off change.
    conversation_archive_enabled: bool | None = None
    # end M5 conversation archive
    # C2-1 timeouts: tri-state like the caps (absent = unchanged, null = clear
    # to inherit the environment / default, value = store). Cross-field timeout
    # invariants are checked against the effective values in the API handler.
    upstream_connect_timeout_seconds: float | None = Field(default=None, gt=0, le=86400)
    proxy_request_budget_seconds: float | None = Field(default=None, gt=0, le=86400)
    compact_request_budget_seconds: float | None = Field(default=None, gt=0, le=86400)
    transcription_request_budget_seconds: float | None = Field(default=None, gt=0, le=86400)
    stream_idle_timeout_seconds: float | None = Field(default=None, gt=0, le=86400)
    proxy_downstream_websocket_idle_timeout_seconds: float | None = Field(default=None, gt=0, le=86400)
    sse_keepalive_interval_seconds: float | None = Field(default=None, ge=0, le=86400)
    # end C2-1 timeouts
    # M1 stream/bridge budgets: same tri-state contract and bounds as the
    # C2-1 timeouts; the connect-within-stream-budget and stuck-gate-within-
    # bridge-budget invariants are checked on the effective values.
    http_responses_stream_request_budget_seconds: float | None = Field(default=None, gt=0, le=86400)
    http_responses_session_bridge_request_budget_seconds: float | None = Field(default=None, gt=0, le=86400)
    # end M1 stream/bridge budgets

    @field_validator("request_log_retention_override_days")
    @classmethod
    def _validate_request_log_retention_override(cls, value: int | None) -> int | None:
        if value is not None and value != 0 and value < 30:
            raise ValueError("request_log_retention_override_days must be 0 (disabled) or >= 30")
        return value

    @field_validator("usage_history_retention_override_days")
    @classmethod
    def _validate_usage_history_retention_override(cls, value: int | None) -> int | None:
        if value is not None and value != 0 and value < 45:
            raise ValueError("usage_history_retention_override_days must be 0 (disabled) or >= 45")
        return value

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

    @field_validator("weekly_pace_smoothing_minutes")
    @classmethod
    def _validate_weekly_pace_smoothing_minutes(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value not in _WEEKLY_PACE_SMOOTHING_MINUTES:
            raise ValueError("weekly_pace_smoothing_minutes must be one of 15, 30, 60, 120, 240")
        return value


class SubscriptionOverflowContextWindowMismatch(DashboardModel):
    registry: int
    source: int | None = None
    max_output_tokens: int | None = None


class SubscriptionOverflowPreflightModel(DashboardModel):
    slug: str
    enabled: bool
    never_overflows: bool
    never_overflows_reason: str | None = None
    undeclared_tool_types: list[str] = Field(default_factory=list)
    supports_vision: bool
    supports_streaming: bool
    priced: bool
    context_window_mismatch: SubscriptionOverflowContextWindowMismatch | None = None
    warnings: list[str] = Field(default_factory=list)


class SubscriptionOverflowPreflightResponse(DashboardModel):
    source_id: str
    source_name: str
    source_enabled: bool
    eligible: bool
    blockers: list[str] = Field(default_factory=list)
    drain_until: datetime | None = None
    served_models: list[SubscriptionOverflowPreflightModel] = Field(default_factory=list)
    missing_models: list[str] = Field(default_factory=list)
    scoped_api_key_count: int = Field(ge=0)
    live_pin_count: int = Field(ge=0)
    tombstone_count: int = Field(ge=0)


class RuntimeConnectAddressResponse(DashboardModel):
    connect_address: str


# M4 model catalogue: per-model context window overrides. ``source`` is
# ``"dashboard"`` when a dashboard row exists for the slug and ``"env"`` when
# only the ``CODEX_LB_MODEL_CONTEXT_WINDOW_OVERRIDES`` entry applies;
# ``env_value`` is that entry (``None`` when the environment has none).
class ModelContextWindowOverrideResponse(DashboardModel):
    slug: str
    context_window: int
    source: Literal["dashboard", "env"]
    env_value: int | None = None


class ModelContextWindowOverridesResponse(DashboardModel):
    overrides: list[ModelContextWindowOverrideResponse]


# The column is a database ``Integer``, so PostgreSQL rejects anything wider at
# commit time; bounding the request turns that 500 into a 422.
MAX_MODEL_CONTEXT_WINDOW = 2_147_483_647


class ModelContextWindowOverrideUpsertRequest(DashboardModel):
    # StrictInt, not ``int``: a reported context window is a token count, so a
    # bool, a float or a numeric string is an operator mistake to surface as a
    # 422 rather than silently coerce into a stored window.
    context_window: StrictInt = Field(ge=1, le=MAX_MODEL_CONTEXT_WINDOW)


# end M4 model catalogue


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
    # Credentials cross the LB-to-proxy hop unencrypted (http/socks5/socks5h
    # with a username or password); the dashboard renders a warning.
    plaintext_credentials: bool = False


class UpstreamProxyEndpointTestResponse(DashboardModel):
    endpoint_id: str
    ok: bool
    status_code: int | None = None
    elapsed_ms: int | None = None
    error: str | None = None


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

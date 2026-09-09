from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from app.core.config.settings import Settings, get_settings
from app.db.models import DashboardSettings
from app.modules.settings.repository import SettingsRepository
from app.modules.usage.additional_quota_keys import (
    normalize_additional_quota_key,
)

type SettingSource = Literal["dashboard", "env", "default"]
type SettingScalar = int | float | str | bool


@dataclass(frozen=True, slots=True)
class InheritableValue[T: SettingScalar]:
    """Effective value of a dashboard-tier setting and where it came from.

    ``source`` is ``"dashboard"`` when the dashboard column is non-NULL,
    ``"env"`` when the column is NULL and the environment value differs from
    the code default, and ``"default"`` otherwise. ``env_value`` is ``None``
    for database-only settings that have no environment fallback.
    """

    value: T
    source: SettingSource
    env_value: T | None
    default: T


def resolve_inheritable[T: SettingScalar](
    column_value: T | None, env_value: T | None, default: T
) -> InheritableValue[T]:
    """Resolve one inheritable setting as code default < environment < dashboard.

    This is the only place that combines the three layers; call sites must not
    re-implement the precedence (``configuration-tiers``).
    """
    if column_value is not None:
        return InheritableValue(column_value, "dashboard", env_value, default)
    if env_value is not None and env_value != default:
        return InheritableValue(env_value, "env", env_value, default)
    return InheritableValue(default, "default", env_value, default)


@dataclass(frozen=True, slots=True)
class DashboardSettingsData:
    sticky_threads_enabled: bool
    upstream_stream_transport: str
    prohibit_fast_mode: bool
    http_downstream_transport_policy: str
    proxy_account_response_create_limit: int
    proxy_account_response_create_limit_override: int | None
    proxy_account_stream_limit: int
    proxy_account_stream_limit_override: int | None
    proxy_account_stream_recovery_reserve: int
    proxy_account_stream_recovery_reserve_override: int | None
    proxy_api_key_fair_share_congestion_threshold_pct: int
    proxy_api_key_fair_share_congestion_threshold_pct_override: int | None
    upstream_proxy_routing_enabled: bool
    upstream_proxy_default_pool_id: str | None
    prefer_earlier_reset_accounts: bool
    prefer_earlier_reset_window: str
    show_reset_credit_badges: bool
    auto_redeem_reset_credits_before_expiry: bool
    show_reset_credit_expiry_badge: bool
    routing_strategy: str
    relative_availability_power: float
    relative_availability_top_k: int
    single_account_id: str | None
    subscription_overflow_source_id: str | None
    subscription_overflow_drain_until: datetime | None
    openai_cache_affinity_max_age_seconds: int
    dashboard_session_ttl_seconds: int
    http_responses_session_bridge_prompt_cache_idle_ttl_seconds: int
    http_responses_session_bridge_gateway_safe_mode: bool
    sticky_reallocation_budget_threshold_pct: float
    sticky_reallocation_primary_budget_threshold_pct: float
    sticky_reallocation_secondary_budget_threshold_pct: float
    additional_quota_routing_policies: dict[str, str]
    warmup_model: str
    import_without_overwrite: bool
    totp_required_on_login: bool
    totp_configured: bool
    api_key_auth_enabled: bool
    hide_upstream_quota_from_api_keys: bool
    limit_warmup_enabled: bool
    limit_warmup_windows: str
    limit_warmup_model: str
    limit_warmup_prompt: str
    limit_warmup_cooldown_seconds: int
    limit_warmup_exhausted_threshold_percent: float
    limit_warmup_idle_threshold_percent: float
    limit_warmup_min_available_percent: float
    weekly_pace_working_days: str
    weekly_pace_smoothing_minutes: int
    guest_access_enabled: bool
    guest_password_configured: bool
    limit_warmup_staggered_idle_enabled: bool
    request_log_retention_days: int
    usage_history_retention_days: int
    request_log_retention_override_days: int | None
    usage_history_retention_override_days: int | None
    version: int
    # Effective value, source and fallbacks of every inheritable setting, keyed
    # by setting name; the settings API exposes it as ``provenance``.
    provenance: Mapping[str, InheritableValue[int]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DashboardSettingsUpdateData:
    sticky_threads_enabled: bool
    upstream_stream_transport: str
    prohibit_fast_mode: bool
    http_downstream_transport_policy: str
    proxy_account_response_create_limit: int | None
    clear_proxy_account_response_create_limit: bool
    proxy_account_stream_limit: int | None
    clear_proxy_account_stream_limit: bool
    proxy_account_stream_recovery_reserve: int | None
    clear_proxy_account_stream_recovery_reserve: bool
    proxy_api_key_fair_share_congestion_threshold_pct: int | None
    clear_proxy_api_key_fair_share_congestion_threshold_pct: bool
    upstream_proxy_routing_enabled: bool
    upstream_proxy_default_pool_id: str | None
    prefer_earlier_reset_accounts: bool
    prefer_earlier_reset_window: str
    show_reset_credit_badges: bool
    auto_redeem_reset_credits_before_expiry: bool
    show_reset_credit_expiry_badge: bool
    routing_strategy: str
    relative_availability_power: float
    relative_availability_top_k: int
    single_account_id: str | None
    # Tri-state designation: value = designate, clear flag = off, neither =
    # untouched. The drain deadline is written only when its set flag is on.
    subscription_overflow_source_id: str | None
    clear_subscription_overflow_source: bool
    subscription_overflow_drain_until: datetime | None
    set_subscription_overflow_drain_until: bool
    openai_cache_affinity_max_age_seconds: int
    dashboard_session_ttl_seconds: int
    http_responses_session_bridge_prompt_cache_idle_ttl_seconds: int
    http_responses_session_bridge_gateway_safe_mode: bool
    sticky_reallocation_budget_threshold_pct: float
    sticky_reallocation_primary_budget_threshold_pct: float
    sticky_reallocation_secondary_budget_threshold_pct: float
    additional_quota_routing_policies: dict[str, str]
    warmup_model: str
    import_without_overwrite: bool
    totp_required_on_login: bool
    api_key_auth_enabled: bool
    hide_upstream_quota_from_api_keys: bool
    limit_warmup_enabled: bool
    limit_warmup_windows: str
    limit_warmup_model: str
    limit_warmup_prompt: str
    limit_warmup_cooldown_seconds: int
    limit_warmup_exhausted_threshold_percent: float
    limit_warmup_idle_threshold_percent: float
    limit_warmup_min_available_percent: float
    weekly_pace_working_days: str
    weekly_pace_smoothing_minutes: int
    guest_access_enabled: bool
    limit_warmup_staggered_idle_enabled: bool
    # Tri-state retention overrides: value set = store, clear flag = reset to
    # NULL (not configured = retention disabled), neither = leave untouched.
    request_log_retention_override_days: int | None
    usage_history_retention_override_days: int | None
    clear_request_log_retention_override: bool
    clear_usage_history_retention_override: bool


class SettingsService:
    def __init__(self, repository: SettingsRepository) -> None:
        self._repository = repository

    async def get_settings(self) -> DashboardSettingsData:
        row = await self._repository.get_or_create()
        return _settings_data(row)

    async def update_settings(
        self,
        payload: DashboardSettingsUpdateData,
        *,
        expected_version: int | None = None,
    ) -> DashboardSettingsData:
        current = await self._repository.get_or_create()
        if payload.totp_required_on_login and current.totp_secret_encrypted is None:
            raise ValueError("Configure TOTP before enabling login enforcement")
        row = await self._repository.update(
            expected_version=expected_version,
            sticky_threads_enabled=payload.sticky_threads_enabled,
            upstream_stream_transport=payload.upstream_stream_transport,
            prohibit_fast_mode=payload.prohibit_fast_mode,
            http_downstream_transport_policy=payload.http_downstream_transport_policy,
            proxy_account_response_create_limit=payload.proxy_account_response_create_limit,
            clear_proxy_account_response_create_limit=payload.clear_proxy_account_response_create_limit,
            proxy_account_stream_limit=payload.proxy_account_stream_limit,
            clear_proxy_account_stream_limit=payload.clear_proxy_account_stream_limit,
            proxy_account_stream_recovery_reserve=payload.proxy_account_stream_recovery_reserve,
            clear_proxy_account_stream_recovery_reserve=payload.clear_proxy_account_stream_recovery_reserve,
            proxy_api_key_fair_share_congestion_threshold_pct=(
                payload.proxy_api_key_fair_share_congestion_threshold_pct
            ),
            clear_proxy_api_key_fair_share_congestion_threshold_pct=(
                payload.clear_proxy_api_key_fair_share_congestion_threshold_pct
            ),
            upstream_proxy_routing_enabled=payload.upstream_proxy_routing_enabled,
            upstream_proxy_default_pool_id=payload.upstream_proxy_default_pool_id,
            prefer_earlier_reset_accounts=payload.prefer_earlier_reset_accounts,
            prefer_earlier_reset_window=payload.prefer_earlier_reset_window,
            show_reset_credit_badges=payload.show_reset_credit_badges,
            auto_redeem_reset_credits_before_expiry=payload.auto_redeem_reset_credits_before_expiry,
            show_reset_credit_expiry_badge=payload.show_reset_credit_expiry_badge,
            routing_strategy=payload.routing_strategy,
            relative_availability_power=payload.relative_availability_power,
            relative_availability_top_k=payload.relative_availability_top_k,
            single_account_id=payload.single_account_id,
            subscription_overflow_source_id=payload.subscription_overflow_source_id,
            clear_subscription_overflow_source=payload.clear_subscription_overflow_source,
            subscription_overflow_drain_until=payload.subscription_overflow_drain_until,
            set_subscription_overflow_drain_until=payload.set_subscription_overflow_drain_until,
            openai_cache_affinity_max_age_seconds=payload.openai_cache_affinity_max_age_seconds,
            dashboard_session_ttl_seconds=payload.dashboard_session_ttl_seconds,
            http_responses_session_bridge_prompt_cache_idle_ttl_seconds=(
                payload.http_responses_session_bridge_prompt_cache_idle_ttl_seconds
            ),
            http_responses_session_bridge_gateway_safe_mode=payload.http_responses_session_bridge_gateway_safe_mode,
            sticky_reallocation_budget_threshold_pct=payload.sticky_reallocation_budget_threshold_pct,
            sticky_reallocation_primary_budget_threshold_pct=payload.sticky_reallocation_primary_budget_threshold_pct,
            sticky_reallocation_secondary_budget_threshold_pct=payload.sticky_reallocation_secondary_budget_threshold_pct,
            additional_quota_routing_policies_json=_dump_additional_quota_routing_policies(
                payload.additional_quota_routing_policies
            ),
            warmup_model=payload.warmup_model,
            import_without_overwrite=payload.import_without_overwrite,
            totp_required_on_login=payload.totp_required_on_login,
            api_key_auth_enabled=payload.api_key_auth_enabled,
            hide_upstream_quota_from_api_keys=payload.hide_upstream_quota_from_api_keys,
            limit_warmup_enabled=payload.limit_warmup_enabled,
            limit_warmup_windows=payload.limit_warmup_windows,
            limit_warmup_model=payload.limit_warmup_model,
            limit_warmup_prompt=payload.limit_warmup_prompt,
            limit_warmup_cooldown_seconds=payload.limit_warmup_cooldown_seconds,
            limit_warmup_exhausted_threshold_percent=payload.limit_warmup_exhausted_threshold_percent,
            limit_warmup_idle_threshold_percent=payload.limit_warmup_idle_threshold_percent,
            limit_warmup_min_available_percent=payload.limit_warmup_min_available_percent,
            weekly_pace_working_days=payload.weekly_pace_working_days,
            weekly_pace_smoothing_minutes=payload.weekly_pace_smoothing_minutes,
            guest_access_enabled=payload.guest_access_enabled,
            limit_warmup_staggered_idle_enabled=payload.limit_warmup_staggered_idle_enabled,
            request_log_retention_days=payload.request_log_retention_override_days,
            usage_history_retention_days=payload.usage_history_retention_override_days,
            clear_request_log_retention=payload.clear_request_log_retention_override,
            clear_usage_history_retention=payload.clear_usage_history_retention_override,
        )
        return _settings_data(row)


_ROUTING_POLICIES = frozenset({"inherit", "normal", "burn_first", "preserve"})

# Inheritable settings with an environment fallback: the ``dashboard_settings``
# column, the ``Settings`` field and the provenance key share one name.
_ENVIRONMENT_INHERITABLE_SETTINGS = (
    "proxy_account_response_create_limit",
    "proxy_account_stream_limit",
    "proxy_account_stream_recovery_reserve",
    "proxy_api_key_fair_share_congestion_threshold_pct",
)
# Retention has no environment fallback: NULL = never set from the dashboard =
# disabled; 0 = explicitly disabled.
_RETENTION_DISABLED_DAYS = 0


def _resolve_environment_inheritable(row: DashboardSettings, name: str) -> InheritableValue[int]:
    return resolve_inheritable(
        getattr(row, name),
        getattr(get_settings(), name),
        Settings.model_fields[name].default,
    )


def _resolve_inheritable_settings(row: DashboardSettings) -> dict[str, InheritableValue[int]]:
    resolved = {name: _resolve_environment_inheritable(row, name) for name in _ENVIRONMENT_INHERITABLE_SETTINGS}
    resolved["request_log_retention_days"] = resolve_inheritable(
        row.request_log_retention_days, None, _RETENTION_DISABLED_DAYS
    )
    resolved["usage_history_retention_days"] = resolve_inheritable(
        row.usage_history_retention_days, None, _RETENTION_DISABLED_DAYS
    )
    return resolved


def _settings_data(row: DashboardSettings) -> DashboardSettingsData:
    resolved = _resolve_inheritable_settings(row)
    return DashboardSettingsData(
        sticky_threads_enabled=row.sticky_threads_enabled,
        upstream_stream_transport=row.upstream_stream_transport,
        prohibit_fast_mode=row.prohibit_fast_mode,
        http_downstream_transport_policy=row.http_downstream_transport_policy,
        proxy_account_response_create_limit=resolved["proxy_account_response_create_limit"].value,
        proxy_account_response_create_limit_override=row.proxy_account_response_create_limit,
        proxy_account_stream_limit=resolved["proxy_account_stream_limit"].value,
        proxy_account_stream_limit_override=row.proxy_account_stream_limit,
        proxy_account_stream_recovery_reserve=resolved["proxy_account_stream_recovery_reserve"].value,
        proxy_account_stream_recovery_reserve_override=row.proxy_account_stream_recovery_reserve,
        proxy_api_key_fair_share_congestion_threshold_pct=(
            resolved["proxy_api_key_fair_share_congestion_threshold_pct"].value
        ),
        proxy_api_key_fair_share_congestion_threshold_pct_override=(
            row.proxy_api_key_fair_share_congestion_threshold_pct
        ),
        upstream_proxy_routing_enabled=row.upstream_proxy_routing_enabled,
        upstream_proxy_default_pool_id=row.upstream_proxy_default_pool_id,
        prefer_earlier_reset_accounts=row.prefer_earlier_reset_accounts,
        prefer_earlier_reset_window=row.prefer_earlier_reset_window,
        show_reset_credit_badges=row.show_reset_credit_badges,
        auto_redeem_reset_credits_before_expiry=row.auto_redeem_reset_credits_before_expiry,
        show_reset_credit_expiry_badge=row.show_reset_credit_expiry_badge,
        routing_strategy=row.routing_strategy,
        relative_availability_power=row.relative_availability_power,
        relative_availability_top_k=row.relative_availability_top_k,
        single_account_id=row.single_account_id,
        subscription_overflow_source_id=row.subscription_overflow_source_id,
        subscription_overflow_drain_until=row.subscription_overflow_drain_until,
        openai_cache_affinity_max_age_seconds=row.openai_cache_affinity_max_age_seconds,
        dashboard_session_ttl_seconds=row.dashboard_session_ttl_seconds,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=(
            row.http_responses_session_bridge_prompt_cache_idle_ttl_seconds
        ),
        http_responses_session_bridge_gateway_safe_mode=row.http_responses_session_bridge_gateway_safe_mode,
        sticky_reallocation_budget_threshold_pct=row.sticky_reallocation_budget_threshold_pct,
        sticky_reallocation_primary_budget_threshold_pct=row.sticky_reallocation_primary_budget_threshold_pct,
        sticky_reallocation_secondary_budget_threshold_pct=row.sticky_reallocation_secondary_budget_threshold_pct,
        additional_quota_routing_policies=_parse_additional_quota_routing_policies(
            row.additional_quota_routing_policies_json
        ),
        warmup_model=row.warmup_model,
        import_without_overwrite=row.import_without_overwrite,
        totp_required_on_login=row.totp_required_on_login,
        totp_configured=row.totp_secret_encrypted is not None,
        api_key_auth_enabled=row.api_key_auth_enabled,
        hide_upstream_quota_from_api_keys=row.hide_upstream_quota_from_api_keys,
        limit_warmup_enabled=row.limit_warmup_enabled,
        limit_warmup_windows=row.limit_warmup_windows,
        limit_warmup_model=row.limit_warmup_model,
        limit_warmup_prompt=row.limit_warmup_prompt,
        limit_warmup_cooldown_seconds=row.limit_warmup_cooldown_seconds,
        limit_warmup_exhausted_threshold_percent=row.limit_warmup_exhausted_threshold_percent,
        limit_warmup_idle_threshold_percent=row.limit_warmup_idle_threshold_percent,
        limit_warmup_min_available_percent=row.limit_warmup_min_available_percent,
        weekly_pace_working_days=row.weekly_pace_working_days,
        weekly_pace_smoothing_minutes=row.weekly_pace_smoothing_minutes,
        guest_access_enabled=row.guest_access_enabled,
        guest_password_configured=row.guest_password_hash is not None,
        limit_warmup_staggered_idle_enabled=row.limit_warmup_staggered_idle_enabled,
        request_log_retention_days=resolved["request_log_retention_days"].value,
        usage_history_retention_days=resolved["usage_history_retention_days"].value,
        request_log_retention_override_days=row.request_log_retention_days,
        usage_history_retention_override_days=row.usage_history_retention_days,
        version=row.version,
        provenance=resolved,
    )


def _parse_additional_quota_routing_policies(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    policies: dict[str, str] = {}
    for quota_key, policy in parsed.items():
        if not isinstance(quota_key, str) or not isinstance(policy, str):
            continue
        normalized_quota_key = normalize_additional_quota_key(quota_key)
        policy = policy.strip().lower()
        if normalized_quota_key and policy in _ROUTING_POLICIES:
            policies[normalized_quota_key] = policy
    return policies


def _dump_additional_quota_routing_policies(policies: dict[str, str]) -> str:
    normalized = {}
    for quota_key, policy in policies.items():
        if not isinstance(quota_key, str) or not isinstance(policy, str):
            continue
        normalized_quota_key = normalize_additional_quota_key(quota_key)
        normalized_policy = policy.strip().lower()
        if normalized_quota_key is not None and normalized_policy in _ROUTING_POLICIES:
            normalized[normalized_quota_key] = normalized_policy
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))

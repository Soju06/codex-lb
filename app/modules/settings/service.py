from __future__ import annotations

import json
from dataclasses import dataclass

from app.modules.settings.repository import SettingsRepository
from app.modules.usage.additional_quota_keys import (
    canonicalize_additional_quota_key,
    get_additional_quota_definition,
)


@dataclass(frozen=True, slots=True)
class DashboardSettingsData:
    sticky_threads_enabled: bool
    upstream_stream_transport: str
    upstream_proxy_routing_enabled: bool
    upstream_proxy_default_pool_id: str | None
    prefer_earlier_reset_accounts: bool
    prefer_earlier_reset_window: str
    routing_strategy: str
    relative_availability_power: float
    relative_availability_top_k: int
    single_account_id: str | None
    manual_account_priority_ids: tuple[str, ...]
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
    limit_warmup_enabled: bool
    limit_warmup_windows: str
    limit_warmup_model: str
    limit_warmup_prompt: str
    limit_warmup_cooldown_seconds: int
    limit_warmup_min_available_percent: float
    weekly_pace_working_days: str


@dataclass(frozen=True, slots=True)
class DashboardSettingsUpdateData:
    sticky_threads_enabled: bool
    upstream_stream_transport: str
    upstream_proxy_routing_enabled: bool
    upstream_proxy_default_pool_id: str | None
    prefer_earlier_reset_accounts: bool
    prefer_earlier_reset_window: str
    routing_strategy: str
    relative_availability_power: float
    relative_availability_top_k: int
    single_account_id: str | None
    manual_account_priority_ids: tuple[str, ...]
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
    limit_warmup_enabled: bool
    limit_warmup_windows: str
    limit_warmup_model: str
    limit_warmup_prompt: str
    limit_warmup_cooldown_seconds: int
    limit_warmup_min_available_percent: float
    weekly_pace_working_days: str


class SettingsService:
    def __init__(self, repository: SettingsRepository) -> None:
        self._repository = repository

    async def get_settings(self) -> DashboardSettingsData:
        row = await self._repository.get_or_create()
        return DashboardSettingsData(
            sticky_threads_enabled=row.sticky_threads_enabled,
            upstream_stream_transport=row.upstream_stream_transport,
            upstream_proxy_routing_enabled=row.upstream_proxy_routing_enabled,
            upstream_proxy_default_pool_id=row.upstream_proxy_default_pool_id,
            prefer_earlier_reset_accounts=row.prefer_earlier_reset_accounts,
            prefer_earlier_reset_window=row.prefer_earlier_reset_window,
            routing_strategy=row.routing_strategy,
            relative_availability_power=row.relative_availability_power,
            relative_availability_top_k=row.relative_availability_top_k,
            single_account_id=row.single_account_id,
            manual_account_priority_ids=_parse_manual_account_priority_ids(row.manual_account_priority_ids_json),
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
            limit_warmup_enabled=row.limit_warmup_enabled,
            limit_warmup_windows=row.limit_warmup_windows,
            limit_warmup_model=row.limit_warmup_model,
            limit_warmup_prompt=row.limit_warmup_prompt,
            limit_warmup_cooldown_seconds=row.limit_warmup_cooldown_seconds,
            limit_warmup_min_available_percent=row.limit_warmup_min_available_percent,
            weekly_pace_working_days=row.weekly_pace_working_days,
        )

    async def update_settings(self, payload: DashboardSettingsUpdateData) -> DashboardSettingsData:
        current = await self._repository.get_or_create()
        if payload.totp_required_on_login and current.totp_secret_encrypted is None:
            raise ValueError("Configure TOTP before enabling login enforcement")
        row = await self._repository.update(
            sticky_threads_enabled=payload.sticky_threads_enabled,
            upstream_stream_transport=payload.upstream_stream_transport,
            upstream_proxy_routing_enabled=payload.upstream_proxy_routing_enabled,
            upstream_proxy_default_pool_id=payload.upstream_proxy_default_pool_id,
            prefer_earlier_reset_accounts=payload.prefer_earlier_reset_accounts,
            prefer_earlier_reset_window=payload.prefer_earlier_reset_window,
            routing_strategy=payload.routing_strategy,
            relative_availability_power=payload.relative_availability_power,
            relative_availability_top_k=payload.relative_availability_top_k,
            single_account_id=payload.single_account_id,
            manual_account_priority_ids_json=_dump_manual_account_priority_ids(payload.manual_account_priority_ids),
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
            limit_warmup_enabled=payload.limit_warmup_enabled,
            limit_warmup_windows=payload.limit_warmup_windows,
            limit_warmup_model=payload.limit_warmup_model,
            limit_warmup_prompt=payload.limit_warmup_prompt,
            limit_warmup_cooldown_seconds=payload.limit_warmup_cooldown_seconds,
            limit_warmup_min_available_percent=payload.limit_warmup_min_available_percent,
            weekly_pace_working_days=payload.weekly_pace_working_days,
        )
        return DashboardSettingsData(
            sticky_threads_enabled=row.sticky_threads_enabled,
            upstream_stream_transport=row.upstream_stream_transport,
            upstream_proxy_routing_enabled=row.upstream_proxy_routing_enabled,
            upstream_proxy_default_pool_id=row.upstream_proxy_default_pool_id,
            prefer_earlier_reset_accounts=row.prefer_earlier_reset_accounts,
            prefer_earlier_reset_window=row.prefer_earlier_reset_window,
            routing_strategy=row.routing_strategy,
            relative_availability_power=row.relative_availability_power,
            relative_availability_top_k=row.relative_availability_top_k,
            single_account_id=row.single_account_id,
            manual_account_priority_ids=_parse_manual_account_priority_ids(row.manual_account_priority_ids_json),
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
            limit_warmup_enabled=row.limit_warmup_enabled,
            limit_warmup_windows=row.limit_warmup_windows,
            limit_warmup_model=row.limit_warmup_model,
            limit_warmup_prompt=row.limit_warmup_prompt,
            limit_warmup_cooldown_seconds=row.limit_warmup_cooldown_seconds,
            limit_warmup_min_available_percent=row.limit_warmup_min_available_percent,
            weekly_pace_working_days=row.weekly_pace_working_days,
        )


_ROUTING_POLICIES = frozenset({"inherit", "normal", "burn_first", "preserve"})


def _normalize_additional_quota_key(raw_quota_key: str) -> str | None:
    canonical_key = canonicalize_additional_quota_key(quota_key=raw_quota_key, limit_name=raw_quota_key)
    if canonical_key is None:
        return None
    if get_additional_quota_definition(canonical_key) is None:
        return None
    return canonical_key


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
        normalized_quota_key = _normalize_additional_quota_key(quota_key)
        policy = policy.strip().lower()
        if normalized_quota_key and policy in _ROUTING_POLICIES:
            policies[normalized_quota_key] = policy
    return policies


def _dump_additional_quota_routing_policies(policies: dict[str, str]) -> str:
    normalized = {}
    for quota_key, policy in policies.items():
        if not isinstance(quota_key, str) or not isinstance(policy, str):
            continue
        normalized_quota_key = _normalize_additional_quota_key(quota_key)
        normalized_policy = policy.strip().lower()
        if normalized_quota_key is not None and normalized_policy in _ROUTING_POLICIES:
            normalized[normalized_quota_key] = normalized_policy
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


def _parse_manual_account_priority_ids(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return ()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    seen: set[str] = set()
    ordered: list[str] = []
    for raw_account_id in parsed:
        if not isinstance(raw_account_id, str):
            continue
        account_id = raw_account_id.strip()
        if not account_id or account_id in seen:
            continue
        seen.add(account_id)
        ordered.append(account_id)
    return tuple(ordered)


def _dump_manual_account_priority_ids(account_ids: tuple[str, ...]) -> str:
    return json.dumps(list(account_ids), separators=(",", ":"))

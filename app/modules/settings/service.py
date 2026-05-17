from __future__ import annotations

import json
from dataclasses import dataclass

from app.modules.settings.repository import SettingsRepository
from app.modules.usage.additional_quota_keys import (
    ADDITIONAL_QUOTA_ROUTING_POLICIES,
    canonicalize_additional_quota_routing_policy,
    get_additional_quota_definition_for_key,
    get_additional_quota_routing_policy,
    list_additional_quota_definitions,
    normalize_additional_quota_routing_policy_overrides,
)


@dataclass(frozen=True, slots=True)
class AdditionalQuotaPolicyData:
    quota_key: str
    display_label: str
    routing_policy: str
    model_ids: list[str]


@dataclass(frozen=True, slots=True)
class DashboardSettingsData:
    sticky_threads_enabled: bool
    upstream_stream_transport: str
    prefer_earlier_reset_accounts: bool
    routing_strategy: str
    openai_cache_affinity_max_age_seconds: int
    dashboard_session_ttl_seconds: int
    http_responses_session_bridge_prompt_cache_idle_ttl_seconds: int
    http_responses_session_bridge_gateway_safe_mode: bool
    sticky_reallocation_budget_threshold_pct: float
    import_without_overwrite: bool
    totp_required_on_login: bool
    totp_configured: bool
    api_key_auth_enabled: bool
    additional_quota_routing_policies: dict[str, str]
    additional_quota_policies: list[AdditionalQuotaPolicyData]


@dataclass(frozen=True, slots=True)
class DashboardSettingsUpdateData:
    sticky_threads_enabled: bool
    upstream_stream_transport: str
    prefer_earlier_reset_accounts: bool
    routing_strategy: str
    openai_cache_affinity_max_age_seconds: int
    dashboard_session_ttl_seconds: int
    http_responses_session_bridge_prompt_cache_idle_ttl_seconds: int
    http_responses_session_bridge_gateway_safe_mode: bool
    sticky_reallocation_budget_threshold_pct: float
    import_without_overwrite: bool
    totp_required_on_login: bool
    api_key_auth_enabled: bool
    additional_quota_routing_policies: dict[str, str]


class AdditionalQuotaRoutingPolicyValidationError(ValueError):
    pass


def parse_additional_quota_routing_policies(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return normalize_additional_quota_routing_policy_overrides({str(key): str(value) for key, value in payload.items()})


def validate_additional_quota_routing_policies(policies: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    unknown_keys: list[str] = []
    invalid_policies: list[str] = []

    for key, value in policies.items():
        definition = get_additional_quota_definition_for_key(str(key))
        if definition is None:
            unknown_keys.append(str(key))
            continue

        routing_policy = canonicalize_additional_quota_routing_policy(str(value))
        if routing_policy is None:
            invalid_policies.append(f"{definition.quota_key}={value}")
            continue

        normalized[definition.quota_key] = routing_policy

    if unknown_keys or invalid_policies:
        details = []
        if unknown_keys:
            details.append(f"unknown quota keys: {', '.join(sorted(unknown_keys))}")
        if invalid_policies:
            details.append(f"invalid routing policies: {', '.join(sorted(invalid_policies))}")
        valid_keys = ", ".join(sorted(definition.quota_key for definition in list_additional_quota_definitions()))
        valid_policies = ", ".join(sorted(ADDITIONAL_QUOTA_ROUTING_POLICIES))
        details.append(f"valid quota keys: {valid_keys}")
        details.append(f"valid routing policies: {valid_policies}")
        raise AdditionalQuotaRoutingPolicyValidationError(
            "Invalid additional quota routing policies; " + "; ".join(details)
        )

    return normalized


def serialize_additional_quota_routing_policies(policies: dict[str, str]) -> str:
    return json.dumps(
        validate_additional_quota_routing_policies(policies),
        sort_keys=True,
        separators=(",", ":"),
    )


def build_additional_quota_policy_data(overrides: dict[str, str]) -> list[AdditionalQuotaPolicyData]:
    policies = []
    for definition in list_additional_quota_definitions():
        policies.append(
            AdditionalQuotaPolicyData(
                quota_key=definition.quota_key,
                display_label=definition.display_label,
                routing_policy=get_additional_quota_routing_policy(definition.quota_key, overrides=overrides),
                model_ids=sorted(definition.model_ids),
            )
        )
    return policies


class SettingsService:
    def __init__(self, repository: SettingsRepository) -> None:
        self._repository = repository

    async def get_settings(self) -> DashboardSettingsData:
        row = await self._repository.get_or_create()
        routing_policies = parse_additional_quota_routing_policies(row.additional_quota_routing_policies_json)
        return DashboardSettingsData(
            sticky_threads_enabled=row.sticky_threads_enabled,
            upstream_stream_transport=row.upstream_stream_transport,
            prefer_earlier_reset_accounts=row.prefer_earlier_reset_accounts,
            routing_strategy=row.routing_strategy,
            openai_cache_affinity_max_age_seconds=row.openai_cache_affinity_max_age_seconds,
            dashboard_session_ttl_seconds=row.dashboard_session_ttl_seconds,
            http_responses_session_bridge_prompt_cache_idle_ttl_seconds=(
                row.http_responses_session_bridge_prompt_cache_idle_ttl_seconds
            ),
            http_responses_session_bridge_gateway_safe_mode=row.http_responses_session_bridge_gateway_safe_mode,
            sticky_reallocation_budget_threshold_pct=row.sticky_reallocation_budget_threshold_pct,
            import_without_overwrite=row.import_without_overwrite,
            totp_required_on_login=row.totp_required_on_login,
            totp_configured=row.totp_secret_encrypted is not None,
            api_key_auth_enabled=row.api_key_auth_enabled,
            additional_quota_routing_policies=routing_policies,
            additional_quota_policies=build_additional_quota_policy_data(routing_policies),
        )

    async def update_settings(self, payload: DashboardSettingsUpdateData) -> DashboardSettingsData:
        current = await self._repository.get_or_create()
        if payload.totp_required_on_login and current.totp_secret_encrypted is None:
            raise ValueError("Configure TOTP before enabling login enforcement")
        row = await self._repository.update(
            sticky_threads_enabled=payload.sticky_threads_enabled,
            upstream_stream_transport=payload.upstream_stream_transport,
            prefer_earlier_reset_accounts=payload.prefer_earlier_reset_accounts,
            routing_strategy=payload.routing_strategy,
            openai_cache_affinity_max_age_seconds=payload.openai_cache_affinity_max_age_seconds,
            dashboard_session_ttl_seconds=payload.dashboard_session_ttl_seconds,
            http_responses_session_bridge_prompt_cache_idle_ttl_seconds=(
                payload.http_responses_session_bridge_prompt_cache_idle_ttl_seconds
            ),
            http_responses_session_bridge_gateway_safe_mode=payload.http_responses_session_bridge_gateway_safe_mode,
            sticky_reallocation_budget_threshold_pct=payload.sticky_reallocation_budget_threshold_pct,
            import_without_overwrite=payload.import_without_overwrite,
            totp_required_on_login=payload.totp_required_on_login,
            api_key_auth_enabled=payload.api_key_auth_enabled,
            additional_quota_routing_policies_json=serialize_additional_quota_routing_policies(
                payload.additional_quota_routing_policies
            ),
        )
        routing_policies = parse_additional_quota_routing_policies(row.additional_quota_routing_policies_json)
        return DashboardSettingsData(
            sticky_threads_enabled=row.sticky_threads_enabled,
            upstream_stream_transport=row.upstream_stream_transport,
            prefer_earlier_reset_accounts=row.prefer_earlier_reset_accounts,
            routing_strategy=row.routing_strategy,
            openai_cache_affinity_max_age_seconds=row.openai_cache_affinity_max_age_seconds,
            dashboard_session_ttl_seconds=row.dashboard_session_ttl_seconds,
            http_responses_session_bridge_prompt_cache_idle_ttl_seconds=(
                row.http_responses_session_bridge_prompt_cache_idle_ttl_seconds
            ),
            http_responses_session_bridge_gateway_safe_mode=row.http_responses_session_bridge_gateway_safe_mode,
            sticky_reallocation_budget_threshold_pct=row.sticky_reallocation_budget_threshold_pct,
            import_without_overwrite=row.import_without_overwrite,
            totp_required_on_login=row.totp_required_on_login,
            totp_configured=row.totp_secret_encrypted is not None,
            api_key_auth_enabled=row.api_key_auth_enabled,
            additional_quota_routing_policies=routing_policies,
            additional_quota_policies=build_additional_quota_policy_data(routing_policies),
        )

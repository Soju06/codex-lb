from __future__ import annotations

from dataclasses import dataclass

from app.modules.settings.repository import SettingsRepository


@dataclass(frozen=True, slots=True)
class DashboardSettingsData:
    sticky_threads_enabled: bool
    upstream_stream_transport: str
    prefer_earlier_reset_accounts: bool
    priorities_enabled: bool
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


@dataclass(frozen=True, slots=True)
class DashboardSettingsUpdateData:
    sticky_threads_enabled: bool
    upstream_stream_transport: str
    prefer_earlier_reset_accounts: bool
    priorities_enabled: bool
    routing_strategy: str
    openai_cache_affinity_max_age_seconds: int
    dashboard_session_ttl_seconds: int
    http_responses_session_bridge_prompt_cache_idle_ttl_seconds: int
    http_responses_session_bridge_gateway_safe_mode: bool
    sticky_reallocation_budget_threshold_pct: float
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
            upstream_stream_transport=row.upstream_stream_transport,
            prefer_earlier_reset_accounts=row.prefer_earlier_reset_accounts,
            priorities_enabled=row.priorities_enabled,
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
        )

    async def update_settings(self, payload: DashboardSettingsUpdateData) -> DashboardSettingsData:
        current = await self._repository.get_or_create()
        if payload.totp_required_on_login and current.totp_secret_encrypted is None:
            raise ValueError("Configure TOTP before enabling login enforcement")
        row = await self._repository.update(
            sticky_threads_enabled=payload.sticky_threads_enabled,
            upstream_stream_transport=payload.upstream_stream_transport,
            prefer_earlier_reset_accounts=payload.prefer_earlier_reset_accounts,
            priorities_enabled=payload.priorities_enabled,
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
        )
        return DashboardSettingsData(
            sticky_threads_enabled=row.sticky_threads_enabled,
            upstream_stream_transport=row.upstream_stream_transport,
            prefer_earlier_reset_accounts=row.prefer_earlier_reset_accounts,
            priorities_enabled=row.priorities_enabled,
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
        )

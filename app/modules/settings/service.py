from __future__ import annotations

from dataclasses import dataclass

from app.core.clients.upstream_proxy import normalize_upstream_proxy_url, redact_upstream_proxy_url
from app.core.crypto import TokenEncryptor
from app.modules.settings.repository import SettingsRepository


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
    upstream_proxy_configured: bool
    upstream_proxy_url: str | None


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
    upstream_proxy_url: str | None
    upstream_proxy_url_set: bool = False


class SettingsService:
    def __init__(self, repository: SettingsRepository) -> None:
        self._repository = repository
        self._encryptor = TokenEncryptor()

    async def get_settings(self) -> DashboardSettingsData:
        row = await self._repository.get_or_create()
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
            upstream_proxy_configured=row.upstream_proxy_url_encrypted is not None,
            upstream_proxy_url=redact_upstream_proxy_url(self._decrypt_optional(row.upstream_proxy_url_encrypted)),
        )

    async def update_settings(self, payload: DashboardSettingsUpdateData) -> DashboardSettingsData:
        current = await self._repository.get_or_create()
        if payload.totp_required_on_login and current.totp_secret_encrypted is None:
            raise ValueError("Configure TOTP before enabling login enforcement")
        if payload.upstream_proxy_url_set:
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
                upstream_proxy_url_encrypted=self._encrypt_optional_proxy_url(payload.upstream_proxy_url),
            )
        else:
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
            )
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
            upstream_proxy_configured=row.upstream_proxy_url_encrypted is not None,
            upstream_proxy_url=redact_upstream_proxy_url(self._decrypt_optional(row.upstream_proxy_url_encrypted)),
        )

    async def list_upstream_proxy_groups(self) -> list[tuple[str, str]]:
        groups = await self._repository.list_upstream_proxy_groups()
        return [
            (group.name, redact_upstream_proxy_url(self._encryptor.decrypt(group.proxy_url_encrypted)) or "")
            for group in groups
        ]

    async def upsert_upstream_proxy_group(self, name: str, proxy_url: str) -> tuple[str, str]:
        normalized_name = _normalize_group_name(name)
        encrypted = self._encryptor.encrypt(normalize_upstream_proxy_url(proxy_url))
        row = await self._repository.upsert_upstream_proxy_group(normalized_name, encrypted)
        return row.name, redact_upstream_proxy_url(self._encryptor.decrypt(row.proxy_url_encrypted)) or ""

    async def delete_upstream_proxy_group(self, name: str) -> bool:
        return await self._repository.delete_upstream_proxy_group(_normalize_group_name(name))

    def _encrypt_optional_proxy_url(self, proxy_url: str | None) -> bytes | None:
        if proxy_url is None:
            return None
        return self._encryptor.encrypt(normalize_upstream_proxy_url(proxy_url))

    def _decrypt_optional(self, encrypted: bytes | None) -> str | None:
        if encrypted is None:
            return None
        return self._encryptor.decrypt(encrypted)


def _normalize_group_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("Proxy group name is required")
    if len(normalized) > 100:
        raise ValueError("Proxy group name is too long")
    return normalized

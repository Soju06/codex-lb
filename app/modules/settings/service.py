from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.modules.settings.repository import SettingsRepository


_REQUEST_VISIBILITY_MODES = frozenset({"off", "persistent", "temporary"})


def request_visibility_capture_enabled_now(settings: object, *, now: datetime | None = None) -> bool:
    mode_value = getattr(settings, "request_visibility_mode", None)
    if isinstance(mode_value, str):
        mode = mode_value.strip().lower()
        if mode == "persistent":
            return True
        if mode == "temporary":
            expires_at = getattr(settings, "request_visibility_expires_at", None)
            if not isinstance(expires_at, datetime):
                return False
            current = now or datetime.now(UTC)
            if expires_at.tzinfo is None and current.tzinfo is not None:
                current = current.replace(tzinfo=None)
            return expires_at > current
        if mode in _REQUEST_VISIBILITY_MODES:
            return False

    return False


@dataclass(frozen=True, slots=True)
class DashboardSettingsData:
    sticky_threads_enabled: bool
    upstream_stream_transport: str
    prefer_earlier_reset_accounts: bool
    routing_strategy: str
    openai_cache_affinity_max_age_seconds: int
    http_responses_session_bridge_prompt_cache_idle_ttl_seconds: int
    sticky_reallocation_budget_threshold_pct: float
    import_without_overwrite: bool
    totp_required_on_login: bool
    totp_configured: bool
    api_key_auth_enabled: bool
    request_visibility_mode: str
    request_visibility_expires_at: datetime | None
    request_visibility_enabled: bool


@dataclass(frozen=True, slots=True)
class DashboardSettingsUpdateData:
    sticky_threads_enabled: bool
    upstream_stream_transport: str
    prefer_earlier_reset_accounts: bool
    routing_strategy: str
    openai_cache_affinity_max_age_seconds: int
    http_responses_session_bridge_prompt_cache_idle_ttl_seconds: int
    sticky_reallocation_budget_threshold_pct: float
    import_without_overwrite: bool
    totp_required_on_login: bool
    api_key_auth_enabled: bool
    request_visibility_mode: str
    request_visibility_expires_at: datetime | None
    request_visibility_duration_minutes: int | None = None


class SettingsService:
    def __init__(self, repository: SettingsRepository) -> None:
        self._repository = repository

    async def get_settings(self) -> DashboardSettingsData:
        row = await self._repository.get_or_create()
        return DashboardSettingsData(
            sticky_threads_enabled=row.sticky_threads_enabled,
            upstream_stream_transport=row.upstream_stream_transport,
            prefer_earlier_reset_accounts=row.prefer_earlier_reset_accounts,
            routing_strategy=row.routing_strategy,
            openai_cache_affinity_max_age_seconds=row.openai_cache_affinity_max_age_seconds,
            http_responses_session_bridge_prompt_cache_idle_ttl_seconds=row.http_responses_session_bridge_prompt_cache_idle_ttl_seconds,
            sticky_reallocation_budget_threshold_pct=row.sticky_reallocation_budget_threshold_pct,
            import_without_overwrite=row.import_without_overwrite,
            totp_required_on_login=row.totp_required_on_login,
            totp_configured=row.totp_secret_encrypted is not None,
            api_key_auth_enabled=row.api_key_auth_enabled,
            request_visibility_mode=row.request_visibility_mode,
            request_visibility_expires_at=row.request_visibility_expires_at,
            request_visibility_enabled=request_visibility_capture_enabled_now(row),
        )

    async def update_settings(self, payload: DashboardSettingsUpdateData) -> DashboardSettingsData:
        current = await self._repository.get_or_create()
        if payload.totp_required_on_login and current.totp_secret_encrypted is None:
            raise ValueError("Configure TOTP before enabling login enforcement")
        request_visibility_expires_at = payload.request_visibility_expires_at
        if payload.request_visibility_mode == "temporary":
            if payload.request_visibility_duration_minutes is not None:
                request_visibility_expires_at = datetime.now(UTC) + timedelta(
                    minutes=payload.request_visibility_duration_minutes
                )
            elif request_visibility_expires_at is None:
                raise ValueError("Temporary request visibility requires a duration")
        else:
            request_visibility_expires_at = None
        row = await self._repository.update(
            sticky_threads_enabled=payload.sticky_threads_enabled,
            upstream_stream_transport=payload.upstream_stream_transport,
            prefer_earlier_reset_accounts=payload.prefer_earlier_reset_accounts,
            routing_strategy=payload.routing_strategy,
            openai_cache_affinity_max_age_seconds=payload.openai_cache_affinity_max_age_seconds,
            http_responses_session_bridge_prompt_cache_idle_ttl_seconds=payload.http_responses_session_bridge_prompt_cache_idle_ttl_seconds,
            sticky_reallocation_budget_threshold_pct=payload.sticky_reallocation_budget_threshold_pct,
            import_without_overwrite=payload.import_without_overwrite,
            totp_required_on_login=payload.totp_required_on_login,
            api_key_auth_enabled=payload.api_key_auth_enabled,
            request_visibility_mode=payload.request_visibility_mode,
            request_visibility_expires_at=request_visibility_expires_at,
        )
        return DashboardSettingsData(
            sticky_threads_enabled=row.sticky_threads_enabled,
            upstream_stream_transport=row.upstream_stream_transport,
            prefer_earlier_reset_accounts=row.prefer_earlier_reset_accounts,
            routing_strategy=row.routing_strategy,
            openai_cache_affinity_max_age_seconds=row.openai_cache_affinity_max_age_seconds,
            http_responses_session_bridge_prompt_cache_idle_ttl_seconds=row.http_responses_session_bridge_prompt_cache_idle_ttl_seconds,
            sticky_reallocation_budget_threshold_pct=row.sticky_reallocation_budget_threshold_pct,
            import_without_overwrite=row.import_without_overwrite,
            totp_required_on_login=row.totp_required_on_login,
            totp_configured=row.totp_secret_encrypted is not None,
            api_key_auth_enabled=row.api_key_auth_enabled,
            request_visibility_mode=row.request_visibility_mode,
            request_visibility_expires_at=row.request_visibility_expires_at,
            request_visibility_enabled=request_visibility_capture_enabled_now(row),
        )

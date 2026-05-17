from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.settings import get_settings
from app.db.models import DashboardSettings, UpstreamProxyGroup

_SETTINGS_ID = 1


class _Unset:
    pass


_UNSET = _Unset()


class SettingsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(self) -> DashboardSettings:
        existing = await self._session.get(DashboardSettings, _SETTINGS_ID)
        if existing is not None:
            return existing

        row = DashboardSettings(
            id=_SETTINGS_ID,
            sticky_threads_enabled=True,
            upstream_stream_transport="default",
            prefer_earlier_reset_accounts=True,
            routing_strategy="capacity_weighted",
            openai_cache_affinity_max_age_seconds=get_settings().openai_cache_affinity_max_age_seconds,
            dashboard_session_ttl_seconds=43200,
            import_without_overwrite=True,
            totp_required_on_login=False,
            password_hash=None,
            bootstrap_token_encrypted=None,
            bootstrap_token_hash=None,
            api_key_auth_enabled=False,
            totp_secret_encrypted=None,
            totp_last_verified_step=None,
        )
        self._session.add(row)
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            existing = await self._session.get(DashboardSettings, _SETTINGS_ID)
            if existing is None:
                raise
            return existing
        await self._session.refresh(row)
        return row

    async def update(
        self,
        *,
        sticky_threads_enabled: bool | None = None,
        upstream_stream_transport: str | None = None,
        prefer_earlier_reset_accounts: bool | None = None,
        routing_strategy: str | None = None,
        openai_cache_affinity_max_age_seconds: int | None = None,
        dashboard_session_ttl_seconds: int | None = None,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds: int | None = None,
        http_responses_session_bridge_gateway_safe_mode: bool | None = None,
        sticky_reallocation_budget_threshold_pct: float | None = None,
        import_without_overwrite: bool | None = None,
        totp_required_on_login: bool | None = None,
        api_key_auth_enabled: bool | None = None,
        upstream_proxy_url_encrypted: bytes | None | _Unset = _UNSET,
    ) -> DashboardSettings:
        settings = await self.get_or_create()
        if sticky_threads_enabled is not None:
            settings.sticky_threads_enabled = sticky_threads_enabled
        if upstream_stream_transport is not None:
            settings.upstream_stream_transport = upstream_stream_transport
        if prefer_earlier_reset_accounts is not None:
            settings.prefer_earlier_reset_accounts = prefer_earlier_reset_accounts
        if routing_strategy is not None:
            settings.routing_strategy = routing_strategy
        if openai_cache_affinity_max_age_seconds is not None:
            settings.openai_cache_affinity_max_age_seconds = openai_cache_affinity_max_age_seconds
        if dashboard_session_ttl_seconds is not None:
            settings.dashboard_session_ttl_seconds = dashboard_session_ttl_seconds
        if http_responses_session_bridge_prompt_cache_idle_ttl_seconds is not None:
            settings.http_responses_session_bridge_prompt_cache_idle_ttl_seconds = (
                http_responses_session_bridge_prompt_cache_idle_ttl_seconds
            )
        if http_responses_session_bridge_gateway_safe_mode is not None:
            settings.http_responses_session_bridge_gateway_safe_mode = http_responses_session_bridge_gateway_safe_mode
        if sticky_reallocation_budget_threshold_pct is not None:
            settings.sticky_reallocation_budget_threshold_pct = sticky_reallocation_budget_threshold_pct
        if import_without_overwrite is not None:
            settings.import_without_overwrite = import_without_overwrite
        if totp_required_on_login is not None:
            settings.totp_required_on_login = totp_required_on_login
        if api_key_auth_enabled is not None:
            settings.api_key_auth_enabled = api_key_auth_enabled
        if not isinstance(upstream_proxy_url_encrypted, _Unset):
            settings.upstream_proxy_url_encrypted = upstream_proxy_url_encrypted
        await self.commit_refresh(settings)
        return settings

    async def commit_refresh(self, settings: DashboardSettings) -> None:
        await self._session.commit()
        await self._session.refresh(settings)

    async def list_upstream_proxy_groups(self) -> list[UpstreamProxyGroup]:
        result = await self._session.execute(select(UpstreamProxyGroup).order_by(UpstreamProxyGroup.name))
        return list(result.scalars().all())

    async def get_upstream_proxy_group(self, name: str) -> UpstreamProxyGroup | None:
        return await self._session.get(UpstreamProxyGroup, name)

    async def upsert_upstream_proxy_group(self, name: str, proxy_url_encrypted: bytes) -> UpstreamProxyGroup:
        row = await self._session.get(UpstreamProxyGroup, name)
        if row is None:
            row = UpstreamProxyGroup(name=name, proxy_url_encrypted=proxy_url_encrypted)
            self._session.add(row)
        else:
            row.proxy_url_encrypted = proxy_url_encrypted
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def delete_upstream_proxy_group(self, name: str) -> bool:
        result = await self._session.execute(
            delete(UpstreamProxyGroup).where(UpstreamProxyGroup.name == name).returning(UpstreamProxyGroup.name)
        )
        await self._session.commit()
        return result.scalar_one_or_none() is not None

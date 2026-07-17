from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm.exc import StaleDataError

from app.core.auth.dashboard_session_ttl import DEFAULT_DASHBOARD_SESSION_TTL_SECONDS
from app.core.config.settings import get_settings
from app.core.exceptions import DashboardSettingsConflictError
from app.db.models import DashboardSettings

_SETTINGS_ID = 1


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
            prohibit_fast_mode=False,
            http_downstream_transport_policy=get_settings().http_downstream_transport_policy,
            proxy_account_response_create_limit=get_settings().proxy_account_response_create_limit,
            proxy_account_stream_limit=get_settings().proxy_account_stream_limit,
            proxy_account_stream_recovery_reserve=get_settings().proxy_account_stream_recovery_reserve,
            upstream_proxy_routing_enabled=False,
            upstream_proxy_default_pool_id=None,
            prefer_earlier_reset_accounts=True,
            prefer_earlier_reset_window="secondary",
            show_reset_credit_badges=True,
            auto_redeem_reset_credits_before_expiry=False,
            show_reset_credit_expiry_badge=True,
            routing_strategy="capacity_weighted",
            relative_availability_power=2.0,
            relative_availability_top_k=5,
            single_account_id=None,
            openai_cache_affinity_max_age_seconds=get_settings().openai_cache_affinity_max_age_seconds,
            dashboard_session_ttl_seconds=DEFAULT_DASHBOARD_SESSION_TTL_SECONDS,
            warmup_model=get_settings().warmup_model,
            import_without_overwrite=True,
            totp_required_on_login=False,
            password_hash=None,
            guest_access_enabled=False,
            guest_password_hash=None,
            bootstrap_token_encrypted=None,
            bootstrap_token_hash=None,
            api_key_auth_enabled=False,
            hide_upstream_quota_from_api_keys=False,
            totp_secret_encrypted=None,
            totp_last_verified_step=None,
            sticky_reallocation_primary_budget_threshold_pct=95.0,
            sticky_reallocation_secondary_budget_threshold_pct=100.0,
            additional_quota_routing_policies_json="{}",
            limit_warmup_enabled=False,
            limit_warmup_windows="both",
            limit_warmup_model="auto",
            limit_warmup_prompt="Say OK.",
            limit_warmup_cooldown_seconds=3600,
            limit_warmup_exhausted_threshold_percent=99.0,
            limit_warmup_idle_threshold_percent=1.0,
            limit_warmup_min_available_percent=100.0,
            weekly_pace_working_days="0,1,2,3,4,5,6",
            weekly_pace_smoothing_minutes=30,
            limit_warmup_staggered_idle_enabled=False,
            request_log_retention_days=None,
            usage_history_retention_days=None,
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
        prohibit_fast_mode: bool | None = None,
        http_downstream_transport_policy: str | None = None,
        proxy_account_response_create_limit: int | None = None,
        proxy_account_stream_limit: int | None = None,
        proxy_account_stream_recovery_reserve: int | None = None,
        upstream_proxy_routing_enabled: bool | None = None,
        upstream_proxy_default_pool_id: str | None = None,
        prefer_earlier_reset_accounts: bool | None = None,
        prefer_earlier_reset_window: str | None = None,
        show_reset_credit_badges: bool | None = None,
        auto_redeem_reset_credits_before_expiry: bool | None = None,
        show_reset_credit_expiry_badge: bool | None = None,
        routing_strategy: str | None = None,
        relative_availability_power: float | None = None,
        relative_availability_top_k: int | None = None,
        single_account_id: str | None = None,
        openai_cache_affinity_max_age_seconds: int | None = None,
        dashboard_session_ttl_seconds: int | None = None,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds: int | None = None,
        http_responses_session_bridge_gateway_safe_mode: bool | None = None,
        sticky_reallocation_budget_threshold_pct: float | None = None,
        sticky_reallocation_primary_budget_threshold_pct: float | None = None,
        sticky_reallocation_secondary_budget_threshold_pct: float | None = None,
        additional_quota_routing_policies_json: str | None = None,
        warmup_model: str | None = None,
        import_without_overwrite: bool | None = None,
        totp_required_on_login: bool | None = None,
        api_key_auth_enabled: bool | None = None,
        hide_upstream_quota_from_api_keys: bool | None = None,
        limit_warmup_enabled: bool | None = None,
        limit_warmup_windows: str | None = None,
        limit_warmup_model: str | None = None,
        limit_warmup_prompt: str | None = None,
        limit_warmup_cooldown_seconds: int | None = None,
        limit_warmup_exhausted_threshold_percent: float | None = None,
        limit_warmup_idle_threshold_percent: float | None = None,
        limit_warmup_min_available_percent: float | None = None,
        weekly_pace_working_days: str | None = None,
        weekly_pace_smoothing_minutes: int | None = None,
        guest_access_enabled: bool | None = None,
        limit_warmup_staggered_idle_enabled: bool | None = None,
        request_log_retention_days: int | None = None,
        usage_history_retention_days: int | None = None,
        clear_request_log_retention: bool = False,
        clear_usage_history_retention: bool = False,
        expected_version: int | None = None,
    ) -> DashboardSettings:
        settings = await self.get_or_create()
        if expected_version is not None and settings.version != expected_version:
            # Bind the CAS to the row this UPDATE targets: with
            # DashboardSettings.version as version_id_col, commit_refresh emits
            # `UPDATE ... WHERE version = :expected`, so a writer committing in
            # between still surfaces as StaleDataError -> 409.
            raise DashboardSettingsConflictError(
                "Settings were modified since this form was loaded; reload and retry",
            )
        if sticky_threads_enabled is not None:
            settings.sticky_threads_enabled = sticky_threads_enabled
        if upstream_stream_transport is not None:
            settings.upstream_stream_transport = upstream_stream_transport
        if prohibit_fast_mode is not None:
            settings.prohibit_fast_mode = prohibit_fast_mode
        if http_downstream_transport_policy is not None:
            settings.http_downstream_transport_policy = http_downstream_transport_policy
        if proxy_account_response_create_limit is not None:
            settings.proxy_account_response_create_limit = proxy_account_response_create_limit
        if proxy_account_stream_limit is not None:
            settings.proxy_account_stream_limit = proxy_account_stream_limit
        if proxy_account_stream_recovery_reserve is not None:
            settings.proxy_account_stream_recovery_reserve = proxy_account_stream_recovery_reserve
        if upstream_proxy_routing_enabled is not None:
            settings.upstream_proxy_routing_enabled = upstream_proxy_routing_enabled
        settings.upstream_proxy_default_pool_id = upstream_proxy_default_pool_id or None
        if prefer_earlier_reset_accounts is not None:
            settings.prefer_earlier_reset_accounts = prefer_earlier_reset_accounts
        if prefer_earlier_reset_window is not None:
            settings.prefer_earlier_reset_window = prefer_earlier_reset_window
        if show_reset_credit_badges is not None:
            settings.show_reset_credit_badges = show_reset_credit_badges
        if auto_redeem_reset_credits_before_expiry is not None:
            settings.auto_redeem_reset_credits_before_expiry = auto_redeem_reset_credits_before_expiry
        if show_reset_credit_expiry_badge is not None:
            settings.show_reset_credit_expiry_badge = show_reset_credit_expiry_badge
        if routing_strategy is not None:
            settings.routing_strategy = routing_strategy
        if relative_availability_power is not None:
            settings.relative_availability_power = relative_availability_power
        if relative_availability_top_k is not None:
            settings.relative_availability_top_k = relative_availability_top_k
        if single_account_id is not None or routing_strategy == "single_account":
            settings.single_account_id = single_account_id
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
        if sticky_reallocation_primary_budget_threshold_pct is not None:
            settings.sticky_reallocation_primary_budget_threshold_pct = sticky_reallocation_primary_budget_threshold_pct
        if sticky_reallocation_secondary_budget_threshold_pct is not None:
            settings.sticky_reallocation_secondary_budget_threshold_pct = (
                sticky_reallocation_secondary_budget_threshold_pct
            )
        if additional_quota_routing_policies_json is not None:
            settings.additional_quota_routing_policies_json = additional_quota_routing_policies_json
        if warmup_model is not None:
            settings.warmup_model = warmup_model
        if import_without_overwrite is not None:
            settings.import_without_overwrite = import_without_overwrite
        if totp_required_on_login is not None:
            settings.totp_required_on_login = totp_required_on_login
        if api_key_auth_enabled is not None:
            settings.api_key_auth_enabled = api_key_auth_enabled
        if hide_upstream_quota_from_api_keys is not None:
            settings.hide_upstream_quota_from_api_keys = hide_upstream_quota_from_api_keys
        if limit_warmup_enabled is not None:
            settings.limit_warmup_enabled = limit_warmup_enabled
        if limit_warmup_windows is not None:
            settings.limit_warmup_windows = limit_warmup_windows
        if limit_warmup_model is not None:
            settings.limit_warmup_model = limit_warmup_model
        if limit_warmup_prompt is not None:
            settings.limit_warmup_prompt = limit_warmup_prompt
        if limit_warmup_cooldown_seconds is not None:
            settings.limit_warmup_cooldown_seconds = limit_warmup_cooldown_seconds
        if limit_warmup_exhausted_threshold_percent is not None:
            settings.limit_warmup_exhausted_threshold_percent = limit_warmup_exhausted_threshold_percent
        if limit_warmup_idle_threshold_percent is not None:
            settings.limit_warmup_idle_threshold_percent = limit_warmup_idle_threshold_percent
        if limit_warmup_min_available_percent is not None:
            settings.limit_warmup_min_available_percent = limit_warmup_min_available_percent
        if weekly_pace_working_days is not None:
            settings.weekly_pace_working_days = weekly_pace_working_days
        if weekly_pace_smoothing_minutes is not None:
            settings.weekly_pace_smoothing_minutes = weekly_pace_smoothing_minutes
        if guest_access_enabled is not None:
            settings.guest_access_enabled = guest_access_enabled
        if limit_warmup_staggered_idle_enabled is not None:
            settings.limit_warmup_staggered_idle_enabled = limit_warmup_staggered_idle_enabled
        # Retention overrides are tri-state: a clear flag resets the column to
        # NULL (inherit the deprecated env alias); a non-None value stores an
        # override; neither leaves the stored value untouched.
        if clear_request_log_retention:
            settings.request_log_retention_days = None
        elif request_log_retention_days is not None:
            settings.request_log_retention_days = request_log_retention_days
        if clear_usage_history_retention:
            settings.usage_history_retention_days = None
        elif usage_history_retention_days is not None:
            settings.usage_history_retention_days = usage_history_retention_days
        # Force the optimistic-version CAS to run even when the payload makes no
        # net change. `version_id_col` only raises `StaleDataError` when the
        # flush emits an ORM UPDATE; a full-row save that assigns values all
        # equal to this (possibly stale) session's row would otherwise flush
        # nothing, commit silently, and refresh over a concurrent writer's
        # values without the required 409. Flagging a column dirty guarantees an
        # `UPDATE ... SET version = version + 1 WHERE version = :expected`, so a
        # stale no-op save still surfaces the conflict.
        flag_modified(settings, "sticky_threads_enabled")
        await self.commit_refresh(settings)
        return settings

    async def commit_refresh(self, settings: DashboardSettings) -> None:
        try:
            await self._session.commit()
        except StaleDataError as exc:
            # The optimistic version check (DashboardSettings.version) matched
            # zero rows: another writer (replica or request) committed first.
            await self._session.rollback()
            raise DashboardSettingsConflictError() from exc
        await self._session.refresh(settings)

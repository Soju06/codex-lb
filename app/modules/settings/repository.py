from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm.exc import StaleDataError

from app.core.auth.dashboard_session_ttl import DEFAULT_DASHBOARD_SESSION_TTL_SECONDS
from app.core.exceptions import DashboardSettingsConflictError
from app.core.upstream_proxy.cache import get_upstream_route_cache
from app.db.models import DashboardSettings, ModelContextWindowOverride

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
            upstream_stream_transport="auto",
            prohibit_fast_mode=False,
            # Account-capacity overrides are tri-state: NULL inherits the
            # process environment value at read time. The first-boot seed must
            # stay NULL — copying the env value here would freeze it as a
            # dashboard override while the UI keeps labelling the (possibly
            # changed) env value as the inherited baseline.
            proxy_account_response_create_limit=None,
            proxy_account_stream_limit=None,
            proxy_account_stream_recovery_reserve=None,
            proxy_api_key_fair_share_congestion_threshold_pct=None,
            # C2-2 routing/overload: same tri-state rule, seeded NULL.
            proxy_overload_isolation_seconds=None,
            proxy_account_error_rate_weighting_enabled=None,
            proxy_account_inflight_penalty_pct=None,
            proxy_account_lease_token_weight=None,
            proxy_account_lease_ttl_seconds=None,
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
            subscription_overflow_source_id=None,
            subscription_overflow_drain_until=None,
            dashboard_session_ttl_seconds=DEFAULT_DASHBOARD_SESSION_TTL_SECONDS,
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
            # C2-3 resilience toggles: NULL = inherit the env alias / default.
            soft_drain_enabled=None,
            deterministic_failover_enabled=None,
            circuit_breaker_enabled=None,
            # M3 codex prewarm: NULL = inherit the env alias / default (off).
            http_responses_session_bridge_codex_prewarm_enabled=None,
            # M2 background jobs: NULL = inherit the env alias / default.
            auth_guardian_enabled=None,
            automations_scheduler_enabled=None,
            rate_limit_reset_credits_refresh_enabled=None,
            # M5 conversation archive: NULL = inherit the env alias / default.
            conversation_archive_enabled=None,
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
        clear_proxy_account_response_create_limit: bool = False,
        proxy_account_stream_limit: int | None = None,
        clear_proxy_account_stream_limit: bool = False,
        proxy_account_stream_recovery_reserve: int | None = None,
        clear_proxy_account_stream_recovery_reserve: bool = False,
        proxy_api_key_fair_share_congestion_threshold_pct: int | None = None,
        clear_proxy_api_key_fair_share_congestion_threshold_pct: bool = False,
        # C2-2 routing/overload
        proxy_overload_isolation_seconds: int | None = None,
        clear_proxy_overload_isolation_seconds: bool = False,
        proxy_account_error_rate_weighting_enabled: bool | None = None,
        clear_proxy_account_error_rate_weighting_enabled: bool = False,
        proxy_account_inflight_penalty_pct: float | None = None,
        clear_proxy_account_inflight_penalty_pct: bool = False,
        proxy_account_lease_token_weight: float | None = None,
        clear_proxy_account_lease_token_weight: bool = False,
        proxy_account_lease_ttl_seconds: float | None = None,
        clear_proxy_account_lease_ttl_seconds: bool = False,
        # end C2-2 routing/overload
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
        subscription_overflow_source_id: str | None = None,
        clear_subscription_overflow_source: bool = False,
        subscription_overflow_drain_until: datetime | None = None,
        set_subscription_overflow_drain_until: bool = False,
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
        # C2-3 resilience toggles (tri-state like the retention overrides)
        soft_drain_enabled: bool | None = None,
        clear_soft_drain_enabled: bool = False,
        deterministic_failover_enabled: bool | None = None,
        clear_deterministic_failover_enabled: bool = False,
        circuit_breaker_enabled: bool | None = None,
        clear_circuit_breaker_enabled: bool = False,
        # M2 background jobs (tri-state like the resilience toggles)
        auth_guardian_enabled: bool | None = None,
        clear_auth_guardian_enabled: bool = False,
        automations_scheduler_enabled: bool | None = None,
        clear_automations_scheduler_enabled: bool = False,
        rate_limit_reset_credits_refresh_enabled: bool | None = None,
        clear_rate_limit_reset_credits_refresh_enabled: bool = False,
        # M5 conversation archive (tri-state like the resilience toggles)
        conversation_archive_enabled: bool | None = None,
        clear_conversation_archive_enabled: bool = False,
        # end M5 conversation archive
        # C2-1 timeouts (tri-state: value = store, clear flag = back to NULL /
        # inherit, neither = untouched).
        upstream_connect_timeout_seconds: float | None = None,
        clear_upstream_connect_timeout_seconds: bool = False,
        proxy_request_budget_seconds: float | None = None,
        clear_proxy_request_budget_seconds: bool = False,
        compact_request_budget_seconds: float | None = None,
        clear_compact_request_budget_seconds: bool = False,
        transcription_request_budget_seconds: float | None = None,
        clear_transcription_request_budget_seconds: bool = False,
        stream_idle_timeout_seconds: float | None = None,
        clear_stream_idle_timeout_seconds: bool = False,
        proxy_downstream_websocket_idle_timeout_seconds: float | None = None,
        clear_proxy_downstream_websocket_idle_timeout_seconds: bool = False,
        sse_keepalive_interval_seconds: float | None = None,
        clear_sse_keepalive_interval_seconds: bool = False,
        # end C2-1 timeouts
        # M3 codex prewarm (tri-state like the resilience toggles)
        http_responses_session_bridge_codex_prewarm_enabled: bool | None = None,
        clear_http_responses_session_bridge_codex_prewarm_enabled: bool = False,
        # end M3 codex prewarm
        # M1 stream/bridge budgets (same tri-state contract).
        http_responses_stream_request_budget_seconds: float | None = None,
        clear_http_responses_stream_request_budget_seconds: bool = False,
        http_responses_session_bridge_request_budget_seconds: float | None = None,
        clear_http_responses_session_bridge_request_budget_seconds: bool = False,
        # end M1 stream/bridge budgets
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
        upstream_route_inputs_changed = (
            upstream_proxy_routing_enabled is not None
            and upstream_proxy_routing_enabled != settings.upstream_proxy_routing_enabled
        ) or (upstream_proxy_default_pool_id or None) != settings.upstream_proxy_default_pool_id
        if sticky_threads_enabled is not None:
            settings.sticky_threads_enabled = sticky_threads_enabled
        if upstream_stream_transport is not None:
            settings.upstream_stream_transport = upstream_stream_transport
        if prohibit_fast_mode is not None:
            settings.prohibit_fast_mode = prohibit_fast_mode
        if http_downstream_transport_policy is not None:
            settings.http_downstream_transport_policy = http_downstream_transport_policy
        if clear_proxy_account_response_create_limit:
            settings.proxy_account_response_create_limit = None
        elif proxy_account_response_create_limit is not None:
            settings.proxy_account_response_create_limit = proxy_account_response_create_limit
        if clear_proxy_account_stream_limit:
            settings.proxy_account_stream_limit = None
        elif proxy_account_stream_limit is not None:
            settings.proxy_account_stream_limit = proxy_account_stream_limit
        if clear_proxy_account_stream_recovery_reserve:
            settings.proxy_account_stream_recovery_reserve = None
        elif proxy_account_stream_recovery_reserve is not None:
            settings.proxy_account_stream_recovery_reserve = proxy_account_stream_recovery_reserve
        if clear_proxy_api_key_fair_share_congestion_threshold_pct:
            settings.proxy_api_key_fair_share_congestion_threshold_pct = None
        elif proxy_api_key_fair_share_congestion_threshold_pct is not None:
            settings.proxy_api_key_fair_share_congestion_threshold_pct = (
                proxy_api_key_fair_share_congestion_threshold_pct
            )
        # C2-2 routing/overload
        if clear_proxy_overload_isolation_seconds:
            settings.proxy_overload_isolation_seconds = None
        elif proxy_overload_isolation_seconds is not None:
            settings.proxy_overload_isolation_seconds = proxy_overload_isolation_seconds
        if clear_proxy_account_error_rate_weighting_enabled:
            settings.proxy_account_error_rate_weighting_enabled = None
        elif proxy_account_error_rate_weighting_enabled is not None:
            settings.proxy_account_error_rate_weighting_enabled = proxy_account_error_rate_weighting_enabled
        if clear_proxy_account_inflight_penalty_pct:
            settings.proxy_account_inflight_penalty_pct = None
        elif proxy_account_inflight_penalty_pct is not None:
            settings.proxy_account_inflight_penalty_pct = proxy_account_inflight_penalty_pct
        if clear_proxy_account_lease_token_weight:
            settings.proxy_account_lease_token_weight = None
        elif proxy_account_lease_token_weight is not None:
            settings.proxy_account_lease_token_weight = proxy_account_lease_token_weight
        if clear_proxy_account_lease_ttl_seconds:
            settings.proxy_account_lease_ttl_seconds = None
        elif proxy_account_lease_ttl_seconds is not None:
            settings.proxy_account_lease_ttl_seconds = proxy_account_lease_ttl_seconds
        # end C2-2 routing/overload
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
        # Independent of ``single_account_id``: its NULL write is coupled to the
        # routing strategy, whereas the overflow designation clears through an
        # explicit flag and never touches (or is touched by) routing_strategy.
        if clear_subscription_overflow_source:
            settings.subscription_overflow_source_id = None
        elif subscription_overflow_source_id is not None:
            settings.subscription_overflow_source_id = subscription_overflow_source_id
        if set_subscription_overflow_drain_until:
            settings.subscription_overflow_drain_until = subscription_overflow_drain_until
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
        # M3 codex prewarm: clear flag resets to NULL (inherit the env alias /
        # code default); a non-None value is dashboard-owned.
        if clear_http_responses_session_bridge_codex_prewarm_enabled:
            settings.http_responses_session_bridge_codex_prewarm_enabled = None
        elif http_responses_session_bridge_codex_prewarm_enabled is not None:
            settings.http_responses_session_bridge_codex_prewarm_enabled = (
                http_responses_session_bridge_codex_prewarm_enabled
            )
        # end M3 codex prewarm
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
        # NULL (not configured = retention disabled); a non-None value stores an
        # override; neither leaves the stored value untouched.
        if clear_request_log_retention:
            settings.request_log_retention_days = None
        elif request_log_retention_days is not None:
            settings.request_log_retention_days = request_log_retention_days
        if clear_usage_history_retention:
            settings.usage_history_retention_days = None
        elif usage_history_retention_days is not None:
            settings.usage_history_retention_days = usage_history_retention_days
        # C2-3 resilience toggles: clear flag resets to NULL (inherit the env
        # alias / code default); a non-None value is dashboard-owned.
        if clear_soft_drain_enabled:
            settings.soft_drain_enabled = None
        elif soft_drain_enabled is not None:
            settings.soft_drain_enabled = soft_drain_enabled
        if clear_deterministic_failover_enabled:
            settings.deterministic_failover_enabled = None
        elif deterministic_failover_enabled is not None:
            settings.deterministic_failover_enabled = deterministic_failover_enabled
        if clear_circuit_breaker_enabled:
            settings.circuit_breaker_enabled = None
        elif circuit_breaker_enabled is not None:
            settings.circuit_breaker_enabled = circuit_breaker_enabled
        # M2 background jobs: clear flag resets to NULL (inherit the env alias
        # / code default); a non-None value is dashboard-owned.
        for column_name, value, clear in (
            ("auth_guardian_enabled", auth_guardian_enabled, clear_auth_guardian_enabled),
            ("automations_scheduler_enabled", automations_scheduler_enabled, clear_automations_scheduler_enabled),
            (
                "rate_limit_reset_credits_refresh_enabled",
                rate_limit_reset_credits_refresh_enabled,
                clear_rate_limit_reset_credits_refresh_enabled,
            ),
        ):
            if clear:
                setattr(settings, column_name, None)
            elif value is not None:
                setattr(settings, column_name, value)
        # end M2 background jobs
        # M5 conversation archive: clear flag resets to NULL (inherit the env
        # alias / code default); a non-None value is dashboard-owned.
        if clear_conversation_archive_enabled:
            settings.conversation_archive_enabled = None
        elif conversation_archive_enabled is not None:
            settings.conversation_archive_enabled = conversation_archive_enabled
        # end M5 conversation archive
        # C2-1 timeouts
        for column_name, value, clear in (
            (
                "upstream_connect_timeout_seconds",
                upstream_connect_timeout_seconds,
                clear_upstream_connect_timeout_seconds,
            ),
            ("proxy_request_budget_seconds", proxy_request_budget_seconds, clear_proxy_request_budget_seconds),
            ("compact_request_budget_seconds", compact_request_budget_seconds, clear_compact_request_budget_seconds),
            (
                "transcription_request_budget_seconds",
                transcription_request_budget_seconds,
                clear_transcription_request_budget_seconds,
            ),
            ("stream_idle_timeout_seconds", stream_idle_timeout_seconds, clear_stream_idle_timeout_seconds),
            (
                "proxy_downstream_websocket_idle_timeout_seconds",
                proxy_downstream_websocket_idle_timeout_seconds,
                clear_proxy_downstream_websocket_idle_timeout_seconds,
            ),
            ("sse_keepalive_interval_seconds", sse_keepalive_interval_seconds, clear_sse_keepalive_interval_seconds),
            # M1 stream/bridge budgets
            (
                "http_responses_stream_request_budget_seconds",
                http_responses_stream_request_budget_seconds,
                clear_http_responses_stream_request_budget_seconds,
            ),
            (
                "http_responses_session_bridge_request_budget_seconds",
                http_responses_session_bridge_request_budget_seconds,
                clear_http_responses_session_bridge_request_budget_seconds,
            ),
            # end M1 stream/bridge budgets
        ):
            if clear:
                setattr(settings, column_name, None)
            elif value is not None:
                setattr(settings, column_name, value)
        # end C2-1 timeouts
        # Force the optimistic-version CAS to run even when the payload makes no
        # net change. `version_id_col` only raises `StaleDataError` when the
        # flush emits an ORM UPDATE; a full-row save that assigns values all
        # equal to this (possibly stale) session's row would otherwise flush
        # nothing, commit silently, and refresh over a concurrent writer's
        # values without the required 409. Flagging a column dirty guarantees an
        # `UPDATE ... SET version = version + 1 WHERE version = :expected`, so a
        # stale no-op save still surfaces the conflict.
        flag_modified(settings, "sticky_threads_enabled")
        # The route cache must be cleared synchronously between the commit and
        # the refresh await: the committed row is visible to concurrent
        # requests as soon as the commit returns, and any await before the
        # clear would let them resolve from the stale per-account route cache
        # (e.g. cached direct egress immediately after routing was enabled).
        await self.commit_refresh(
            settings,
            on_committed=get_upstream_route_cache().clear if upstream_route_inputs_changed else None,
        )
        return settings

    async def clear_subscription_overflow_source_if_matches(self, source_id: str, *, drain_until: datetime) -> bool:
        """Turn overflow off and arm the drain deadline when ``source_id`` is designated.

        No commit: the caller (the model-source delete route) commits the clear
        together with the source delete so a designated-but-deleted source can
        never persist. ``DashboardSettings.version`` is the version_id_col, so a
        concurrent settings writer still surfaces as ``StaleDataError`` at that
        commit.
        """
        settings = await self.get_or_create()
        if settings.subscription_overflow_source_id != source_id:
            return False
        settings.subscription_overflow_source_id = None
        settings.subscription_overflow_drain_until = drain_until
        return True

    async def commit_refresh(
        self, settings: DashboardSettings, *, on_committed: Callable[[], None] | None = None
    ) -> None:
        try:
            await self._session.commit()
        except StaleDataError as exc:
            # The optimistic version check (DashboardSettings.version) matched
            # zero rows: another writer (replica or request) committed first.
            await self._session.rollback()
            raise DashboardSettingsConflictError() from exc
        if on_committed is not None:
            # Runs synchronously between the commit and the refresh await so
            # concurrent requests cannot observe the committed row alongside
            # stale state the hook is meant to reset.
            on_committed()
        await self._session.refresh(settings)


# M4 model catalogue: dashboard rows of the per-model context window overrides.
_UPSERT_INSERT_FNS = {"postgresql": pg_insert, "sqlite": sqlite_insert}


class ModelContextWindowOverridesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[ModelContextWindowOverride]:
        result = await self._session.execute(
            select(ModelContextWindowOverride).order_by(ModelContextWindowOverride.slug.asc())
        )
        return list(result.scalars().all())

    async def by_slug(self) -> dict[str, int]:
        """``slug -> context_window`` for every dashboard row."""
        return {row.slug: row.context_window for row in await self.list_all()}

    async def upsert(self, slug: str, context_window: int) -> None:
        """Create or replace the row for ``slug`` in one statement.

        A read-then-insert would let two concurrent creates of the same new slug
        both miss and one fail the primary key, turning a documented
        create-or-replace into a 500. ``updated_at`` is set explicitly because
        the ORM ``onupdate`` does not fire for a Core insert.
        """
        dialect = self._session.get_bind().dialect.name
        insert_fn = _UPSERT_INSERT_FNS.get(dialect)
        if insert_fn is None:
            raise RuntimeError(f"model_context_window_overrides upsert unsupported for dialect={dialect!r}")
        statement = insert_fn(ModelContextWindowOverride).values(slug=slug, context_window=context_window)
        await self._session.execute(
            statement.on_conflict_do_update(
                index_elements=[ModelContextWindowOverride.slug],
                set_={"context_window": context_window, "updated_at": func.now()},
            )
        )
        await self._session.commit()

    async def delete(self, slug: str) -> bool:
        row = await self._session.get(ModelContextWindowOverride, slug)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.commit()
        return True


# end M4 model catalogue

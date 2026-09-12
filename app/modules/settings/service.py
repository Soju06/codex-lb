from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.auth.dashboard_access import is_admin_level
from app.core.clients.thread_cache_identity import (
    THREAD_CACHE_IDENTITY_MODE_DEFAULT,
    normalize_thread_cache_identity_mode,
)
from app.core.config.background_jobs import BACKGROUND_JOB_SETTINGS
from app.core.config.dashboard_overrides import DASHBOARD_TIMEOUT_SETTINGS

# Re-exported: the resolver lives in ``app.core.config.inheritable`` so hot
# paths in ``app.core`` share it without importing this module.
from app.core.config.inheritable import InheritableValue as InheritableValue
from app.core.config.inheritable import SettingScalar as SettingScalar
from app.core.config.inheritable import SettingSource as SettingSource
from app.core.config.inheritable import resolve_inheritable as resolve_inheritable
from app.core.config.settings import Settings, get_settings
from app.core.config.spool_retention import OPERATION_SPOOL_RETENTION_SETTING
from app.core.conversation_archive import CONVERSATION_ARCHIVE_SETTING
from app.core.resilience.toggles import RESILIENCE_TOGGLE_SETTINGS
from app.db.models import COMPAT_ADMIN_USERNAME, DashboardSettings, LocalLoginPolicy
from app.modules.dashboard_roles.service import resolve_role_grants
from app.modules.dashboard_users.break_glass import BreakGlassRequiresTotpError
from app.modules.settings.repository import SettingsRepository
from app.modules.usage.additional_quota_keys import (
    normalize_additional_quota_key,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DashboardSettingsData:
    sticky_threads_enabled: bool
    upstream_stream_transport: str
    prohibit_fast_mode: bool
    http_downstream_transport_policy: str
    # Effective mode; ``provenance`` carries the source and the fallbacks.
    thread_cache_identity_mode: str
    thread_cache_identity_mode_override: str | None
    proxy_account_response_create_limit: int
    proxy_account_response_create_limit_override: int | None
    proxy_account_stream_limit: int
    proxy_account_stream_limit_override: int | None
    proxy_account_stream_recovery_reserve: int
    proxy_account_stream_recovery_reserve_override: int | None
    proxy_api_key_fair_share_congestion_threshold_pct: int
    proxy_api_key_fair_share_congestion_threshold_pct_override: int | None
    # C2-2 routing/overload: effective values; the dashboard-stored value and
    # the fallbacks are reported through ``provenance``.
    proxy_overload_isolation_seconds: int
    proxy_account_error_rate_weighting_enabled: bool
    proxy_account_inflight_penalty_pct: float
    proxy_account_lease_token_weight: float
    proxy_account_lease_ttl_seconds: float
    # end C2-2 routing/overload
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
    # M3 codex prewarm: effective value (dashboard column, else the deprecated
    # env alias, else the code default); provenance carries the source.
    http_responses_session_bridge_codex_prewarm_enabled: bool
    # end M3 codex prewarm
    sticky_reallocation_budget_threshold_pct: float
    sticky_reallocation_primary_budget_threshold_pct: float
    sticky_reallocation_secondary_budget_threshold_pct: float
    additional_quota_routing_policies: dict[str, str]
    warmup_model: str
    import_without_overwrite: bool
    totp_required_on_login: bool
    totp_required_for_admin_role: bool
    local_login_policy: str
    users_without_totp_count: int
    admins_without_totp_count: int
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
    # C2-3 resilience toggles: effective values (dashboard column, else the
    # deprecated env alias, else the code default); provenance carries the source.
    soft_drain_enabled: bool
    deterministic_failover_enabled: bool
    circuit_breaker_enabled: bool
    # M2 background jobs: effective values (dashboard column, else the deprecated
    # env alias, else the code default); provenance carries the source. The
    # guardian additionally reports whether the static topology blocks it.
    auth_guardian_enabled: bool
    auth_guardian_blocked_by_topology: bool
    automations_scheduler_enabled: bool
    rate_limit_reset_credits_refresh_enabled: bool
    # end M2 background jobs
    # M5 conversation archive: effective toggle; provenance carries the source.
    conversation_archive_enabled: bool
    # end M5 conversation archive
    # R2 spool retention: effective retention of the durable HTTP-bridge
    # operation spool (dashboard column, else the deprecated env alias, else
    # the code default); provenance carries the source.
    http_responses_session_bridge_operation_spool_retention_seconds: float
    # end R2 spool retention
    version: int
    # C2-1 timeouts: effective values (dashboard column, else environment,
    # else code default); the column values are exposed through ``provenance``.
    upstream_connect_timeout_seconds: float
    proxy_request_budget_seconds: float
    compact_request_budget_seconds: float
    transcription_request_budget_seconds: float
    stream_idle_timeout_seconds: float
    proxy_downstream_websocket_idle_timeout_seconds: float
    sse_keepalive_interval_seconds: float
    # end C2-1 timeouts
    # M1 stream/bridge budgets: effective values (dashboard column, else
    # environment, else code default); provenance carries the source.
    http_responses_stream_request_budget_seconds: float
    http_responses_session_bridge_request_budget_seconds: float
    # end M1 stream/bridge budgets
    # Effective value, source and fallbacks of every inheritable setting, keyed
    # by setting name; the settings API exposes it as ``provenance``.
    provenance: Mapping[str, InheritableValue[Any]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DashboardSettingsUpdateData:
    sticky_threads_enabled: bool
    upstream_stream_transport: str
    prohibit_fast_mode: bool
    http_downstream_transport_policy: str
    thread_cache_identity_mode: str | None
    clear_thread_cache_identity_mode: bool
    proxy_account_response_create_limit: int | None
    clear_proxy_account_response_create_limit: bool
    proxy_account_stream_limit: int | None
    clear_proxy_account_stream_limit: bool
    proxy_account_stream_recovery_reserve: int | None
    clear_proxy_account_stream_recovery_reserve: bool
    proxy_api_key_fair_share_congestion_threshold_pct: int | None
    clear_proxy_api_key_fair_share_congestion_threshold_pct: bool
    # C2-2 routing/overload (tri-state: value = store, clear flag = inherit)
    proxy_overload_isolation_seconds: int | None
    clear_proxy_overload_isolation_seconds: bool
    proxy_account_error_rate_weighting_enabled: bool | None
    clear_proxy_account_error_rate_weighting_enabled: bool
    proxy_account_inflight_penalty_pct: float | None
    clear_proxy_account_inflight_penalty_pct: bool
    proxy_account_lease_token_weight: float | None
    clear_proxy_account_lease_token_weight: bool
    proxy_account_lease_ttl_seconds: float | None
    clear_proxy_account_lease_ttl_seconds: bool
    # end C2-2 routing/overload
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
    totp_required_for_admin_role: bool
    local_login_policy: str | None
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
    # C2-3 resilience toggles: tri-state (value = store, clear flag = back to
    # NULL so the env alias / default applies again, neither = untouched).
    soft_drain_enabled: bool | None = None
    clear_soft_drain_enabled: bool = False
    deterministic_failover_enabled: bool | None = None
    clear_deterministic_failover_enabled: bool = False
    circuit_breaker_enabled: bool | None = None
    clear_circuit_breaker_enabled: bool = False
    # M2 background jobs: tri-state like the resilience toggles.
    auth_guardian_enabled: bool | None = None
    clear_auth_guardian_enabled: bool = False
    automations_scheduler_enabled: bool | None = None
    clear_automations_scheduler_enabled: bool = False
    rate_limit_reset_credits_refresh_enabled: bool | None = None
    clear_rate_limit_reset_credits_refresh_enabled: bool = False
    # end M2 background jobs
    # M5 conversation archive: tri-state like the resilience toggles.
    conversation_archive_enabled: bool | None = None
    clear_conversation_archive_enabled: bool = False
    # end M5 conversation archive
    # R2 spool retention: tri-state like the C2-1 timeouts.
    http_responses_session_bridge_operation_spool_retention_seconds: float | None = None
    clear_http_responses_session_bridge_operation_spool_retention_seconds: bool = False
    # end R2 spool retention
    # C2-1 timeouts (tri-state like the caps: value = store, clear = NULL,
    # neither = untouched).
    upstream_connect_timeout_seconds: float | None = None
    clear_upstream_connect_timeout_seconds: bool = False
    proxy_request_budget_seconds: float | None = None
    clear_proxy_request_budget_seconds: bool = False
    compact_request_budget_seconds: float | None = None
    clear_compact_request_budget_seconds: bool = False
    transcription_request_budget_seconds: float | None = None
    clear_transcription_request_budget_seconds: bool = False
    stream_idle_timeout_seconds: float | None = None
    clear_stream_idle_timeout_seconds: bool = False
    proxy_downstream_websocket_idle_timeout_seconds: float | None = None
    clear_proxy_downstream_websocket_idle_timeout_seconds: bool = False
    sse_keepalive_interval_seconds: float | None = None
    clear_sse_keepalive_interval_seconds: bool = False
    # end C2-1 timeouts
    # M3 codex prewarm (tri-state: value = store, clear flag = back to NULL so
    # the env alias / default applies again, neither = untouched).
    http_responses_session_bridge_codex_prewarm_enabled: bool | None = None
    clear_http_responses_session_bridge_codex_prewarm_enabled: bool = False
    # end M3 codex prewarm
    # M1 stream/bridge budgets (tri-state like the C2-1 timeouts).
    http_responses_stream_request_budget_seconds: float | None = None
    clear_http_responses_stream_request_budget_seconds: bool = False
    http_responses_session_bridge_request_budget_seconds: float | None = None
    clear_http_responses_session_bridge_request_budget_seconds: bool = False
    # end M1 stream/bridge budgets


class CompatAdminUnenrolledError(Exception):
    """Requiring TOTP at sign-in while the migrated ``admin`` account has no secret.

    A previous-release replica reads the legacy settings columns: with the
    policy on and no secret there it would refuse that account forever.
    Remove together with the legacy mirror in release N+1.
    """


@dataclass(frozen=True, slots=True)
class TotpEnrollmentSummary:
    """Who still has to enrol, and whether the acting account already did."""

    actor_configured: bool
    users_without_totp: int
    admins_without_totp: int
    compat_admin_unenrolled: bool


class SettingsService:
    def __init__(self, repository: SettingsRepository) -> None:
        self._repository = repository

    async def totp_enrollment(self, actor_user_id: str | None) -> TotpEnrollmentSummary:
        users = await self._repository.list_active_password_users()
        without_totp = [user for user in users if user.totp_secret_encrypted is None]
        return TotpEnrollmentSummary(
            actor_configured=any(user.id == actor_user_id and user.totp_secret_encrypted is not None for user in users),
            users_without_totp=len(without_totp),
            admins_without_totp=sum(1 for user in without_totp if is_admin_level(resolve_role_grants(user.role))),
            compat_admin_unenrolled=any(user.username == COMPAT_ADMIN_USERNAME for user in without_totp),
        )

    async def get_settings(self, *, actor_user_id: str | None = None) -> DashboardSettingsData:
        row = await self._repository.get_or_create()
        return _settings_data(row, await self.totp_enrollment(actor_user_id))

    async def update_settings(
        self,
        payload: DashboardSettingsUpdateData,
        *,
        actor_user_id: str | None = None,
        expected_version: int | None = None,
    ) -> DashboardSettingsData:
        # The accounts lock is taken before this service reads anything and is
        # held until ``update`` commits, so the count below and the policy write
        # are one step. Acquiring it first keeps both dialects taking the lock
        # at the same moment instead of relying on the in-transaction fallback
        # of ``acquire_write_intent``. It is only taken when the payload could
        # be a tightening, so an ordinary settings save is never queued behind
        # account mutations.
        if payload.local_login_policy not in (None, LocalLoginPolicy.ENABLED.value):
            await self._repository.acquire_account_write_intent()
        current = await self._repository.get_or_create()
        # Requiring TOTP of others starts with the acting account: whoever turns
        # either requirement on must already hold a secret, or the next request
        # would park them at the enrolment gate they just created.
        enabling_global = payload.totp_required_on_login and not current.totp_required_on_login
        enabling_admin_role = payload.totp_required_for_admin_role and not current.totp_required_for_admin_role
        if enabling_global or enabling_admin_role:
            enrollment = await self.totp_enrollment(actor_user_id)
            if not enrollment.actor_configured:
                raise ValueError("Set up your own TOTP before requiring it at sign-in")
            if enabling_global and enrollment.compat_admin_unenrolled:
                raise CompatAdminUnenrolledError(
                    "Enrol the 'admin' account in two-factor, or remove its password, "
                    "before requiring two-factor at sign-in"
                )
        # Closing the local password form is the most dangerous button in the
        # product: it is also the setting that locks the install out when the
        # identity provider is down. Only the tightening transition is gated
        # (re-saving the stored value, and relaxing back to ``enabled``, never
        # are), and the refusal names the account that would fix it. The count
        # and the settings write are one atomic step: the accounts lock above
        # is held until ``update`` commits, so no concurrent mutation can
        # remove the last qualifying account in between.
        tightening = (
            payload.local_login_policy is not None
            and payload.local_login_policy != current.local_login_policy
            and payload.local_login_policy != LocalLoginPolicy.ENABLED.value
        )
        if tightening and await self._repository.count_qualifying_break_glass() == 0:
            designated = await self._repository.list_break_glass_designations()
            username = designated[0].username if designated else None
            raise BreakGlassRequiresTotpError(
                (
                    f"Turn on two-factor for '{username}' before restricting local sign-in"
                    if username is not None
                    else "Designate an admin account with two-factor as the emergency account "
                    "before restricting local sign-in"
                ),
                username=username,
            )
        row = await self._repository.update(
            expected_version=expected_version,
            sticky_threads_enabled=payload.sticky_threads_enabled,
            upstream_stream_transport=payload.upstream_stream_transport,
            prohibit_fast_mode=payload.prohibit_fast_mode,
            http_downstream_transport_policy=payload.http_downstream_transport_policy,
            thread_cache_identity_mode=payload.thread_cache_identity_mode,
            clear_thread_cache_identity_mode=payload.clear_thread_cache_identity_mode,
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
            # C2-2 routing/overload
            proxy_overload_isolation_seconds=payload.proxy_overload_isolation_seconds,
            clear_proxy_overload_isolation_seconds=payload.clear_proxy_overload_isolation_seconds,
            proxy_account_error_rate_weighting_enabled=payload.proxy_account_error_rate_weighting_enabled,
            clear_proxy_account_error_rate_weighting_enabled=payload.clear_proxy_account_error_rate_weighting_enabled,
            proxy_account_inflight_penalty_pct=payload.proxy_account_inflight_penalty_pct,
            clear_proxy_account_inflight_penalty_pct=payload.clear_proxy_account_inflight_penalty_pct,
            proxy_account_lease_token_weight=payload.proxy_account_lease_token_weight,
            clear_proxy_account_lease_token_weight=payload.clear_proxy_account_lease_token_weight,
            proxy_account_lease_ttl_seconds=payload.proxy_account_lease_ttl_seconds,
            clear_proxy_account_lease_ttl_seconds=payload.clear_proxy_account_lease_ttl_seconds,
            # end C2-2 routing/overload
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
            totp_required_for_admin_role=payload.totp_required_for_admin_role,
            local_login_policy=payload.local_login_policy,
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
            # C2-3 resilience toggles
            soft_drain_enabled=payload.soft_drain_enabled,
            clear_soft_drain_enabled=payload.clear_soft_drain_enabled,
            # M5 conversation archive
            conversation_archive_enabled=payload.conversation_archive_enabled,
            clear_conversation_archive_enabled=payload.clear_conversation_archive_enabled,
            # end M5 conversation archive
            # R2 spool retention
            http_responses_session_bridge_operation_spool_retention_seconds=(
                payload.http_responses_session_bridge_operation_spool_retention_seconds
            ),
            clear_http_responses_session_bridge_operation_spool_retention_seconds=(
                payload.clear_http_responses_session_bridge_operation_spool_retention_seconds
            ),
            # end R2 spool retention
            deterministic_failover_enabled=payload.deterministic_failover_enabled,
            clear_deterministic_failover_enabled=payload.clear_deterministic_failover_enabled,
            circuit_breaker_enabled=payload.circuit_breaker_enabled,
            clear_circuit_breaker_enabled=payload.clear_circuit_breaker_enabled,
            # M2 background jobs
            auth_guardian_enabled=payload.auth_guardian_enabled,
            clear_auth_guardian_enabled=payload.clear_auth_guardian_enabled,
            automations_scheduler_enabled=payload.automations_scheduler_enabled,
            clear_automations_scheduler_enabled=payload.clear_automations_scheduler_enabled,
            rate_limit_reset_credits_refresh_enabled=payload.rate_limit_reset_credits_refresh_enabled,
            clear_rate_limit_reset_credits_refresh_enabled=payload.clear_rate_limit_reset_credits_refresh_enabled,
            # end M2 background jobs
            # C2-1 timeouts
            upstream_connect_timeout_seconds=payload.upstream_connect_timeout_seconds,
            clear_upstream_connect_timeout_seconds=payload.clear_upstream_connect_timeout_seconds,
            proxy_request_budget_seconds=payload.proxy_request_budget_seconds,
            clear_proxy_request_budget_seconds=payload.clear_proxy_request_budget_seconds,
            compact_request_budget_seconds=payload.compact_request_budget_seconds,
            clear_compact_request_budget_seconds=payload.clear_compact_request_budget_seconds,
            transcription_request_budget_seconds=payload.transcription_request_budget_seconds,
            clear_transcription_request_budget_seconds=payload.clear_transcription_request_budget_seconds,
            stream_idle_timeout_seconds=payload.stream_idle_timeout_seconds,
            clear_stream_idle_timeout_seconds=payload.clear_stream_idle_timeout_seconds,
            proxy_downstream_websocket_idle_timeout_seconds=payload.proxy_downstream_websocket_idle_timeout_seconds,
            clear_proxy_downstream_websocket_idle_timeout_seconds=(
                payload.clear_proxy_downstream_websocket_idle_timeout_seconds
            ),
            sse_keepalive_interval_seconds=payload.sse_keepalive_interval_seconds,
            clear_sse_keepalive_interval_seconds=payload.clear_sse_keepalive_interval_seconds,
            # end C2-1 timeouts
            # M3 codex prewarm
            http_responses_session_bridge_codex_prewarm_enabled=(
                payload.http_responses_session_bridge_codex_prewarm_enabled
            ),
            clear_http_responses_session_bridge_codex_prewarm_enabled=(
                payload.clear_http_responses_session_bridge_codex_prewarm_enabled
            ),
            # end M3 codex prewarm
            # M1 stream/bridge budgets
            http_responses_stream_request_budget_seconds=payload.http_responses_stream_request_budget_seconds,
            clear_http_responses_stream_request_budget_seconds=(
                payload.clear_http_responses_stream_request_budget_seconds
            ),
            http_responses_session_bridge_request_budget_seconds=(
                payload.http_responses_session_bridge_request_budget_seconds
            ),
            clear_http_responses_session_bridge_request_budget_seconds=(
                payload.clear_http_responses_session_bridge_request_budget_seconds
            ),
            # end M1 stream/bridge budgets
        )
        return _settings_data(row, await self.totp_enrollment(actor_user_id))
        return _settings_data(row, await self.totp_enrollment(actor_user_id))


# Retention has no environment fallback: NULL = never set from the dashboard =
# disabled; 0 = explicitly disabled.
_RETENTION_DISABLED_DAYS = 0


_ROUTING_POLICIES = frozenset({"inherit", "normal", "burn_first", "preserve"})

# Inheritable settings with an environment fallback: the ``dashboard_settings``
# column, the ``Settings`` field and the provenance key share one name.
_ENVIRONMENT_INHERITABLE_SETTINGS = (
    # Thread cache identity mode (str): NULL inherits the env value, then "shared".
    "thread_cache_identity_mode",
    "proxy_account_response_create_limit",
    "proxy_account_stream_limit",
    "proxy_account_stream_recovery_reserve",
    "proxy_api_key_fair_share_congestion_threshold_pct",
    *DASHBOARD_TIMEOUT_SETTINGS,  # C2-1 timeouts
    # C2-2 routing/overload
    "proxy_overload_isolation_seconds",
    "proxy_account_error_rate_weighting_enabled",
    "proxy_account_inflight_penalty_pct",
    "proxy_account_lease_token_weight",
    "proxy_account_lease_ttl_seconds",
    # end C2-2 routing/overload
    # M3 codex prewarm: bool; a NULL column inherits the deprecated env alias.
    "http_responses_session_bridge_codex_prewarm_enabled",
    # end M3 codex prewarm
    CONVERSATION_ARCHIVE_SETTING,  # M5 conversation archive (bool, env alias)
    # R2 spool retention: float; a NULL column inherits the deprecated env alias.
    OPERATION_SPOOL_RETENTION_SETTING,
)
# Retention has no environment fallback: NULL = never set from the dashboard =
# disabled; 0 = explicitly disabled.
_RETENTION_DISABLED_DAYS = 0


def _resolve_environment_inheritable(row: DashboardSettings, name: str) -> InheritableValue[Any]:
    default = Settings.model_fields[name].default
    # ``Settings`` always carries every field; startup-settings fakes in tests
    # may carry only the fields they exercise, so fall back to the code default.
    return resolve_inheritable(getattr(row, name), getattr(get_settings(), name, default), default)


def _resolve_environment_toggle(row: DashboardSettings, name: str) -> InheritableValue[bool]:
    # C2-3 resilience toggles: same precedence as the integer caps, typed bool.
    default = bool(Settings.model_fields[name].default)
    return resolve_inheritable(getattr(row, name), bool(getattr(get_settings(), name, default)), default)


def _auth_guardian_blocked_by_topology() -> bool:
    # M2 background jobs: read the two topology fields defensively, the way
    # ``_resolve_environment_toggle`` reads its field, so a stub startup-settings
    # object without them reads as "not blocked" instead of raising.
    startup = get_settings()
    ring = getattr(startup, "http_responses_session_bridge_instance_ring", ())
    leader_election_enabled = bool(getattr(startup, "leader_election_enabled", True))
    return len(ring) > 1 and not leader_election_enabled


def warn_environment_shadowed_by_dashboard(row: DashboardSettings, settings: Settings | None = None) -> list[str]:
    """Log one startup WARN naming env vars that are set but ignored because the dashboard owns the value.

    Only settings whose environment variable is explicitly set (``model_fields_set``)
    AND whose dashboard column is non-NULL are reported, so an operator who
    edits the variable learns why nothing changed. Returns the names for tests.
    """
    environment = settings if settings is not None else get_settings()
    shadowed = [
        name
        # M2 background jobs: their env aliases are shadowed the same way.
        for name in (*_ENVIRONMENT_INHERITABLE_SETTINGS, *BACKGROUND_JOB_SETTINGS)
        if name in environment.model_fields_set and getattr(row, name, None) is not None
    ]
    if shadowed:
        logger.warning(
            "environment value(s) ignored because the dashboard owns the setting: %s "
            "(Settings -> Advanced; clear the dashboard value to inherit the environment again)",
            ", ".join(f"CODEX_LB_{name.upper()}" for name in shadowed),
        )
    return shadowed


def _resolve_inheritable_settings(
    row: DashboardSettings,
) -> dict[str, InheritableValue[Any]]:
    resolved: dict[str, InheritableValue[Any]] = {
        name: _resolve_environment_inheritable(row, name) for name in _ENVIRONMENT_INHERITABLE_SETTINGS
    }
    # A stale or hand-edited column value is not a valid decision, so it is
    # normalized away before it can reach the pattern-constrained settings
    # response and fail ``GET /api/settings`` for every setting at once. A
    # NULL-equivalent column simply falls back to the environment/default legs.
    resolved["thread_cache_identity_mode"] = resolve_inheritable(
        normalize_thread_cache_identity_mode(row.thread_cache_identity_mode),
        normalize_thread_cache_identity_mode(getattr(get_settings(), "thread_cache_identity_mode", None))
        or THREAD_CACHE_IDENTITY_MODE_DEFAULT,
        THREAD_CACHE_IDENTITY_MODE_DEFAULT,
    )
    resolved["request_log_retention_days"] = resolve_inheritable(
        row.request_log_retention_days, None, _RETENTION_DISABLED_DAYS
    )
    resolved["usage_history_retention_days"] = resolve_inheritable(
        row.usage_history_retention_days, None, _RETENTION_DISABLED_DAYS
    )
    for name in RESILIENCE_TOGGLE_SETTINGS:  # C2-3 resilience toggles
        resolved[name] = _resolve_environment_toggle(row, name)
    for name in BACKGROUND_JOB_SETTINGS:  # M2 background jobs
        resolved[name] = _resolve_environment_toggle(row, name)
    return resolved


def _settings_data(row: DashboardSettings, totp: TotpEnrollmentSummary) -> DashboardSettingsData:
    resolved = _resolve_inheritable_settings(row)
    return DashboardSettingsData(
        sticky_threads_enabled=row.sticky_threads_enabled,
        upstream_stream_transport=row.upstream_stream_transport,
        prohibit_fast_mode=row.prohibit_fast_mode,
        http_downstream_transport_policy=row.http_downstream_transport_policy,
        thread_cache_identity_mode=resolved["thread_cache_identity_mode"].value,
        thread_cache_identity_mode_override=normalize_thread_cache_identity_mode(row.thread_cache_identity_mode),
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
        # C2-2 routing/overload
        proxy_overload_isolation_seconds=resolved["proxy_overload_isolation_seconds"].value,
        proxy_account_error_rate_weighting_enabled=resolved["proxy_account_error_rate_weighting_enabled"].value,
        proxy_account_inflight_penalty_pct=resolved["proxy_account_inflight_penalty_pct"].value,
        proxy_account_lease_token_weight=resolved["proxy_account_lease_token_weight"].value,
        proxy_account_lease_ttl_seconds=resolved["proxy_account_lease_ttl_seconds"].value,
        # end C2-2 routing/overload
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
        # M3 codex prewarm
        http_responses_session_bridge_codex_prewarm_enabled=bool(
            resolved["http_responses_session_bridge_codex_prewarm_enabled"].value
        ),
        # end M3 codex prewarm
        sticky_reallocation_budget_threshold_pct=row.sticky_reallocation_budget_threshold_pct,
        sticky_reallocation_primary_budget_threshold_pct=row.sticky_reallocation_primary_budget_threshold_pct,
        sticky_reallocation_secondary_budget_threshold_pct=row.sticky_reallocation_secondary_budget_threshold_pct,
        additional_quota_routing_policies=_parse_additional_quota_routing_policies(
            row.additional_quota_routing_policies_json
        ),
        warmup_model=row.warmup_model,
        import_without_overwrite=row.import_without_overwrite,
        totp_required_on_login=row.totp_required_on_login,
        totp_required_for_admin_role=row.totp_required_for_admin_role,
        local_login_policy=row.local_login_policy,
        users_without_totp_count=totp.users_without_totp,
        admins_without_totp_count=totp.admins_without_totp,
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
        # C2-3 resilience toggles
        soft_drain_enabled=bool(resolved["soft_drain_enabled"].value),
        deterministic_failover_enabled=bool(resolved["deterministic_failover_enabled"].value),
        circuit_breaker_enabled=bool(resolved["circuit_breaker_enabled"].value),
        # M2 background jobs
        auth_guardian_enabled=bool(resolved["auth_guardian_enabled"].value),
        auth_guardian_blocked_by_topology=_auth_guardian_blocked_by_topology(),
        automations_scheduler_enabled=bool(resolved["automations_scheduler_enabled"].value),
        rate_limit_reset_credits_refresh_enabled=bool(resolved["rate_limit_reset_credits_refresh_enabled"].value),
        # end M2 background jobs
        # M5 conversation archive
        conversation_archive_enabled=bool(resolved[CONVERSATION_ARCHIVE_SETTING].value),
        # end M5 conversation archive
        # R2 spool retention
        http_responses_session_bridge_operation_spool_retention_seconds=float(
            resolved[OPERATION_SPOOL_RETENTION_SETTING].value
        ),
        # end R2 spool retention
        version=row.version,
        # C2-1 timeouts
        upstream_connect_timeout_seconds=float(resolved["upstream_connect_timeout_seconds"].value),
        proxy_request_budget_seconds=float(resolved["proxy_request_budget_seconds"].value),
        compact_request_budget_seconds=float(resolved["compact_request_budget_seconds"].value),
        transcription_request_budget_seconds=float(resolved["transcription_request_budget_seconds"].value),
        stream_idle_timeout_seconds=float(resolved["stream_idle_timeout_seconds"].value),
        proxy_downstream_websocket_idle_timeout_seconds=float(
            resolved["proxy_downstream_websocket_idle_timeout_seconds"].value
        ),
        sse_keepalive_interval_seconds=float(resolved["sse_keepalive_interval_seconds"].value),
        # end C2-1 timeouts
        # M1 stream/bridge budgets
        http_responses_stream_request_budget_seconds=float(
            resolved["http_responses_stream_request_budget_seconds"].value
        ),
        http_responses_session_bridge_request_budget_seconds=float(
            resolved["http_responses_session_bridge_request_budget_seconds"].value
        ),
        # end M1 stream/bridge budgets
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

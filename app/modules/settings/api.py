from __future__ import annotations

import ipaddress
import socket
import time
from collections.abc import Mapping
from typing import Any, cast

import aiohttp
import httpx
from aiohttp_socks import ProxyConnector
from fastapi import APIRouter, Body, Depends, Query, Request
from python_socks import ProxyType
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.audit.service import AuditActor, AuditService, AuditSeverity, AuditTarget
from app.core.auth.dashboard_access import DashboardPrincipal, DashboardRole, Permission
from app.core.auth.dependencies import (
    ensure_dashboard_permission,
    ensure_step_up,
    require_dashboard_permission,
    require_dashboard_write_access,
    set_dashboard_error_format,
    validate_dashboard_session,
)
from app.core.clients.http import _shared_ssl_context
from app.core.config import settings as settings_module
from app.core.config.background_jobs import BACKGROUND_JOB_SETTINGS
from app.core.config.context_window_overrides import (
    get_model_context_window_overrides_cache,
    resolve_context_window_overrides,
)
from app.core.config.dashboard_overrides import DASHBOARD_TIMEOUT_SETTINGS
from app.core.config.inheritable import resolve_inheritable
from app.core.config.settings import get_settings as get_app_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.config.spool_retention import (
    OPERATION_SPOOL_RETENTION_SETTING,
    SPOOL_RETENTION_FLOOR_INPUTS,
    binding_spool_retention_floor_term,
    operation_spool_retention_floor_seconds,
)
from app.core.conversation_archive import CONVERSATION_ARCHIVE_SETTING, CONVERSATION_ARCHIVE_TOGGLED_ACTION
from app.core.crypto import TokenEncryptor
from app.core.exceptions import (
    DashboardBadRequestError,
    DashboardConflictError,
    DashboardNotFoundError,
    DashboardSettingsConflictError,
)
from app.core.resilience.toggles import RESILIENCE_TOGGLE_SETTINGS
from app.core.timeout_invariants import find_timeout_invariant_violations
from app.core.upstream_proxy import UpstreamProxyRouteError, resolve_proxy_endpoint, sends_plaintext_credentials
from app.core.upstream_proxy.cache import get_upstream_route_cache
from app.core.utils.time import utcnow
from app.db.models import Account, AccountProxyBinding, AccountStatus, ProxyEndpoint, ProxyPool, ProxyPoolMember
from app.dependencies import SettingsContext, get_proxy_service_for_app, get_settings_context
from app.modules.dashboard_users.break_glass import BreakGlassRequiresTotpError
from app.modules.model_sources.repository import ModelSourcesRepository
from app.modules.proxy.account_cache import (
    clear_account_routing_unavailable,
    get_account_selection_cache,
    propagate_account_routing_change,
)
from app.modules.settings.repository import ModelContextWindowOverridesRepository
from app.modules.settings.schemas import (
    SECURITY_SETTINGS_FIELDS,
    AccountProxyBindingRequest,
    AccountProxyBindingResponse,
    AdditionalQuotaPolicy,
    DashboardSettingsResponse,
    DashboardSettingsUpdateRequest,
    LocalLoginPolicyLiteral,
    ModelContextWindowOverrideResponse,
    ModelContextWindowOverridesResponse,
    ModelContextWindowOverrideUpsertRequest,
    RuntimeConnectAddressResponse,
    SettingProvenance,
    SubscriptionOverflowPreflightResponse,
    UpstreamProxyAdminResponse,
    UpstreamProxyEndpointCreateRequest,
    UpstreamProxyEndpointResponse,
    UpstreamProxyEndpointTestResponse,
    UpstreamProxyPoolCreateRequest,
    UpstreamProxyPoolMemberRequest,
    UpstreamProxyPoolResponse,
)
from app.modules.settings.service import DashboardSettingsUpdateData
from app.modules.settings.subscription_overflow import (
    load_subscription_overflow_preflight,
    resolve_drain_until,
    resolve_pins_expire_by,
    validate_overflow_source,
)
from app.modules.usage.additional_quota_keys import (
    get_additional_quota_routing_policy,
    list_additional_quota_definitions,
)

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}
UPSTREAM_PROXY_TEST_URL = "https://chatgpt.com/cdn-cgi/trace"
UPSTREAM_PROXY_TEST_TIMEOUT_SECONDS = 8.0
HTTP_PROXY_AUTHENTICATION_REQUIRED = 407
IMPORT_PROXY_REQUIRED_PAUSE_REASON = "upstream_proxy_required_on_import"


def _is_non_loopback_ipv4(value: str | None) -> bool:
    if not value:
        return False
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return isinstance(address, ipaddress.IPv4Address) and not address.is_loopback and not address.is_unspecified


def _resolve_hostname_ipv4(hostname: str) -> str | None:
    try:
        infos = socket.getaddrinfo(hostname, None, family=socket.AF_INET, type=socket.SOCK_STREAM)
    except OSError:
        return None
    for info in infos:
        candidate = info[4][0]
        if not isinstance(candidate, str):
            continue
        if _is_non_loopback_ipv4(candidate):
            return candidate
    return None


def _resolve_runtime_connect_address(request: Request) -> str:
    override = get_app_settings().connect_address
    if override:
        return override

    request_host = request.url.hostname or ""
    if _is_non_loopback_ipv4(request_host):
        return request_host

    normalized_host = request_host.strip().lower()
    if normalized_host and normalized_host not in LOOPBACK_HOSTS:
        resolved_host = _resolve_hostname_ipv4(request_host)
        if resolved_host:
            return resolved_host
        return request_host
    return "<codex-lb-ip-or-dns>"


router = APIRouter(
    prefix="/api/settings",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


# C2-2 routing/overload
_ROUTING_OVERLOAD_SETTINGS: tuple[str, ...] = (
    "proxy_overload_isolation_seconds",
    "proxy_account_error_rate_weighting_enabled",
    "proxy_account_inflight_penalty_pct",
    "proxy_account_lease_token_weight",
    "proxy_account_lease_ttl_seconds",
)


def _dashboard_value(payload: DashboardSettingsUpdateRequest, name: str) -> Any:
    """Tri-state read: the value when the field was sent (``None`` for an explicit
    null), ``None`` when it was omitted (unchanged)."""
    return getattr(payload, name) if name in payload.model_fields_set else None


def _clears_dashboard_value(payload: DashboardSettingsUpdateRequest, name: str) -> bool:
    """True only for an explicit ``null``: the setting returns to inheriting the
    environment value or code default."""
    return name in payload.model_fields_set and getattr(payload, name) is None


# end C2-2 routing/overload


def _dashboard_settings_response(settings, *, principal: DashboardPrincipal) -> DashboardSettingsResponse:
    environment_settings = get_app_settings()
    # M5 conversation archive: the T1 directory is this replica's local shard;
    # a filesystem path is admin-only information. ``getattr``: startup-settings
    # fakes in tests may carry only the fields they exercise.
    archive_dir = getattr(environment_settings, "conversation_archive_dir", None)
    conversation_archive_dir = (
        str(archive_dir) if archive_dir is not None and principal.role == DashboardRole.ADMIN else None
    )
    additional_quota_policies = [
        AdditionalQuotaPolicy(
            quota_key=definition.quota_key,
            display_label=definition.display_label,
            routing_policy=get_additional_quota_routing_policy(
                definition.quota_key,
                overrides=settings.additional_quota_routing_policies,
            ),
            model_ids=sorted(definition.model_ids),
        )
        for definition in list_additional_quota_definitions()
    ]
    return DashboardSettingsResponse(
        sticky_threads_enabled=settings.sticky_threads_enabled,
        upstream_stream_transport=settings.upstream_stream_transport,
        prohibit_fast_mode=settings.prohibit_fast_mode,
        http_downstream_transport_policy=settings.http_downstream_transport_policy,
        thread_cache_identity_mode=settings.thread_cache_identity_mode,
        thread_cache_identity_mode_override=settings.thread_cache_identity_mode_override,
        proxy_account_response_create_limit=settings.proxy_account_response_create_limit,
        proxy_account_response_create_limit_environment_value=(
            getattr(
                environment_settings,
                "proxy_account_response_create_limit",
                settings.proxy_account_response_create_limit,
            )
        ),
        proxy_account_response_create_limit_override=(settings.proxy_account_response_create_limit_override),
        proxy_account_stream_limit=settings.proxy_account_stream_limit,
        proxy_account_stream_limit_environment_value=getattr(
            environment_settings,
            "proxy_account_stream_limit",
            settings.proxy_account_stream_limit,
        ),
        proxy_account_stream_limit_override=settings.proxy_account_stream_limit_override,
        proxy_account_stream_recovery_reserve=settings.proxy_account_stream_recovery_reserve,
        proxy_account_stream_recovery_reserve_environment_value=(
            getattr(
                environment_settings,
                "proxy_account_stream_recovery_reserve",
                settings.proxy_account_stream_recovery_reserve,
            )
        ),
        proxy_account_stream_recovery_reserve_override=(settings.proxy_account_stream_recovery_reserve_override),
        proxy_api_key_fair_share_congestion_threshold_pct=(settings.proxy_api_key_fair_share_congestion_threshold_pct),
        proxy_api_key_fair_share_congestion_threshold_pct_environment_value=(
            getattr(
                environment_settings,
                "proxy_api_key_fair_share_congestion_threshold_pct",
                settings.proxy_api_key_fair_share_congestion_threshold_pct,
            )
        ),
        proxy_api_key_fair_share_congestion_threshold_pct_override=(
            settings.proxy_api_key_fair_share_congestion_threshold_pct_override
        ),
        # C2-2 routing/overload
        proxy_overload_isolation_seconds=settings.proxy_overload_isolation_seconds,
        proxy_account_error_rate_weighting_enabled=settings.proxy_account_error_rate_weighting_enabled,
        proxy_account_inflight_penalty_pct=settings.proxy_account_inflight_penalty_pct,
        proxy_account_lease_token_weight=settings.proxy_account_lease_token_weight,
        proxy_account_lease_ttl_seconds=settings.proxy_account_lease_ttl_seconds,
        # end C2-2 routing/overload
        upstream_proxy_routing_enabled=settings.upstream_proxy_routing_enabled,
        upstream_proxy_default_pool_id=settings.upstream_proxy_default_pool_id,
        prefer_earlier_reset_accounts=settings.prefer_earlier_reset_accounts,
        prefer_earlier_reset_window=settings.prefer_earlier_reset_window,
        show_reset_credit_badges=settings.show_reset_credit_badges,
        auto_redeem_reset_credits_before_expiry=settings.auto_redeem_reset_credits_before_expiry,
        show_reset_credit_expiry_badge=settings.show_reset_credit_expiry_badge,
        routing_strategy=settings.routing_strategy,
        relative_availability_power=settings.relative_availability_power,
        relative_availability_top_k=settings.relative_availability_top_k,
        single_account_id=settings.single_account_id,
        subscription_overflow_source_id=settings.subscription_overflow_source_id,
        subscription_overflow_drain_until=settings.subscription_overflow_drain_until,
        subscription_overflow_pins_expire_by=resolve_pins_expire_by(settings.subscription_overflow_drain_until),
        openai_cache_affinity_max_age_seconds=settings.openai_cache_affinity_max_age_seconds,
        dashboard_session_ttl_seconds=settings.dashboard_session_ttl_seconds,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=settings.http_responses_session_bridge_prompt_cache_idle_ttl_seconds,
        http_responses_session_bridge_gateway_safe_mode=settings.http_responses_session_bridge_gateway_safe_mode,
        # M3 codex prewarm
        http_responses_session_bridge_codex_prewarm_enabled=(
            settings.http_responses_session_bridge_codex_prewarm_enabled
        ),
        # end M3 codex prewarm
        sticky_reallocation_budget_threshold_pct=settings.sticky_reallocation_budget_threshold_pct,
        sticky_reallocation_primary_budget_threshold_pct=settings.sticky_reallocation_primary_budget_threshold_pct,
        sticky_reallocation_secondary_budget_threshold_pct=settings.sticky_reallocation_secondary_budget_threshold_pct,
        additional_quota_routing_policies=settings.additional_quota_routing_policies,
        additional_quota_policies=additional_quota_policies,
        warmup_model=settings.warmup_model,
        import_without_overwrite=settings.import_without_overwrite,
        totp_required_on_login=settings.totp_required_on_login,
        totp_required_for_admin_role=settings.totp_required_for_admin_role,
        local_login_policy=cast(LocalLoginPolicyLiteral, settings.local_login_policy),
        users_without_totp_count=settings.users_without_totp_count,
        admins_without_totp_count=settings.admins_without_totp_count,
        api_key_auth_enabled=settings.api_key_auth_enabled,
        hide_upstream_quota_from_api_keys=settings.hide_upstream_quota_from_api_keys,
        limit_warmup_enabled=settings.limit_warmup_enabled,
        limit_warmup_windows=settings.limit_warmup_windows,
        limit_warmup_model=settings.limit_warmup_model,
        limit_warmup_prompt=settings.limit_warmup_prompt,
        limit_warmup_cooldown_seconds=settings.limit_warmup_cooldown_seconds,
        limit_warmup_exhausted_threshold_percent=settings.limit_warmup_exhausted_threshold_percent,
        limit_warmup_idle_threshold_percent=settings.limit_warmup_idle_threshold_percent,
        limit_warmup_min_available_percent=settings.limit_warmup_min_available_percent,
        weekly_pace_working_days=settings.weekly_pace_working_days,
        weekly_pace_smoothing_minutes=settings.weekly_pace_smoothing_minutes,
        guest_access_enabled=settings.guest_access_enabled,
        guest_password_configured=settings.guest_password_configured,
        limit_warmup_staggered_idle_enabled=settings.limit_warmup_staggered_idle_enabled,
        request_log_retention_days=settings.request_log_retention_days,
        usage_history_retention_days=settings.usage_history_retention_days,
        request_log_retention_override_days=settings.request_log_retention_override_days,
        usage_history_retention_override_days=settings.usage_history_retention_override_days,
        # C2-3 resilience toggles
        soft_drain_enabled=settings.soft_drain_enabled,
        deterministic_failover_enabled=settings.deterministic_failover_enabled,
        circuit_breaker_enabled=settings.circuit_breaker_enabled,
        # M2 background jobs
        auth_guardian_enabled=settings.auth_guardian_enabled,
        auth_guardian_blocked_by_topology=settings.auth_guardian_blocked_by_topology,
        automations_scheduler_enabled=settings.automations_scheduler_enabled,
        rate_limit_reset_credits_refresh_enabled=settings.rate_limit_reset_credits_refresh_enabled,
        # end M2 background jobs
        # M5 conversation archive
        conversation_archive_enabled=settings.conversation_archive_enabled,
        conversation_archive_dir=conversation_archive_dir,
        # end M5 conversation archive
        # R2 spool retention: the effective window plus the floor the API would
        # enforce, so the data retention card can mirror the check client-side.
        http_responses_session_bridge_operation_spool_retention_seconds=(
            settings.http_responses_session_bridge_operation_spool_retention_seconds
        ),
        http_responses_session_bridge_operation_spool_retention_floor_seconds=(
            operation_spool_retention_floor_seconds(settings, startup_settings=environment_settings)
        ),
        # end R2 spool retention
        version=settings.version,
        # C2-1 timeouts
        upstream_connect_timeout_seconds=settings.upstream_connect_timeout_seconds,
        proxy_request_budget_seconds=settings.proxy_request_budget_seconds,
        compact_request_budget_seconds=settings.compact_request_budget_seconds,
        transcription_request_budget_seconds=settings.transcription_request_budget_seconds,
        stream_idle_timeout_seconds=settings.stream_idle_timeout_seconds,
        proxy_downstream_websocket_idle_timeout_seconds=settings.proxy_downstream_websocket_idle_timeout_seconds,
        sse_keepalive_interval_seconds=settings.sse_keepalive_interval_seconds,
        # end C2-1 timeouts
        # M1 stream/bridge budgets
        http_responses_stream_request_budget_seconds=settings.http_responses_stream_request_budget_seconds,
        http_responses_session_bridge_request_budget_seconds=(
            settings.http_responses_session_bridge_request_budget_seconds
        ),
        # end M1 stream/bridge budgets
        provenance={
            name: SettingProvenance(source=resolved.source, env_value=resolved.env_value, default=resolved.default)
            for name, resolved in settings.provenance.items()
        },
    )


@router.get("", response_model=DashboardSettingsResponse)
async def get_settings(
    principal: DashboardPrincipal = Depends(validate_dashboard_session),
    context: SettingsContext = Depends(get_settings_context),
) -> DashboardSettingsResponse:
    settings = await context.service.get_settings(actor_user_id=principal.user_id)
    return _dashboard_settings_response(settings, principal=principal)


@router.get("/subscription-overflow/preflight", response_model=SubscriptionOverflowPreflightResponse)
async def get_subscription_overflow_preflight(
    source_id: str = Query(min_length=1, max_length=255),
    _write_access=Depends(require_dashboard_write_access),
    context: SettingsContext = Depends(get_settings_context),
) -> SubscriptionOverflowPreflightResponse:
    # Write access rather than session-only: the report enumerates the source
    # catalog and key scoping, which a read-only guest must not see.
    settings = await context.repository.get_or_create()
    preflight = await load_subscription_overflow_preflight(
        context.session,
        source_id,
        drain_until=settings.subscription_overflow_drain_until,
    )
    if preflight is None:
        raise DashboardNotFoundError("Model source not found")
    return preflight


@router.get(
    "/runtime/connect-address",
    response_model=RuntimeConnectAddressResponse,
    dependencies=[Depends(require_dashboard_permission(Permission.OPS_WRITE))],
)
async def get_runtime_connect_address(request: Request) -> RuntimeConnectAddressResponse:
    return RuntimeConnectAddressResponse(connect_address=_resolve_runtime_connect_address(request))


@router.get(
    "/upstream-proxy",
    response_model=UpstreamProxyAdminResponse,
    dependencies=[Depends(require_dashboard_permission(Permission.OPS_WRITE))],
)
async def get_upstream_proxy_admin(
    context: SettingsContext = Depends(get_settings_context),
) -> UpstreamProxyAdminResponse:
    settings = await context.repository.get_or_create()
    endpoint_rows = (await context.session.execute(select(ProxyEndpoint).order_by(ProxyEndpoint.name.asc()))).scalars()
    pool_rows = (await context.session.execute(select(ProxyPool).order_by(ProxyPool.name.asc()))).scalars().all()
    member_rows = (
        await context.session.execute(select(ProxyPoolMember).order_by(ProxyPoolMember.sort_order.asc()))
    ).scalars()
    bindings = (
        (await context.session.execute(select(AccountProxyBinding).order_by(AccountProxyBinding.account_id.asc())))
        .scalars()
        .all()
    )
    endpoint_ids_by_pool: dict[str, list[str]] = {}
    for member in member_rows:
        endpoint_ids_by_pool.setdefault(member.pool_id, []).append(member.endpoint_id)
    return UpstreamProxyAdminResponse(
        routing_enabled=settings.upstream_proxy_routing_enabled,
        default_pool_id=settings.upstream_proxy_default_pool_id,
        endpoints=[_proxy_endpoint_response(row) for row in endpoint_rows],
        pools=[
            UpstreamProxyPoolResponse(
                id=row.id,
                name=row.name,
                is_active=row.is_active,
                endpoint_ids=endpoint_ids_by_pool.get(row.id, []),
            )
            for row in pool_rows
        ],
        bindings=[
            AccountProxyBindingResponse(account_id=row.account_id, pool_id=row.pool_id, is_active=row.is_active)
            for row in bindings
        ],
    )


@router.post("/upstream-proxy/endpoints", response_model=UpstreamProxyEndpointResponse)
async def create_upstream_proxy_endpoint(
    payload: UpstreamProxyEndpointCreateRequest,
    _security_access=Depends(require_dashboard_permission(Permission.SECURITY_WRITE)),
    context: SettingsContext = Depends(get_settings_context),
) -> UpstreamProxyEndpointResponse:
    if payload.username is not None and ":" in payload.username:
        # Mirrors the resolver: a Basic user-id cannot encode a colon.
        raise DashboardBadRequestError('Proxy usernames cannot contain ":"', code="invalid_proxy_username")
    encryptor = TokenEncryptor()
    row = ProxyEndpoint(
        name=payload.name,
        scheme=payload.scheme,
        host=payload.host,
        port=payload.port,
        username=payload.username,
        password_encrypted=encryptor.encrypt(payload.password) if payload.password else None,
        is_active=payload.is_active,
    )
    context.session.add(row)
    await context.session.commit()
    await context.session.refresh(row)
    return _proxy_endpoint_response(row)


@router.post("/upstream-proxy/endpoints/{endpoint_id}/test", response_model=UpstreamProxyEndpointTestResponse)
async def test_upstream_proxy_endpoint(
    endpoint_id: str,
    _write_access=Depends(require_dashboard_write_access),
    context: SettingsContext = Depends(get_settings_context),
) -> UpstreamProxyEndpointTestResponse:
    row = await context.session.get(ProxyEndpoint, endpoint_id)
    if row is None:
        raise DashboardBadRequestError("Proxy endpoint not found", code="proxy_endpoint_not_found")
    try:
        endpoint = resolve_proxy_endpoint(row, encryptor=TokenEncryptor())
    except UpstreamProxyRouteError as exc:
        # Persisted rows the resolver now rejects report the reason instead of 500.
        return UpstreamProxyEndpointTestResponse(endpoint_id=row.id, ok=False, elapsed_ms=0, error=exc.reason)
    started = time.monotonic()
    try:
        status_code = await _probe_upstream_proxy_endpoint(endpoint)
    except Exception as exc:
        return UpstreamProxyEndpointTestResponse(
            endpoint_id=endpoint.id,
            ok=False,
            elapsed_ms=_elapsed_ms(started),
            error=type(exc).__name__,
        )
    if status_code == HTTP_PROXY_AUTHENTICATION_REQUIRED:
        return UpstreamProxyEndpointTestResponse(
            endpoint_id=endpoint.id,
            ok=False,
            status_code=status_code,
            elapsed_ms=_elapsed_ms(started),
            error="proxy_auth_failed",
        )
    ok = status_code < 500
    return UpstreamProxyEndpointTestResponse(
        endpoint_id=endpoint.id,
        ok=ok,
        status_code=status_code,
        elapsed_ms=_elapsed_ms(started),
        error=None if ok else "upstream_probe_failed",
    )


@router.post("/upstream-proxy/pools", response_model=UpstreamProxyPoolResponse)
async def create_upstream_proxy_pool(
    payload: UpstreamProxyPoolCreateRequest,
    _write_access=Depends(require_dashboard_write_access),
    context: SettingsContext = Depends(get_settings_context),
) -> UpstreamProxyPoolResponse:
    endpoint_ids = list(dict.fromkeys(payload.endpoint_ids))
    await _validate_proxy_endpoint_ids(context, endpoint_ids)
    pool = ProxyPool(name=payload.name, is_active=payload.is_active)
    context.session.add(pool)
    await context.session.flush()
    for sort_order, endpoint_id in enumerate(endpoint_ids):
        context.session.add(ProxyPoolMember(pool_id=pool.id, endpoint_id=endpoint_id, sort_order=sort_order))
    try:
        await context.session.commit()
    except IntegrityError as exc:
        await context.session.rollback()
        if _is_missing_proxy_endpoint_error(exc):
            raise DashboardBadRequestError("Proxy endpoint not found", code="proxy_endpoint_not_found")
        raise
    await context.session.refresh(pool)
    return UpstreamProxyPoolResponse(
        id=pool.id,
        name=pool.name,
        is_active=pool.is_active,
        endpoint_ids=endpoint_ids,
    )


@router.post("/upstream-proxy/pools/{pool_id}/members", response_model=UpstreamProxyPoolResponse)
async def add_upstream_proxy_pool_member(
    pool_id: str,
    payload: UpstreamProxyPoolMemberRequest,
    _write_access=Depends(require_dashboard_write_access),
    context: SettingsContext = Depends(get_settings_context),
) -> UpstreamProxyPoolResponse:
    pool = await context.session.get(ProxyPool, pool_id)
    if pool is None:
        raise DashboardBadRequestError("Proxy pool not found", code="proxy_pool_not_found")
    await _validate_proxy_endpoint_ids(context, [payload.endpoint_id])
    await _validate_proxy_pool_member_is_unique(context, pool_id=pool_id, endpoint_id=payload.endpoint_id)
    context.session.add(
        ProxyPoolMember(
            pool_id=pool_id,
            endpoint_id=payload.endpoint_id,
            sort_order=payload.sort_order,
            weight=payload.weight,
            is_active=payload.is_active,
        )
    )
    try:
        await context.session.commit()
    except IntegrityError as exc:
        await context.session.rollback()
        if _is_missing_proxy_endpoint_error(exc):
            raise DashboardBadRequestError("Proxy endpoint not found", code="proxy_endpoint_not_found")
        if _is_duplicate_proxy_pool_member_error(exc):
            raise _duplicate_proxy_pool_member_error()
        raise
    # Pool membership is a route-resolver input: clear + durably bump before
    # responding (same contract as the account-binding upsert).
    await get_upstream_route_cache().invalidate()
    endpoint_ids = (
        (
            await context.session.execute(
                select(ProxyPoolMember.endpoint_id)
                .where(ProxyPoolMember.pool_id == pool_id)
                .order_by(ProxyPoolMember.sort_order.asc())
            )
        )
        .scalars()
        .all()
    )
    return UpstreamProxyPoolResponse(
        id=pool.id,
        name=pool.name,
        is_active=pool.is_active,
        endpoint_ids=list(endpoint_ids),
    )


async def _validate_proxy_endpoint_ids(context: SettingsContext, endpoint_ids: list[str]) -> None:
    if not endpoint_ids:
        return
    existing_ids = set(
        (await context.session.execute(select(ProxyEndpoint.id).where(ProxyEndpoint.id.in_(endpoint_ids))))
        .scalars()
        .all()
    )
    missing_ids = [endpoint_id for endpoint_id in endpoint_ids if endpoint_id not in existing_ids]
    if missing_ids:
        raise DashboardBadRequestError(
            f"Proxy endpoint not found: {', '.join(missing_ids)}",
            code="proxy_endpoint_not_found",
        )


async def _validate_proxy_pool_member_is_unique(
    context: SettingsContext,
    *,
    pool_id: str,
    endpoint_id: str,
) -> None:
    existing_id = (
        await context.session.execute(
            select(ProxyPoolMember.id)
            .where(ProxyPoolMember.pool_id == pool_id, ProxyPoolMember.endpoint_id == endpoint_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing_id is not None:
        raise _duplicate_proxy_pool_member_error()


async def _validate_proxy_pool_id(context: SettingsContext, pool_id: str | None) -> None:
    if pool_id is None:
        return
    if await context.session.get(ProxyPool, pool_id) is None:
        raise DashboardBadRequestError("Proxy pool not found", code="proxy_pool_not_found")


async def _get_account_or_error(context: SettingsContext, account_id: str) -> Account:
    account = await context.session.get(Account, account_id)
    # An account marked for background deletion is already deleted from the
    # operator's point of view: binding mutations must report not-found, as
    # the synchronous delete did once the row was removed.
    if account is None or account.delete_requested_at is not None:
        raise DashboardBadRequestError("Account not found", code="account_not_found")
    return account


def _is_missing_proxy_endpoint_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "foreign key" in message or "fk constraint" in message or "violates foreign key constraint" in message


def _is_duplicate_proxy_pool_member_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "uq_proxy_pool_members_pool_endpoint" in message
        or ("proxy_pool_members" in message and "unique" in message)
        or ("proxy_pool_members.pool_id" in message and "proxy_pool_members.endpoint_id" in message)
    )


def _duplicate_proxy_pool_member_error() -> DashboardBadRequestError:
    return DashboardBadRequestError(
        "Proxy endpoint is already a member of this pool",
        code="proxy_pool_member_duplicate",
    )


@router.put("/upstream-proxy/accounts/{account_id}/binding", response_model=AccountProxyBindingResponse)
async def put_account_proxy_binding(
    account_id: str,
    payload: AccountProxyBindingRequest,
    request: Request,
    _write_access=Depends(require_dashboard_write_access),
    context: SettingsContext = Depends(get_settings_context),
) -> AccountProxyBindingResponse:
    account = await _get_account_or_error(context, account_id)
    await _validate_proxy_pool_id(context, payload.pool_id)
    row = (
        (
            await context.session.execute(
                select(AccountProxyBinding).where(AccountProxyBinding.account_id == account_id).limit(1)
            )
        )
        .scalars()
        .one_or_none()
    )
    close_bridge_sessions = payload.is_active
    if row is None:
        row = AccountProxyBinding(account_id=account_id, pool_id=payload.pool_id, is_active=payload.is_active)
        context.session.add(row)
    else:
        close_bridge_sessions = row.pool_id != payload.pool_id or row.is_active != payload.is_active
        row.pool_id = payload.pool_id
        row.is_active = payload.is_active
    reactivated = False
    if payload.is_active and _account_proxy_binding_should_reactivate(account):
        account.status = AccountStatus.ACTIVE
        account.deactivation_reason = None
        account.reset_at = None
        account.blocked_at = None
        reactivated = True
    await context.session.commit()
    # Binding rows are a route-resolver input: clear this replica's route
    # cache and durably bump ``upstream_route`` before responding so no
    # request on the mutating replica resolves the pre-mutation binding.
    await get_upstream_route_cache().invalidate()
    if reactivated:
        # Invalidate + bump only after the status commit so peers (and this
        # replica's poller) never rebuild selection/routing inputs from the
        # pre-commit row. The coalesced ``account_selection`` bump enqueued by
        # ``invalidate()`` and the durable ``account_routing`` bump both fire
        # post-commit, matching AccountService.reactivate_account.
        clear_account_routing_unavailable(account_id)
        get_account_selection_cache().invalidate()
        await propagate_account_routing_change()
    if close_bridge_sessions:
        await get_proxy_service_for_app(request.app).close_http_bridge_sessions_for_account(account_id)
    await context.session.refresh(row)
    return AccountProxyBindingResponse(account_id=row.account_id, pool_id=row.pool_id, is_active=row.is_active)


def _proxy_endpoint_response(row: ProxyEndpoint) -> UpstreamProxyEndpointResponse:
    return UpstreamProxyEndpointResponse(
        id=row.id,
        name=row.name,
        scheme=row.scheme,
        host=row.host,
        port=row.port,
        username=row.username,
        is_active=row.is_active,
        plaintext_credentials=sends_plaintext_credentials(
            row.scheme, has_credentials=row.username is not None or row.password_encrypted is not None
        ),
    )


def _account_proxy_binding_should_reactivate(account: Account) -> bool:
    reason = account.deactivation_reason or ""
    return (
        account.status == AccountStatus.PAUSED
        and reason == IMPORT_PROXY_REQUIRED_PAUSE_REASON
        or account.status == AccountStatus.DEACTIVATED
        and (reason == "proxy_unreachable" or reason.startswith("proxy_unreachable:"))
    )


async def _probe_upstream_proxy_endpoint(endpoint) -> int:
    if endpoint.scheme.startswith("socks"):
        connector = ProxyConnector(
            host=endpoint.host,
            port=endpoint.port,
            proxy_type=ProxyType.SOCKS5,
            username=endpoint.username,
            password=endpoint.password,
            rdns=endpoint.proxy_url.split(":", 1)[0] == "socks5h",
            ssl=_shared_ssl_context(),
        )
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=UPSTREAM_PROXY_TEST_TIMEOUT_SECONDS),
            trust_env=False,
        ) as client:
            response = await client.get(UPSTREAM_PROXY_TEST_URL, allow_redirects=False)
            return response.status
    async with httpx.AsyncClient(
        proxy=endpoint.proxy_url,
        timeout=httpx.Timeout(UPSTREAM_PROXY_TEST_TIMEOUT_SECONDS),
        follow_redirects=False,
    ) as client:
        response = await client.get(UPSTREAM_PROXY_TEST_URL)
        return response.status_code


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.monotonic() - started) * 1000))


# C2-1 timeouts
# Dashboard-managed fields the timeout-invariant rules read: the C2-1 timeouts
# and request budgets plus the C2-2 account lease TTL (``account-lease-ttl-
# covers-*``), so one PUT-time check sees the merged effective values — a TTL
# below a dashboard budget and a budget above a dashboard TTL are both caught.
_TIMEOUT_INVARIANT_DASHBOARD_SETTINGS: tuple[str, ...] = (
    *DASHBOARD_TIMEOUT_SETTINGS,
    "proxy_account_lease_ttl_seconds",  # C2-2 routing/overload
)


# M4 model catalogue: per-model context window overrides. One dashboard row per
# slug (``model_context_window_overrides``); the
# ``CODEX_LB_MODEL_CONTEXT_WINDOW_OVERRIDES`` entry is the per-slug fallback.
MODEL_CONTEXT_WINDOW_OVERRIDES_PATH = "/model-context-window-overrides"
_MODEL_SLUG_MAX_LENGTH = 256


def _validate_model_slug(slug: str) -> str:
    # The raw segment is validated, never trimmed: silently storing " gpt-5.4 "
    # as "gpt-5.4" would make the row the operator sees disagree with the slug
    # they wrote, and the requirement rejects any slug containing whitespace.
    if (
        not slug
        or len(slug) > _MODEL_SLUG_MAX_LENGTH
        or any(character.isspace() or not character.isprintable() for character in slug)
    ):
        raise DashboardBadRequestError(
            f"Model slug must be 1-{_MODEL_SLUG_MAX_LENGTH} printable characters without whitespace",
            code="invalid_model_slug",
        )
    return slug


async def _model_context_window_overrides_response(context: SettingsContext) -> ModelContextWindowOverridesResponse:
    dashboard = await ModelContextWindowOverridesRepository(context.session).by_slug()
    resolved = resolve_context_window_overrides(dashboard, get_app_settings().model_context_window_overrides)
    return ModelContextWindowOverridesResponse(
        overrides=[
            ModelContextWindowOverrideResponse(
                slug=override.slug,
                context_window=override.context_window,
                source=override.source,
                env_value=override.env_value,
            )
            for override in resolved.values()
        ]
    )


@router.get(MODEL_CONTEXT_WINDOW_OVERRIDES_PATH, response_model=ModelContextWindowOverridesResponse)
async def get_model_context_window_overrides(
    context: SettingsContext = Depends(get_settings_context),
) -> ModelContextWindowOverridesResponse:
    return await _model_context_window_overrides_response(context)


@router.put(MODEL_CONTEXT_WINDOW_OVERRIDES_PATH + "/{slug:path}", response_model=ModelContextWindowOverridesResponse)
async def put_model_context_window_override(
    request: Request,
    slug: str,
    payload: ModelContextWindowOverrideUpsertRequest,
    _write_access=Depends(require_dashboard_write_access),
    context: SettingsContext = Depends(get_settings_context),
) -> ModelContextWindowOverridesResponse:
    normalized = _validate_model_slug(slug)
    await ModelContextWindowOverridesRepository(context.session).upsert(normalized, payload.context_window)
    # The catalog reads a cached snapshot of the rows: clear + durably bump
    # before responding so every replica reports the new window.
    await get_model_context_window_overrides_cache().invalidate()
    # Audited like every other dashboard settings write: this one changes what
    # the model catalog advertises to every client.
    AuditService.log_async(
        "settings_changed",
        actor_ip=request.client.host if request.client else None,
        details={"changed_fields": ["model_context_window_overrides"], "slug": normalized},
    )
    return await _model_context_window_overrides_response(context)


@router.delete(MODEL_CONTEXT_WINDOW_OVERRIDES_PATH + "/{slug:path}", response_model=ModelContextWindowOverridesResponse)
async def delete_model_context_window_override(
    request: Request,
    slug: str,
    _write_access=Depends(require_dashboard_write_access),
    context: SettingsContext = Depends(get_settings_context),
) -> ModelContextWindowOverridesResponse:
    normalized = _validate_model_slug(slug)
    if not await ModelContextWindowOverridesRepository(context.session).delete(normalized):
        raise DashboardNotFoundError(
            "Model context window override not found", code="model_context_window_override_not_found"
        )
    await get_model_context_window_overrides_cache().invalidate()
    AuditService.log_async(
        "settings_changed",
        actor_ip=request.client.host if request.client else None,
        details={"changed_fields": ["model_context_window_overrides"], "slug": normalized},
    )
    return await _model_context_window_overrides_response(context)


# end M4 model catalogue


def _proposed_timeout_settings(payload: DashboardSettingsUpdateRequest, current, startup_settings) -> dict[str, float]:
    """Effective timeout values after ``payload`` is applied: value = store, null = inherit, absent = current."""
    proposed: dict[str, float] = {}
    for name in _TIMEOUT_INVARIANT_DASHBOARD_SETTINGS:
        if name in payload.model_fields_set:
            value = getattr(payload, name)
            if value is None:  # inherit: the environment value, or the process default for a partial fake
                value = getattr(startup_settings, name, None)
                if value is None:
                    value = getattr(settings_module.get_settings(), name)
            proposed[name] = float(value)
        else:
            proposed[name] = float(getattr(current, name))
    return proposed


class _EffectiveTimeoutView:
    """``TimeoutSettings`` view: effective timeout values over the startup settings.

    Fields a startup-settings fake does not carry are read from the process
    ``Settings`` so constant-only rules keep evaluating.
    """

    def __init__(self, base: object, overrides: Mapping[str, float]) -> None:
        self._base = base
        self._overrides = overrides

    def __getattr__(self, name: str) -> object:
        if name in self._overrides:
            return self._overrides[name]
        if hasattr(self._base, name):
            return getattr(self._base, name)
        return getattr(settings_module.get_settings(), name)


def _validate_timeout_invariants(payload: DashboardSettingsUpdateRequest, current, startup_settings) -> None:
    """Reject a PUT that introduces a timeout-invariant violation against the effective values.

    The same rules as startup (``app.core.timeout_invariants``) are evaluated on
    the effective settings — dashboard value, else environment, else default —
    before and after the change; only violations the change introduces are
    rejected, so a deployment whose environment already violates a rule can
    still edit unrelated fields. The C2-2 account lease TTL takes part in the
    same evaluation, so lowering a budget below a dashboard TTL, or storing a
    TTL below an effective budget, is rejected on the merged values.
    """
    if not set(_TIMEOUT_INVARIANT_DASHBOARD_SETTINGS) & payload.model_fields_set:
        return
    before = _EffectiveTimeoutView(
        startup_settings, {name: float(getattr(current, name)) for name in _TIMEOUT_INVARIANT_DASHBOARD_SETTINGS}
    )
    after = _EffectiveTimeoutView(startup_settings, _proposed_timeout_settings(payload, current, startup_settings))
    before_ids = {violation.rule.id for violation in find_timeout_invariant_violations(cast(Any, before))}
    introduced = [
        violation
        for violation in find_timeout_invariant_violations(cast(Any, after))
        if violation.rule.id not in before_ids
    ]
    if introduced:
        raise DashboardBadRequestError(
            "; ".join(violation.format() for violation in introduced),
            code="timeout_invariant_violation",
        )


# R2 spool retention
class _ProposedSpoolRetentionInputs:
    """Effective floor inputs after ``payload`` is applied, over ``current``.

    The three inputs behave differently in the update request: the two reuse
    windows are non-nullable dashboard columns with no environment layer, so a
    ``None`` there only ever means "unchanged"; the bridge request budget is
    tri-state, so an explicit null returns it to the environment value.
    """

    __slots__ = ("_values",)

    def __init__(self, payload: DashboardSettingsUpdateRequest, current, startup_settings) -> None:
        values: dict[str, float] = {}
        for name in SPOOL_RETENTION_FLOOR_INPUTS:
            proposed = getattr(payload, name, None)
            inheritable = name in settings_module.Settings.model_fields
            if proposed is None and inheritable and name in payload.model_fields_set:
                # Explicit null on a tri-state field: inherit the environment
                # value (the process default for a partial startup fake).
                proposed = getattr(startup_settings, name, None)
                if proposed is None:
                    proposed = getattr(settings_module.get_settings(), name)
            values[name] = float(proposed if proposed is not None else getattr(current, name))
        self._values = values

    def __getattr__(self, name: str) -> object:
        try:
            return self._values[name]
        except KeyError as exc:  # keep ``getattr(obj, name, default)`` working
            raise AttributeError(name) from exc


def _proposed_spool_retention_seconds(payload: DashboardSettingsUpdateRequest, current, startup_settings) -> float:
    """Effective spool retention after ``payload``: value = store, null = inherit, absent = current."""
    if OPERATION_SPOOL_RETENTION_SETTING not in payload.model_fields_set:
        return float(getattr(current, OPERATION_SPOOL_RETENTION_SETTING))
    proposed = getattr(payload, OPERATION_SPOOL_RETENTION_SETTING)
    if proposed is not None:
        return float(proposed)
    inherited = current.provenance[OPERATION_SPOOL_RETENTION_SETTING]
    return float(resolve_inheritable(None, inherited.env_value, inherited.default).value)


def _validate_spool_retention_floor(payload: DashboardSettingsUpdateRequest, current, startup_settings) -> None:
    """Reject a PUT that would leave the operation spool shorter than its replay floor.

    The spool holds the raw request payload and the response events that
    durable bridge recovery replays, so it must outlive every window in which a
    spooled operation may still be read. Both the retention window and the floor
    are evaluated on the effective values (dashboard column, else environment,
    else code default) before and after the change.

    A configuration can already be below the floor without ever passing through
    here -- the environment alias alone decides it while the column is NULL --
    and that state must not make the dashboard read-only. The rule is therefore
    "no worse", not "bypass": while below the floor an update is accepted only
    when it leaves the retention no shorter and the floor no higher than it
    found them, so unrelated edits go through but deepening the violation does
    not. Startup warns about the state (``_report_dashboard_timeout_overrides``)
    so the first refusal is never a surprise.
    """
    if not {OPERATION_SPOOL_RETENTION_SETTING, *SPOOL_RETENTION_FLOOR_INPUTS} & payload.model_fields_set:
        return
    before_retention = float(getattr(current, OPERATION_SPOOL_RETENTION_SETTING))
    before_floor = operation_spool_retention_floor_seconds(current, startup_settings=startup_settings)
    after_inputs = _ProposedSpoolRetentionInputs(payload, current, startup_settings)
    after_retention = _proposed_spool_retention_seconds(payload, current, startup_settings)
    term, after_floor = binding_spool_retention_floor_term(after_inputs, startup_settings=startup_settings)
    if after_retention >= after_floor:
        return
    if after_retention >= before_retention and after_floor <= before_floor:
        # Neither side got worse. Only reachable from an already-violating
        # state: a healthy one has before_retention >= before_floor >=
        # after_floor > after_retention >= before_retention, a contradiction.
        return
    if before_retention < before_floor:
        raise DashboardBadRequestError(
            f"{OPERATION_SPOOL_RETENTION_SETTING} is already below its replay floor "
            f"({before_retention:g}s < {before_floor:g}s) and this change would deepen it "
            f"({before_retention:g}s -> {after_retention:g}s against a floor of "
            f"{before_floor:g}s -> {after_floor:g}s, bound by {term}); raise the retention to at least "
            f"{after_floor:g}s, or leave both no worse than they are",
            code="spool_retention_below_floor",
        )
    raise DashboardBadRequestError(
        f"{OPERATION_SPOOL_RETENTION_SETTING} must be at least {after_floor:g}s: the operation spool is replayed "
        f"for as long as {term} ({after_floor:g}s), so a shorter window deletes transcripts a recovery still needs",
        code="spool_retention_below_floor",
    )


# end R2 spool retention


def _proposed_reset_credit_polling_enabled(payload: DashboardSettingsUpdateRequest, current) -> bool:
    """M2 background jobs: the reset-credit polling toggle as it will be after this update.

    A value in the payload wins; an explicit null returns to the inherited
    environment / default value; an omitted field keeps the current effective
    value (dashboard column, else the deprecated env alias, else the default).
    """
    name = "rate_limit_reset_credits_refresh_enabled"
    if name not in payload.model_fields_set:
        return bool(getattr(current, name))
    proposed = getattr(payload, name)
    if proposed is not None:
        return bool(proposed)
    inherited = current.provenance[name]
    return bool(resolve_inheritable(None, inherited.env_value, inherited.default).value)


def _proposed_auto_redeem_enabled(payload: DashboardSettingsUpdateRequest, current) -> bool:
    """M2 background jobs: the auto-redeem opt-in as it will be after this update."""
    if payload.auto_redeem_reset_credits_before_expiry is None:
        return bool(current.auto_redeem_reset_credits_before_expiry)
    return bool(payload.auto_redeem_reset_credits_before_expiry)


def _timeout_field(payload: DashboardSettingsUpdateRequest, name: str) -> tuple[float | None, bool]:
    """(value to store, clear flag) for one tri-state timeout field of ``payload``."""
    if name not in payload.model_fields_set:
        return None, False
    value = getattr(payload, name)
    return value, value is None


# end C2-1 timeouts


@router.put("", response_model=DashboardSettingsResponse)
async def update_settings(
    request: Request,
    payload: DashboardSettingsUpdateRequest = Body(...),
    principal: DashboardPrincipal = Depends(require_dashboard_write_access),
    context: SettingsContext = Depends(get_settings_context),
) -> DashboardSettingsResponse:
    current = await context.service.get_settings(actor_user_id=principal.user_id)
    # The dashboard client submits the whole form on every save, so a security
    # field merely being present must not require security:write; only a value
    # that differs from what is stored does.
    security_changes = {
        name
        for name in payload.model_fields_set & SECURITY_SETTINGS_FIELDS
        if getattr(payload, name) is not None and getattr(payload, name) != getattr(current, name)
    }
    if security_changes:
        ensure_dashboard_permission(principal, Permission.SECURITY_WRITE)
        await ensure_step_up(request, principal, Permission.SECURITY_WRITE)
    if payload.expected_version is not None and payload.expected_version != current.version:
        raise DashboardSettingsConflictError(
            "Settings were modified since this form was loaded; reload and retry",
        )
    if (
        "upstream_proxy_default_pool_id" in payload.model_fields_set
        and payload.upstream_proxy_default_pool_id is not None
    ):
        await _validate_proxy_pool_id(context, payload.upstream_proxy_default_pool_id)
    overflow_provided = "subscription_overflow_source_id" in payload.model_fields_set
    overflow_source_id = (
        payload.subscription_overflow_source_id if overflow_provided else current.subscription_overflow_source_id
    )
    if (
        overflow_provided
        and overflow_source_id is not None
        and overflow_source_id != current.subscription_overflow_source_id
    ):
        validate_overflow_source(await ModelSourcesRepository(context.session).get_by_id(overflow_source_id))
    overflow_drain_until = resolve_drain_until(
        current.subscription_overflow_source_id,
        overflow_source_id,
        current.subscription_overflow_drain_until,
        utcnow(),
    )
    # The reset-credit refresh loop is the sole driver of automatic redemption,
    # so "auto-redeem on, polling off" is a setting that can never run. The gate
    # gets the effective (dashboard-aware) values this request would leave
    # behind and is symmetric: it refuses both the request that turns the
    # opt-in on and the one that turns polling off. A payload that only
    # re-saves an already inconsistent pair is still accepted, so unrelated
    # settings edits are never blocked by pre-existing state.
    proposed_polling_enabled = _proposed_reset_credit_polling_enabled(payload, current)
    proposed_auto_redeem_enabled = _proposed_auto_redeem_enabled(payload, current)
    if proposed_auto_redeem_enabled and not proposed_polling_enabled:
        enables_auto_redeem = not current.auto_redeem_reset_credits_before_expiry
        disables_polling = (
            "rate_limit_reset_credits_refresh_enabled" in payload.model_fields_set
            and current.rate_limit_reset_credits_refresh_enabled
        )
        if enables_auto_redeem or disables_polling:
            raise DashboardBadRequestError(
                "autoRedeemResetCreditsBeforeExpiry requires reset-credit polling; "
                "keep rateLimitResetCreditsRefreshEnabled on (Settings -> Advanced -> Background jobs), "
                "or turn the opt-in off in the same request",
                code="reset_credit_polling_disabled",
            )
    try:
        legacy_threshold_provided = payload.sticky_reallocation_budget_threshold_pct is not None
        primary_threshold_provided = payload.sticky_reallocation_primary_budget_threshold_pct is not None
        if legacy_threshold_provided and primary_threshold_provided:
            assert payload.sticky_reallocation_budget_threshold_pct is not None
            assert payload.sticky_reallocation_primary_budget_threshold_pct is not None
            if (
                payload.sticky_reallocation_budget_threshold_pct
                != payload.sticky_reallocation_primary_budget_threshold_pct
                and (
                    payload.sticky_reallocation_budget_threshold_pct != current.sticky_reallocation_budget_threshold_pct
                    or payload.sticky_reallocation_primary_budget_threshold_pct
                    != current.sticky_reallocation_primary_budget_threshold_pct
                )
            ):
                raise DashboardBadRequestError(
                    "stickyReallocationBudgetThresholdPct and "
                    "stickyReallocationPrimaryBudgetThresholdPct must match when both are provided",
                    code="conflicting_sticky_reallocation_thresholds",
                )

        resolved_primary_threshold = (
            payload.sticky_reallocation_primary_budget_threshold_pct
            if payload.sticky_reallocation_primary_budget_threshold_pct is not None
            else (
                payload.sticky_reallocation_budget_threshold_pct
                if payload.sticky_reallocation_budget_threshold_pct is not None
                else current.sticky_reallocation_primary_budget_threshold_pct
            )
        )
        resolved_legacy_threshold = (
            payload.sticky_reallocation_budget_threshold_pct
            if payload.sticky_reallocation_budget_threshold_pct is not None
            else resolved_primary_threshold
        )
        single_account_id = (
            payload.single_account_id if "single_account_id" in payload.model_fields_set else current.single_account_id
        )
        startup_settings = get_app_settings()
        stream_limit = (
            payload.proxy_account_stream_limit
            if payload.proxy_account_stream_limit is not None
            else startup_settings.proxy_account_stream_limit
            if "proxy_account_stream_limit" in payload.model_fields_set
            else current.proxy_account_stream_limit
        )
        stream_recovery_reserve = (
            payload.proxy_account_stream_recovery_reserve
            if payload.proxy_account_stream_recovery_reserve is not None
            else startup_settings.proxy_account_stream_recovery_reserve
            if "proxy_account_stream_recovery_reserve" in payload.model_fields_set
            else current.proxy_account_stream_recovery_reserve
        )
        cap_fields_changed = bool(
            {
                "proxy_account_stream_limit",
                "proxy_account_stream_recovery_reserve",
            }
            & payload.model_fields_set
        )
        if cap_fields_changed and stream_limit > 0 and stream_recovery_reserve > stream_limit:
            raise DashboardBadRequestError(
                "proxyAccountStreamRecoveryReserve must not exceed proxyAccountStreamLimit",
                code="invalid_proxy_account_stream_recovery_reserve",
            )
        _validate_timeout_invariants(payload, current, startup_settings)  # C2-1 timeouts + C2-2 lease TTL
        _validate_spool_retention_floor(payload, current, startup_settings)  # R2 spool retention
        timeout_fields = {name: _timeout_field(payload, name) for name in DASHBOARD_TIMEOUT_SETTINGS}
        updated = await context.service.update_settings(
            DashboardSettingsUpdateData(
                sticky_threads_enabled=(
                    payload.sticky_threads_enabled
                    if payload.sticky_threads_enabled is not None
                    else current.sticky_threads_enabled
                ),
                upstream_stream_transport=payload.upstream_stream_transport or current.upstream_stream_transport,
                prohibit_fast_mode=(
                    payload.prohibit_fast_mode if payload.prohibit_fast_mode is not None else current.prohibit_fast_mode
                ),
                http_downstream_transport_policy=(
                    payload.http_downstream_transport_policy or current.http_downstream_transport_policy
                ),
                thread_cache_identity_mode=_dashboard_value(payload, "thread_cache_identity_mode"),
                clear_thread_cache_identity_mode=_clears_dashboard_value(payload, "thread_cache_identity_mode"),
                proxy_account_response_create_limit=(
                    payload.proxy_account_response_create_limit
                    if "proxy_account_response_create_limit" in payload.model_fields_set
                    else None
                ),
                clear_proxy_account_response_create_limit=(
                    "proxy_account_response_create_limit" in payload.model_fields_set
                    and payload.proxy_account_response_create_limit is None
                ),
                proxy_account_stream_limit=(
                    payload.proxy_account_stream_limit
                    if "proxy_account_stream_limit" in payload.model_fields_set
                    else None
                ),
                clear_proxy_account_stream_limit=(
                    "proxy_account_stream_limit" in payload.model_fields_set
                    and payload.proxy_account_stream_limit is None
                ),
                proxy_account_stream_recovery_reserve=(
                    payload.proxy_account_stream_recovery_reserve
                    if "proxy_account_stream_recovery_reserve" in payload.model_fields_set
                    else None
                ),
                clear_proxy_account_stream_recovery_reserve=(
                    "proxy_account_stream_recovery_reserve" in payload.model_fields_set
                    and payload.proxy_account_stream_recovery_reserve is None
                ),
                proxy_api_key_fair_share_congestion_threshold_pct=(
                    payload.proxy_api_key_fair_share_congestion_threshold_pct
                    if "proxy_api_key_fair_share_congestion_threshold_pct" in payload.model_fields_set
                    else None
                ),
                clear_proxy_api_key_fair_share_congestion_threshold_pct=(
                    "proxy_api_key_fair_share_congestion_threshold_pct" in payload.model_fields_set
                    and payload.proxy_api_key_fair_share_congestion_threshold_pct is None
                ),
                # C2-2 routing/overload
                proxy_overload_isolation_seconds=_dashboard_value(payload, "proxy_overload_isolation_seconds"),
                clear_proxy_overload_isolation_seconds=_clears_dashboard_value(
                    payload, "proxy_overload_isolation_seconds"
                ),
                proxy_account_error_rate_weighting_enabled=_dashboard_value(
                    payload, "proxy_account_error_rate_weighting_enabled"
                ),
                clear_proxy_account_error_rate_weighting_enabled=_clears_dashboard_value(
                    payload, "proxy_account_error_rate_weighting_enabled"
                ),
                proxy_account_inflight_penalty_pct=_dashboard_value(payload, "proxy_account_inflight_penalty_pct"),
                clear_proxy_account_inflight_penalty_pct=_clears_dashboard_value(
                    payload, "proxy_account_inflight_penalty_pct"
                ),
                proxy_account_lease_token_weight=_dashboard_value(payload, "proxy_account_lease_token_weight"),
                clear_proxy_account_lease_token_weight=_clears_dashboard_value(
                    payload, "proxy_account_lease_token_weight"
                ),
                proxy_account_lease_ttl_seconds=_dashboard_value(payload, "proxy_account_lease_ttl_seconds"),
                clear_proxy_account_lease_ttl_seconds=_clears_dashboard_value(
                    payload, "proxy_account_lease_ttl_seconds"
                ),
                # end C2-2 routing/overload
                upstream_proxy_routing_enabled=(
                    payload.upstream_proxy_routing_enabled
                    if payload.upstream_proxy_routing_enabled is not None
                    else current.upstream_proxy_routing_enabled
                ),
                upstream_proxy_default_pool_id=(
                    payload.upstream_proxy_default_pool_id
                    if "upstream_proxy_default_pool_id" in payload.model_fields_set
                    else current.upstream_proxy_default_pool_id
                ),
                prefer_earlier_reset_accounts=(
                    payload.prefer_earlier_reset_accounts
                    if payload.prefer_earlier_reset_accounts is not None
                    else current.prefer_earlier_reset_accounts
                ),
                prefer_earlier_reset_window=payload.prefer_earlier_reset_window or current.prefer_earlier_reset_window,
                show_reset_credit_badges=(
                    payload.show_reset_credit_badges
                    if payload.show_reset_credit_badges is not None
                    else current.show_reset_credit_badges
                ),
                auto_redeem_reset_credits_before_expiry=(
                    payload.auto_redeem_reset_credits_before_expiry
                    if payload.auto_redeem_reset_credits_before_expiry is not None
                    else current.auto_redeem_reset_credits_before_expiry
                ),
                show_reset_credit_expiry_badge=(
                    payload.show_reset_credit_expiry_badge
                    if payload.show_reset_credit_expiry_badge is not None
                    else current.show_reset_credit_expiry_badge
                ),
                routing_strategy=payload.routing_strategy or current.routing_strategy,
                relative_availability_power=(
                    payload.relative_availability_power
                    if payload.relative_availability_power is not None
                    else current.relative_availability_power
                ),
                relative_availability_top_k=(
                    payload.relative_availability_top_k
                    if payload.relative_availability_top_k is not None
                    else current.relative_availability_top_k
                ),
                single_account_id=single_account_id,
                subscription_overflow_source_id=overflow_source_id,
                clear_subscription_overflow_source=overflow_provided and overflow_source_id is None,
                subscription_overflow_drain_until=overflow_drain_until,
                set_subscription_overflow_drain_until=(
                    overflow_drain_until != current.subscription_overflow_drain_until
                ),
                openai_cache_affinity_max_age_seconds=(
                    payload.openai_cache_affinity_max_age_seconds
                    if payload.openai_cache_affinity_max_age_seconds is not None
                    else current.openai_cache_affinity_max_age_seconds
                ),
                dashboard_session_ttl_seconds=(
                    payload.dashboard_session_ttl_seconds
                    if payload.dashboard_session_ttl_seconds is not None
                    else current.dashboard_session_ttl_seconds
                ),
                http_responses_session_bridge_prompt_cache_idle_ttl_seconds=(
                    payload.http_responses_session_bridge_prompt_cache_idle_ttl_seconds
                    if payload.http_responses_session_bridge_prompt_cache_idle_ttl_seconds is not None
                    else current.http_responses_session_bridge_prompt_cache_idle_ttl_seconds
                ),
                http_responses_session_bridge_gateway_safe_mode=(
                    payload.http_responses_session_bridge_gateway_safe_mode
                    if payload.http_responses_session_bridge_gateway_safe_mode is not None
                    else current.http_responses_session_bridge_gateway_safe_mode
                ),
                # M3 codex prewarm: tri-state via model_fields_set.
                http_responses_session_bridge_codex_prewarm_enabled=_dashboard_value(
                    payload, "http_responses_session_bridge_codex_prewarm_enabled"
                ),
                clear_http_responses_session_bridge_codex_prewarm_enabled=_clears_dashboard_value(
                    payload, "http_responses_session_bridge_codex_prewarm_enabled"
                ),
                # end M3 codex prewarm
                sticky_reallocation_budget_threshold_pct=resolved_legacy_threshold,
                sticky_reallocation_primary_budget_threshold_pct=resolved_primary_threshold,
                sticky_reallocation_secondary_budget_threshold_pct=(
                    payload.sticky_reallocation_secondary_budget_threshold_pct
                    if payload.sticky_reallocation_secondary_budget_threshold_pct is not None
                    else current.sticky_reallocation_secondary_budget_threshold_pct
                ),
                additional_quota_routing_policies=(
                    payload.additional_quota_routing_policies
                    if payload.additional_quota_routing_policies is not None
                    else current.additional_quota_routing_policies
                ),
                warmup_model=(payload.warmup_model if payload.warmup_model is not None else current.warmup_model),
                import_without_overwrite=(
                    payload.import_without_overwrite
                    if payload.import_without_overwrite is not None
                    else current.import_without_overwrite
                ),
                totp_required_on_login=(
                    payload.totp_required_on_login
                    if payload.totp_required_on_login is not None
                    else current.totp_required_on_login
                ),
                totp_required_for_admin_role=(
                    payload.totp_required_for_admin_role
                    if payload.totp_required_for_admin_role is not None
                    else current.totp_required_for_admin_role
                ),
                local_login_policy=(
                    payload.local_login_policy if payload.local_login_policy is not None else current.local_login_policy
                ),
                api_key_auth_enabled=(
                    payload.api_key_auth_enabled
                    if payload.api_key_auth_enabled is not None
                    else current.api_key_auth_enabled
                ),
                hide_upstream_quota_from_api_keys=(
                    payload.hide_upstream_quota_from_api_keys
                    if payload.hide_upstream_quota_from_api_keys is not None
                    else current.hide_upstream_quota_from_api_keys
                ),
                limit_warmup_enabled=(
                    payload.limit_warmup_enabled
                    if payload.limit_warmup_enabled is not None
                    else current.limit_warmup_enabled
                ),
                limit_warmup_windows=payload.limit_warmup_windows or current.limit_warmup_windows,
                limit_warmup_model=payload.limit_warmup_model or current.limit_warmup_model,
                limit_warmup_prompt=payload.limit_warmup_prompt or current.limit_warmup_prompt,
                limit_warmup_cooldown_seconds=(
                    payload.limit_warmup_cooldown_seconds
                    if payload.limit_warmup_cooldown_seconds is not None
                    else current.limit_warmup_cooldown_seconds
                ),
                limit_warmup_exhausted_threshold_percent=(
                    payload.limit_warmup_exhausted_threshold_percent
                    if payload.limit_warmup_exhausted_threshold_percent is not None
                    else current.limit_warmup_exhausted_threshold_percent
                ),
                limit_warmup_idle_threshold_percent=(
                    payload.limit_warmup_idle_threshold_percent
                    if payload.limit_warmup_idle_threshold_percent is not None
                    else current.limit_warmup_idle_threshold_percent
                ),
                limit_warmup_min_available_percent=(
                    payload.limit_warmup_min_available_percent
                    if payload.limit_warmup_min_available_percent is not None
                    else current.limit_warmup_min_available_percent
                ),
                weekly_pace_working_days=(
                    payload.weekly_pace_working_days
                    if payload.weekly_pace_working_days is not None
                    else current.weekly_pace_working_days
                ),
                weekly_pace_smoothing_minutes=(
                    payload.weekly_pace_smoothing_minutes
                    if payload.weekly_pace_smoothing_minutes is not None
                    else current.weekly_pace_smoothing_minutes
                ),
                guest_access_enabled=(
                    payload.guest_access_enabled
                    if payload.guest_access_enabled is not None
                    else current.guest_access_enabled
                ),
                limit_warmup_staggered_idle_enabled=(
                    payload.limit_warmup_staggered_idle_enabled
                    if payload.limit_warmup_staggered_idle_enabled is not None
                    else current.limit_warmup_staggered_idle_enabled
                ),
                request_log_retention_override_days=(
                    payload.request_log_retention_override_days
                    if "request_log_retention_override_days" in payload.model_fields_set
                    else None
                ),
                usage_history_retention_override_days=(
                    payload.usage_history_retention_override_days
                    if "usage_history_retention_override_days" in payload.model_fields_set
                    else None
                ),
                clear_request_log_retention_override=(
                    "request_log_retention_override_days" in payload.model_fields_set
                    and payload.request_log_retention_override_days is None
                ),
                clear_usage_history_retention_override=(
                    "usage_history_retention_override_days" in payload.model_fields_set
                    and payload.usage_history_retention_override_days is None
                ),
                # C2-3 resilience toggles: tri-state via model_fields_set.
                soft_drain_enabled=(
                    payload.soft_drain_enabled if "soft_drain_enabled" in payload.model_fields_set else None
                ),
                clear_soft_drain_enabled=(
                    "soft_drain_enabled" in payload.model_fields_set and payload.soft_drain_enabled is None
                ),
                deterministic_failover_enabled=(
                    payload.deterministic_failover_enabled
                    if "deterministic_failover_enabled" in payload.model_fields_set
                    else None
                ),
                clear_deterministic_failover_enabled=(
                    "deterministic_failover_enabled" in payload.model_fields_set
                    and payload.deterministic_failover_enabled is None
                ),
                circuit_breaker_enabled=(
                    payload.circuit_breaker_enabled if "circuit_breaker_enabled" in payload.model_fields_set else None
                ),
                clear_circuit_breaker_enabled=(
                    "circuit_breaker_enabled" in payload.model_fields_set and payload.circuit_breaker_enabled is None
                ),
                # M2 background jobs: tri-state via model_fields_set.
                auth_guardian_enabled=_dashboard_value(payload, "auth_guardian_enabled"),
                clear_auth_guardian_enabled=_clears_dashboard_value(payload, "auth_guardian_enabled"),
                automations_scheduler_enabled=_dashboard_value(payload, "automations_scheduler_enabled"),
                clear_automations_scheduler_enabled=_clears_dashboard_value(payload, "automations_scheduler_enabled"),
                rate_limit_reset_credits_refresh_enabled=_dashboard_value(
                    payload, "rate_limit_reset_credits_refresh_enabled"
                ),
                clear_rate_limit_reset_credits_refresh_enabled=_clears_dashboard_value(
                    payload, "rate_limit_reset_credits_refresh_enabled"
                ),
                # end M2 background jobs
                # M5 conversation archive: tri-state via model_fields_set.
                conversation_archive_enabled=(
                    payload.conversation_archive_enabled
                    if CONVERSATION_ARCHIVE_SETTING in payload.model_fields_set
                    else None
                ),
                clear_conversation_archive_enabled=(
                    CONVERSATION_ARCHIVE_SETTING in payload.model_fields_set
                    and payload.conversation_archive_enabled is None
                ),
                # end M5 conversation archive
                # R2 spool retention: tri-state via model_fields_set.
                http_responses_session_bridge_operation_spool_retention_seconds=_dashboard_value(
                    payload, OPERATION_SPOOL_RETENTION_SETTING
                ),
                clear_http_responses_session_bridge_operation_spool_retention_seconds=_clears_dashboard_value(
                    payload, OPERATION_SPOOL_RETENTION_SETTING
                ),
                # end R2 spool retention
                # C2-1 timeouts
                upstream_connect_timeout_seconds=timeout_fields["upstream_connect_timeout_seconds"][0],
                clear_upstream_connect_timeout_seconds=timeout_fields["upstream_connect_timeout_seconds"][1],
                proxy_request_budget_seconds=timeout_fields["proxy_request_budget_seconds"][0],
                clear_proxy_request_budget_seconds=timeout_fields["proxy_request_budget_seconds"][1],
                compact_request_budget_seconds=timeout_fields["compact_request_budget_seconds"][0],
                clear_compact_request_budget_seconds=timeout_fields["compact_request_budget_seconds"][1],
                transcription_request_budget_seconds=timeout_fields["transcription_request_budget_seconds"][0],
                clear_transcription_request_budget_seconds=timeout_fields["transcription_request_budget_seconds"][1],
                stream_idle_timeout_seconds=timeout_fields["stream_idle_timeout_seconds"][0],
                clear_stream_idle_timeout_seconds=timeout_fields["stream_idle_timeout_seconds"][1],
                proxy_downstream_websocket_idle_timeout_seconds=timeout_fields[
                    "proxy_downstream_websocket_idle_timeout_seconds"
                ][0],
                clear_proxy_downstream_websocket_idle_timeout_seconds=timeout_fields[
                    "proxy_downstream_websocket_idle_timeout_seconds"
                ][1],
                sse_keepalive_interval_seconds=timeout_fields["sse_keepalive_interval_seconds"][0],
                clear_sse_keepalive_interval_seconds=timeout_fields["sse_keepalive_interval_seconds"][1],
                # end C2-1 timeouts
                # M1 stream/bridge budgets (registered in DASHBOARD_TIMEOUT_SETTINGS,
                # so the PUT-time invariant check and the audit loop cover them).
                http_responses_stream_request_budget_seconds=timeout_fields[
                    "http_responses_stream_request_budget_seconds"
                ][0],
                clear_http_responses_stream_request_budget_seconds=timeout_fields[
                    "http_responses_stream_request_budget_seconds"
                ][1],
                http_responses_session_bridge_request_budget_seconds=timeout_fields[
                    "http_responses_session_bridge_request_budget_seconds"
                ][0],
                clear_http_responses_session_bridge_request_budget_seconds=timeout_fields[
                    "http_responses_session_bridge_request_budget_seconds"
                ][1],
                # end M1 stream/bridge budgets
            ),
            # CAS anchor: omitted fields above were merged from `current`
            # (version checked against expectedVersion when supplied), so the
            # repository must apply the UPDATE only if the row still carries
            # that version; a writer committing in between yields 409 instead
            # of silently reverting its fields.
            actor_user_id=principal.user_id,
            expected_version=current.version,
        )
    except BreakGlassRequiresTotpError as exc:
        # Before the generic ValueError arm: this refusal is a 409 whose body
        # names the account that would clear it, not a malformed-input 400.
        raise DashboardConflictError(
            str(exc),
            code="break_glass_requires_totp",
            param=exc.username,
            details={"username": exc.username},
        ) from exc
    except ValueError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_config") from exc

    upstream_route_inputs_changed = (
        current.upstream_proxy_routing_enabled != updated.upstream_proxy_routing_enabled
        or current.upstream_proxy_default_pool_id != updated.upstream_proxy_default_pool_id
    )
    # ``SettingsRepository.commit_refresh`` already cleared the route cache
    # synchronously between the commit and its refresh await (no concurrent
    # request can see the committed row alongside the stale cache); only the
    # durable cross-replica signals remain here.
    await get_settings_cache().invalidate()
    changed_fields = [
        field_name
        for field_name in (
            "sticky_threads_enabled",
            "upstream_stream_transport",
            "prohibit_fast_mode",
            "http_downstream_transport_policy",
            "thread_cache_identity_mode",
            "proxy_account_response_create_limit",
            "proxy_account_stream_limit",
            "proxy_account_stream_recovery_reserve",
            "proxy_api_key_fair_share_congestion_threshold_pct",
            *_ROUTING_OVERLOAD_SETTINGS,  # C2-2 routing/overload
            "upstream_proxy_routing_enabled",
            "upstream_proxy_default_pool_id",
            "prefer_earlier_reset_accounts",
            "prefer_earlier_reset_window",
            "show_reset_credit_badges",
            "auto_redeem_reset_credits_before_expiry",
            "show_reset_credit_expiry_badge",
            "routing_strategy",
            "relative_availability_power",
            "relative_availability_top_k",
            "single_account_id",
            "subscription_overflow_source_id",
            "subscription_overflow_drain_until",
            "openai_cache_affinity_max_age_seconds",
            "dashboard_session_ttl_seconds",
            "http_responses_session_bridge_prompt_cache_idle_ttl_seconds",
            "http_responses_session_bridge_gateway_safe_mode",
            "http_responses_session_bridge_codex_prewarm_enabled",  # M3 codex prewarm
            "sticky_reallocation_budget_threshold_pct",
            "sticky_reallocation_primary_budget_threshold_pct",
            "sticky_reallocation_secondary_budget_threshold_pct",
            "additional_quota_routing_policies",
            "warmup_model",
            "import_without_overwrite",
            "totp_required_on_login",
            "totp_required_for_admin_role",
            "local_login_policy",
            "api_key_auth_enabled",
            "hide_upstream_quota_from_api_keys",
            "limit_warmup_enabled",
            "limit_warmup_windows",
            "limit_warmup_model",
            "limit_warmup_prompt",
            "limit_warmup_cooldown_seconds",
            "limit_warmup_exhausted_threshold_percent",
            "limit_warmup_idle_threshold_percent",
            "limit_warmup_min_available_percent",
            "weekly_pace_working_days",
            "weekly_pace_smoothing_minutes",
            "guest_access_enabled",
            "limit_warmup_staggered_idle_enabled",
            "request_log_retention_override_days",
            "usage_history_retention_override_days",
            # C2-3 resilience toggles
            "soft_drain_enabled",
            "deterministic_failover_enabled",
            "circuit_breaker_enabled",
            *BACKGROUND_JOB_SETTINGS,  # M2 background jobs
            CONVERSATION_ARCHIVE_SETTING,  # M5 conversation archive
            OPERATION_SPOOL_RETENTION_SETTING,  # R2 spool retention
            *DASHBOARD_TIMEOUT_SETTINGS,  # C2-1 timeouts
        )
        if getattr(current, field_name) != getattr(updated, field_name)
    ]
    # C2-3 resilience toggles / M2 background jobs / M5 conversation archive:
    # storing the inherited value (or clearing it) changes ownership without
    # changing the effective value; audit that too. An effective archive flip
    # additionally gets its own audit line naming the actor (further below).
    for field_name in (
        *RESILIENCE_TOGGLE_SETTINGS,
        *BACKGROUND_JOB_SETTINGS,
        CONVERSATION_ARCHIVE_SETTING,
        OPERATION_SPOOL_RETENTION_SETTING,  # R2 spool retention
    ):
        if current.provenance[field_name] != updated.provenance[field_name] and field_name not in changed_fields:
            changed_fields.append(field_name)
    # C2-1 timeouts: a dashboard value equal to the inherited one still changes
    # the setting's owner (provenance source), which the audit log must record.
    for field_name in DASHBOARD_TIMEOUT_SETTINGS:
        if (
            field_name not in changed_fields
            and current.provenance[field_name].source != updated.provenance[field_name].source
        ):
            changed_fields.append(field_name)
    capacity_override_fields = (
        "proxy_account_response_create_limit",
        "proxy_account_stream_limit",
        "proxy_account_stream_recovery_reserve",
        "proxy_api_key_fair_share_congestion_threshold_pct",
    )
    for field_name in capacity_override_fields:
        override_field_name = f"{field_name}_override"
        if (
            getattr(current, field_name) == getattr(updated, field_name)
            and getattr(current, override_field_name) != getattr(updated, override_field_name)
            and field_name not in changed_fields
        ):
            changed_fields.append(field_name)
    # C2-2 routing/overload: a value that moved between layers without changing
    # the effective number (dashboard set to the inherited value, or cleared)
    # is still a change worth auditing.
    for field_name in _ROUTING_OVERLOAD_SETTINGS:
        if field_name not in changed_fields and current.provenance.get(field_name) != updated.provenance.get(
            field_name
        ):
            changed_fields.append(field_name)
    # end C2-2 routing/overload
    # M3 codex prewarm: storing the inherited value (or clearing it) changes
    # ownership without changing the effective value; audit that too.
    if "http_responses_session_bridge_codex_prewarm_enabled" not in changed_fields and current.provenance.get(
        "http_responses_session_bridge_codex_prewarm_enabled"
    ) != updated.provenance.get("http_responses_session_bridge_codex_prewarm_enabled"):
        changed_fields.append("http_responses_session_bridge_codex_prewarm_enabled")
    # end M3 codex prewarm
    if upstream_route_inputs_changed:
        # Durably bump ``upstream_route`` (with the coalesced retry fallback)
        # rather than relying solely on the ``settings`` bump issued above:
        # that bump is non-raising and enqueues no retry, so a transient write
        # failure would leave peers on the stale route outcome until the TTL
        # instead of the first recovered poll cycle. The re-clear inside
        # ``invalidate`` is harmless; the guarding clear already ran pre-await.
        await get_upstream_route_cache().invalidate()
    actor_ip = request.client.host if request.client else None
    AuditService.log_async(
        "settings_changed",
        actor_ip=actor_ip,
        actor=AuditActor.from_principal(principal),
        target=AuditTarget("settings", "dashboard"),
        details={"changed_fields": changed_fields},
    )
    if current.local_login_policy != updated.local_login_policy:
        # Who may use the local password form is a security decision with its
        # own event: ``settings_changed`` lists field names, this names values.
        AuditService.log_async(
            "login_policy_changed",
            actor_ip=actor_ip,
            actor=AuditActor.from_principal(principal),
            target=AuditTarget("settings", "local_login_policy"),
            details={"from": current.local_login_policy, "to": updated.local_login_policy},
            severity=AuditSeverity.WARNING,
        )
    # M5 conversation archive: enabling turns the proxy into a full
    # prompt/response recorder readable by the same dashboard admin, so every
    # effective on/off change is a dedicated audit event with the actor, not
    # just an entry in ``changed_fields``.
    if current.conversation_archive_enabled != updated.conversation_archive_enabled:
        AuditService.log_async(
            CONVERSATION_ARCHIVE_TOGGLED_ACTION,
            actor_ip=actor_ip,
            details={
                "enabled": updated.conversation_archive_enabled,
                "source": updated.provenance[CONVERSATION_ARCHIVE_SETTING].source,
                "actor": principal.actor,
                "actor_role": principal.role.value,
            },
        )
    # end M5 conversation archive
    return _dashboard_settings_response(updated, principal=principal)

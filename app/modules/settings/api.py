from __future__ import annotations

import ipaddress
import os
import socket
import time

import aiohttp
import httpx
from aiohttp_socks import ProxyConnector
from fastapi import APIRouter, Body, Depends, Request
from python_socks import ProxyType
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.audit.service import AuditService
from app.core.auth.dependencies import (
    require_dashboard_write_access,
    set_dashboard_error_format,
    validate_dashboard_session,
)
from app.core.clients.http import _build_ssl_context
from app.core.config.settings_cache import get_settings_cache
from app.core.crypto import TokenEncryptor
from app.core.exceptions import DashboardBadRequestError, DashboardSettingsConflictError
from app.core.upstream_proxy import resolve_proxy_endpoint
from app.core.upstream_proxy.cache import get_upstream_route_cache
from app.db.models import Account, AccountProxyBinding, AccountStatus, ProxyEndpoint, ProxyPool, ProxyPoolMember
from app.dependencies import SettingsContext, get_proxy_service_for_app, get_settings_context
from app.modules.proxy.account_cache import (
    clear_account_routing_unavailable,
    get_account_selection_cache,
    propagate_account_routing_change,
)
from app.modules.settings.schemas import (
    AccountProxyBindingRequest,
    AccountProxyBindingResponse,
    AdditionalQuotaPolicy,
    DashboardSettingsResponse,
    DashboardSettingsUpdateRequest,
    RuntimeConnectAddressResponse,
    UpstreamProxyAdminResponse,
    UpstreamProxyEndpointCreateRequest,
    UpstreamProxyEndpointResponse,
    UpstreamProxyEndpointTestResponse,
    UpstreamProxyPoolCreateRequest,
    UpstreamProxyPoolMemberRequest,
    UpstreamProxyPoolResponse,
)
from app.modules.settings.service import DashboardSettingsUpdateData
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
    override = os.getenv("CODEX_LB_CONNECT_ADDRESS", "").strip()
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


def _dashboard_settings_response(settings) -> DashboardSettingsResponse:
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
        proxy_account_response_create_limit=settings.proxy_account_response_create_limit,
        proxy_account_stream_limit=settings.proxy_account_stream_limit,
        proxy_account_stream_recovery_reserve=settings.proxy_account_stream_recovery_reserve,
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
        openai_cache_affinity_max_age_seconds=settings.openai_cache_affinity_max_age_seconds,
        dashboard_session_ttl_seconds=settings.dashboard_session_ttl_seconds,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=settings.http_responses_session_bridge_prompt_cache_idle_ttl_seconds,
        http_responses_session_bridge_gateway_safe_mode=settings.http_responses_session_bridge_gateway_safe_mode,
        sticky_reallocation_budget_threshold_pct=settings.sticky_reallocation_budget_threshold_pct,
        sticky_reallocation_primary_budget_threshold_pct=settings.sticky_reallocation_primary_budget_threshold_pct,
        sticky_reallocation_secondary_budget_threshold_pct=settings.sticky_reallocation_secondary_budget_threshold_pct,
        additional_quota_routing_policies=settings.additional_quota_routing_policies,
        additional_quota_policies=additional_quota_policies,
        warmup_model=settings.warmup_model,
        import_without_overwrite=settings.import_without_overwrite,
        totp_required_on_login=settings.totp_required_on_login,
        totp_configured=settings.totp_configured,
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
        version=settings.version,
    )


@router.get("", response_model=DashboardSettingsResponse)
async def get_settings(
    context: SettingsContext = Depends(get_settings_context),
) -> DashboardSettingsResponse:
    settings = await context.service.get_settings()
    return _dashboard_settings_response(settings)


@router.get("/runtime/connect-address", response_model=RuntimeConnectAddressResponse)
async def get_runtime_connect_address(request: Request) -> RuntimeConnectAddressResponse:
    return RuntimeConnectAddressResponse(connect_address=_resolve_runtime_connect_address(request))


@router.get("/upstream-proxy", response_model=UpstreamProxyAdminResponse)
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
    _write_access=Depends(require_dashboard_write_access),
    context: SettingsContext = Depends(get_settings_context),
) -> UpstreamProxyEndpointResponse:
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
    endpoint = resolve_proxy_endpoint(row, encryptor=TokenEncryptor())
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
    if account is None:
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
            ssl=_build_ssl_context(),
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


@router.put("", response_model=DashboardSettingsResponse)
async def update_settings(
    request: Request,
    payload: DashboardSettingsUpdateRequest = Body(...),
    _write_access=Depends(require_dashboard_write_access),
    context: SettingsContext = Depends(get_settings_context),
) -> DashboardSettingsResponse:
    current = await context.service.get_settings()
    if payload.expected_version is not None and payload.expected_version != current.version:
        raise DashboardSettingsConflictError(
            "Settings were modified since this form was loaded; reload and retry",
        )
    if (
        "upstream_proxy_default_pool_id" in payload.model_fields_set
        and payload.upstream_proxy_default_pool_id is not None
    ):
        await _validate_proxy_pool_id(context, payload.upstream_proxy_default_pool_id)
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
        stream_limit = (
            payload.proxy_account_stream_limit
            if payload.proxy_account_stream_limit is not None
            else current.proxy_account_stream_limit
        )
        stream_recovery_reserve = (
            payload.proxy_account_stream_recovery_reserve
            if payload.proxy_account_stream_recovery_reserve is not None
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
                proxy_account_response_create_limit=(
                    payload.proxy_account_response_create_limit
                    if "proxy_account_response_create_limit" in payload.model_fields_set
                    else None
                ),
                proxy_account_stream_limit=(
                    payload.proxy_account_stream_limit
                    if "proxy_account_stream_limit" in payload.model_fields_set
                    else None
                ),
                proxy_account_stream_recovery_reserve=(
                    payload.proxy_account_stream_recovery_reserve
                    if "proxy_account_stream_recovery_reserve" in payload.model_fields_set
                    else None
                ),
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
            ),
            # CAS anchor: omitted fields above were merged from `current`
            # (version checked against expectedVersion when supplied), so the
            # repository must apply the UPDATE only if the row still carries
            # that version; a writer committing in between yields 409 instead
            # of silently reverting its fields.
            expected_version=current.version,
        )
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
            "proxy_account_response_create_limit",
            "proxy_account_stream_limit",
            "proxy_account_stream_recovery_reserve",
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
            "openai_cache_affinity_max_age_seconds",
            "dashboard_session_ttl_seconds",
            "http_responses_session_bridge_prompt_cache_idle_ttl_seconds",
            "http_responses_session_bridge_gateway_safe_mode",
            "sticky_reallocation_budget_threshold_pct",
            "sticky_reallocation_primary_budget_threshold_pct",
            "sticky_reallocation_secondary_budget_threshold_pct",
            "additional_quota_routing_policies",
            "warmup_model",
            "import_without_overwrite",
            "totp_required_on_login",
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
        )
        if getattr(current, field_name) != getattr(updated, field_name)
    ]
    if upstream_route_inputs_changed:
        # Durably bump ``upstream_route`` (with the coalesced retry fallback)
        # rather than relying solely on the ``settings`` bump issued above:
        # that bump is non-raising and enqueues no retry, so a transient write
        # failure would leave peers on the stale route outcome until the TTL
        # instead of the first recovered poll cycle. The re-clear inside
        # ``invalidate`` is harmless; the guarding clear already ran pre-await.
        await get_upstream_route_cache().invalidate()
    AuditService.log_async(
        "settings_changed",
        actor_ip=request.client.host if request.client else None,
        details={"changed_fields": changed_fields},
    )
    return _dashboard_settings_response(updated)

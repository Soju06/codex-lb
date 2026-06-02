from __future__ import annotations

import ipaddress
import os
import socket

from fastapi import APIRouter, Body, Depends, Request

from app.core.audit.service import AuditService
from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.core.config.settings_cache import get_settings_cache
from app.core.exceptions import DashboardBadRequestError
from app.dependencies import SettingsContext, get_settings_context
from app.modules.settings.schemas import (
    AdditionalQuotaPolicy,
    DashboardSettingsResponse,
    DashboardSettingsUpdateRequest,
    RuntimeConnectAddressResponse,
)
from app.modules.settings.service import DashboardSettingsUpdateData
from app.modules.usage.additional_quota_keys import (
    get_additional_quota_routing_policy,
    list_additional_quota_definitions,
)

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


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
        prefer_earlier_reset_accounts=settings.prefer_earlier_reset_accounts,
        prefer_earlier_reset_window=settings.prefer_earlier_reset_window,
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
        limit_warmup_enabled=settings.limit_warmup_enabled,
        limit_warmup_windows=settings.limit_warmup_windows,
        limit_warmup_model=settings.limit_warmup_model,
        limit_warmup_prompt=settings.limit_warmup_prompt,
        limit_warmup_cooldown_seconds=settings.limit_warmup_cooldown_seconds,
        limit_warmup_min_available_percent=settings.limit_warmup_min_available_percent,
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


@router.put("", response_model=DashboardSettingsResponse)
async def update_settings(
    request: Request,
    payload: DashboardSettingsUpdateRequest = Body(...),
    context: SettingsContext = Depends(get_settings_context),
) -> DashboardSettingsResponse:
    current = await context.service.get_settings()
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
        updated = await context.service.update_settings(
            DashboardSettingsUpdateData(
                sticky_threads_enabled=(
                    payload.sticky_threads_enabled
                    if payload.sticky_threads_enabled is not None
                    else current.sticky_threads_enabled
                ),
                upstream_stream_transport=payload.upstream_stream_transport or current.upstream_stream_transport,
                prefer_earlier_reset_accounts=(
                    payload.prefer_earlier_reset_accounts
                    if payload.prefer_earlier_reset_accounts is not None
                    else current.prefer_earlier_reset_accounts
                ),
                prefer_earlier_reset_window=payload.prefer_earlier_reset_window or current.prefer_earlier_reset_window,
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
                limit_warmup_min_available_percent=(
                    payload.limit_warmup_min_available_percent
                    if payload.limit_warmup_min_available_percent is not None
                    else current.limit_warmup_min_available_percent
                ),
            )
        )
    except ValueError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_config") from exc

    await get_settings_cache().invalidate()
    changed_fields = [
        field_name
        for field_name in (
            "sticky_threads_enabled",
            "upstream_stream_transport",
            "prefer_earlier_reset_accounts",
            "prefer_earlier_reset_window",
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
            "limit_warmup_enabled",
            "limit_warmup_windows",
            "limit_warmup_model",
            "limit_warmup_prompt",
            "limit_warmup_cooldown_seconds",
            "limit_warmup_min_available_percent",
        )
        if getattr(current, field_name) != getattr(updated, field_name)
    ]
    AuditService.log_async(
        "settings_changed",
        actor_ip=request.client.host if request.client else None,
        details={"changed_fields": changed_fields},
    )
    return _dashboard_settings_response(updated)

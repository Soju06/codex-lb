from __future__ import annotations

import ipaddress
import os
import socket

from fastapi import APIRouter, Body, Depends, Request

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.core.config.settings_cache import get_settings_cache
from app.core.exceptions import DashboardBadRequestError
from app.dependencies import SettingsContext, get_settings_context
from app.modules.settings.schemas import (
    DashboardSettingsResponse,
    DashboardSettingsUpdateRequest,
    RuntimeConnectAddressResponse,
)
from app.modules.settings.service import DashboardSettingsUpdateData

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


@router.get("", response_model=DashboardSettingsResponse)
async def get_settings(
    context: SettingsContext = Depends(get_settings_context),
) -> DashboardSettingsResponse:
    settings = await context.service.get_settings()
    return DashboardSettingsResponse(
        sticky_threads_enabled=settings.sticky_threads_enabled,
        upstream_stream_transport=settings.upstream_stream_transport,
        prefer_earlier_reset_accounts=settings.prefer_earlier_reset_accounts,
        routing_strategy=settings.routing_strategy,
        openai_cache_affinity_max_age_seconds=settings.openai_cache_affinity_max_age_seconds,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=settings.http_responses_session_bridge_prompt_cache_idle_ttl_seconds,
        sticky_reallocation_budget_threshold_pct=settings.sticky_reallocation_budget_threshold_pct,
        import_without_overwrite=settings.import_without_overwrite,
        totp_required_on_login=settings.totp_required_on_login,
        totp_configured=settings.totp_configured,
        api_key_auth_enabled=settings.api_key_auth_enabled,
    )


@router.get("/runtime/connect-address", response_model=RuntimeConnectAddressResponse)
async def get_runtime_connect_address(request: Request) -> RuntimeConnectAddressResponse:
    return RuntimeConnectAddressResponse(connect_address=_resolve_runtime_connect_address(request))


@router.put("", response_model=DashboardSettingsResponse)
async def update_settings(
    payload: DashboardSettingsUpdateRequest = Body(...),
    context: SettingsContext = Depends(get_settings_context),
) -> DashboardSettingsResponse:
    current = await context.service.get_settings()
    try:
        updated = await context.service.update_settings(
            DashboardSettingsUpdateData(
                sticky_threads_enabled=payload.sticky_threads_enabled,
                upstream_stream_transport=payload.upstream_stream_transport or current.upstream_stream_transport,
                prefer_earlier_reset_accounts=payload.prefer_earlier_reset_accounts,
                routing_strategy=payload.routing_strategy or current.routing_strategy,
                openai_cache_affinity_max_age_seconds=(
                    payload.openai_cache_affinity_max_age_seconds
                    if payload.openai_cache_affinity_max_age_seconds is not None
                    else current.openai_cache_affinity_max_age_seconds
                ),
                http_responses_session_bridge_prompt_cache_idle_ttl_seconds=(
                    payload.http_responses_session_bridge_prompt_cache_idle_ttl_seconds
                    if payload.http_responses_session_bridge_prompt_cache_idle_ttl_seconds is not None
                    else current.http_responses_session_bridge_prompt_cache_idle_ttl_seconds
                ),
                sticky_reallocation_budget_threshold_pct=(
                    payload.sticky_reallocation_budget_threshold_pct
                    if payload.sticky_reallocation_budget_threshold_pct is not None
                    else current.sticky_reallocation_budget_threshold_pct
                ),
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
            )
        )
    except ValueError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_totp_config") from exc

    await get_settings_cache().invalidate()
    return DashboardSettingsResponse(
        sticky_threads_enabled=updated.sticky_threads_enabled,
        upstream_stream_transport=updated.upstream_stream_transport,
        prefer_earlier_reset_accounts=updated.prefer_earlier_reset_accounts,
        routing_strategy=updated.routing_strategy,
        openai_cache_affinity_max_age_seconds=updated.openai_cache_affinity_max_age_seconds,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=updated.http_responses_session_bridge_prompt_cache_idle_ttl_seconds,
        sticky_reallocation_budget_threshold_pct=updated.sticky_reallocation_budget_threshold_pct,
        import_without_overwrite=updated.import_without_overwrite,
        totp_required_on_login=updated.totp_required_on_login,
        totp_configured=updated.totp_configured,
        api_key_auth_enabled=updated.api_key_auth_enabled,
    )

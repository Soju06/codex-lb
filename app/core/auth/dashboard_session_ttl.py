from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from starlette.requests import HTTPConnection

DEFAULT_DASHBOARD_SESSION_TTL_SECONDS = 365 * 24 * 60 * 60
REMOTE_DASHBOARD_SESSION_TTL_SECONDS = 12 * 60 * 60
LONG_DASHBOARD_SESSION_TTL_THRESHOLD_SECONDS = 30 * 24 * 60 * 60
_FORWARDED_CLIENT_IP_HEADERS = (
    "x-forwarded-for",
    "forwarded",
    "x-real-ip",
    "true-client-ip",
    "cf-connecting-ip",
)


def resolve_dashboard_session_ttl_seconds(request: HTTPConnection, configured_ttl_seconds: int) -> int:
    if configured_ttl_seconds <= LONG_DASHBOARD_SESSION_TTL_THRESHOLD_SECONDS:
        return configured_ttl_seconds
    if _allows_long_local_dashboard_session(request):
        return configured_ttl_seconds
    return REMOTE_DASHBOARD_SESSION_TTL_SECONDS


def _allows_long_local_dashboard_session(request: HTTPConnection) -> bool:
    from app.core.auth.dashboard_mode import DashboardAuthMode

    settings = _get_settings()
    if settings.dashboard_auth_mode != DashboardAuthMode.STANDARD:
        return False
    if settings.firewall_trust_proxy_headers:
        return False
    if _is_local_request(request):
        return True
    if not settings.dashboard_trust_loopback_host_header_for_long_sessions:
        return False
    if any(request.headers.get(header) for header in _FORWARDED_CLIENT_IP_HEADERS):
        return False
    return _uses_loopback_dashboard_url(request)


def _get_settings() -> Any:
    from app.core.config.settings import get_settings

    return get_settings()


def _is_local_request(request: HTTPConnection) -> bool:
    from app.core.request_locality import is_local_request

    return is_local_request(request)


def _uses_loopback_dashboard_url(request: HTTPConnection) -> bool:
    from app.core.request_locality import is_local_host

    return is_local_host(request.url.hostname)

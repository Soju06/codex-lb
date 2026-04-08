from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address, ip_network

from fastapi import Request

_HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_FORBIDDEN_PROXY_AUTH_HEADERS = frozenset(
    {
        "authorization",
        "connection",
        "content-length",
        "forwarded",
        "host",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
    }
)


class DashboardAuthMode(StrEnum):
    STANDARD = "standard"
    TRUSTED_HEADER = "trusted_header"
    DISABLED = "disabled"


@dataclass(slots=True, frozen=True)
class DashboardRequestAuth:
    mode: DashboardAuthMode
    actor: str | None = None


def normalize_dashboard_auth_proxy_header(value: str) -> str:
    header = value.strip()
    if not header:
        raise ValueError("dashboard_auth_proxy_header must not be empty")
    if not _HEADER_NAME_PATTERN.fullmatch(header):
        raise ValueError("dashboard_auth_proxy_header must be a valid HTTP header name")
    if header.lower() in _FORBIDDEN_PROXY_AUTH_HEADERS:
        raise ValueError(f"dashboard_auth_proxy_header must not use reserved header '{header}'")
    return header


def _trusted_proxy_networks() -> tuple[IPv4Network | IPv6Network, ...]:
    from app.core.config.settings import get_settings

    settings = get_settings()
    return _parse_trusted_proxy_networks(tuple(settings.firewall_trusted_proxy_cidrs))


@lru_cache(maxsize=16)
def _parse_trusted_proxy_networks(
    cidrs: tuple[str, ...],
) -> tuple[IPv4Network | IPv6Network, ...]:
    return tuple(ip_network(cidr, strict=False) for cidr in cidrs)


def get_dashboard_request_auth(request: Request) -> DashboardRequestAuth | None:
    cached = getattr(request.state, "dashboard_request_auth", None)
    if isinstance(cached, DashboardRequestAuth):
        return cached

    from app.core.config.settings import get_settings

    settings = get_settings()
    auth: DashboardRequestAuth | None = None
    if settings.dashboard_auth_mode == DashboardAuthMode.DISABLED:
        auth = DashboardRequestAuth(mode=DashboardAuthMode.DISABLED)
    elif settings.dashboard_auth_mode == DashboardAuthMode.TRUSTED_HEADER:
        auth = _get_trusted_header_auth(request)

    if auth is not None:
        request.state.dashboard_request_auth = auth
    return auth


def password_management_enabled(mode: DashboardAuthMode) -> bool:
    return mode != DashboardAuthMode.DISABLED


def _get_trusted_header_auth(request: Request) -> DashboardRequestAuth | None:
    from app.core.config.settings import get_settings

    settings = get_settings()
    client_host = request.client.host if request.client else None
    if not client_host or not settings.firewall_trust_proxy_headers:
        return None
    if not _is_trusted_proxy_source(client_host, _trusted_proxy_networks()):
        return None

    raw_actor = request.headers.get(settings.dashboard_auth_proxy_header)
    if raw_actor is None:
        return None

    actor = raw_actor.strip()
    if not actor:
        return None
    return DashboardRequestAuth(mode=DashboardAuthMode.TRUSTED_HEADER, actor=actor)


def _is_trusted_proxy_source(
    source_ip: str,
    trusted_proxy_networks: tuple[IPv4Network | IPv6Network, ...],
) -> bool:
    try:
        candidate = ip_address(source_ip)
    except ValueError:
        return False

    if not trusted_proxy_networks:
        return False
    return any(_network_contains(network, candidate) for network in trusted_proxy_networks)


def _network_contains(
    network: IPv4Network | IPv6Network,
    candidate: IPv4Address | IPv6Address,
) -> bool:
    return candidate.version == network.version and candidate in network

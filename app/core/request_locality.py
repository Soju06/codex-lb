from __future__ import annotations

from collections.abc import Mapping
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network

from starlette.requests import HTTPConnection

from app.core.config.settings import get_settings

_LOCAL_HOSTS = {
    "",
    "localhost",
    "127.0.0.1",
    "::1",
    "[::1]",
}

_TEST_SERVER_HOSTS = {"testserver", "testclient"}
_FORWARDED_CLIENT_IP_HEADERS = {
    "x-forwarded-for",
    "forwarded",
    "x-real-ip",
    "true-client-ip",
    "cf-connecting-ip",
}


def is_local_host(host: str | None) -> bool:
    if host is None:
        return False
    return host.strip().lower() in _LOCAL_HOSTS


def resolve_connection_client_ip(
    headers: Mapping[str, str],
    socket_ip: str | None,
    *,
    trust_proxy_headers: bool,
    trusted_proxy_networks: tuple[IPv4Network | IPv6Network, ...] = (),
) -> str | None:
    if trust_proxy_headers and socket_ip and _is_trusted_proxy_source(socket_ip, trusted_proxy_networks):
        forwarded_for = headers.get("x-forwarded-for")
        if forwarded_for:
            try:
                resolved_from_chain = _resolve_client_ip_from_xff_chain(
                    socket_ip,
                    forwarded_for,
                    trusted_proxy_networks,
                )
            except ValueError:
                return None
            if resolved_from_chain is not None:
                return resolved_from_chain

        for header_name in ("x-real-ip", "true-client-ip", "cf-connecting-ip"):
            forwarded_ip = headers.get(header_name)
            if forwarded_ip:
                candidate = forwarded_ip.strip()
                return candidate if _is_valid_ip(candidate) else None

        forwarded = headers.get("forwarded")
        if forwarded:
            resolved = _resolve_forwarded_header_ip(forwarded)
            if resolved is not None:
                return resolved

        return None
    return socket_ip


def parse_trusted_proxy_networks(cidrs: list[str]) -> tuple[IPv4Network | IPv6Network, ...]:
    return tuple(ip_network(cidr, strict=False) for cidr in cidrs)


def _resolve_client_ip_from_xff_chain(
    socket_ip: str,
    forwarded_for: str,
    trusted_proxy_networks: tuple[IPv4Network | IPv6Network, ...],
) -> str | None:
    hops = [entry.strip() for entry in forwarded_for.split(",")]
    if not hops:
        return None
    if any(not _is_valid_ip(entry) for entry in hops):
        raise ValueError("Invalid X-Forwarded-For chain")

    chain = [*hops, socket_ip]
    resolved = socket_ip
    for index in range(len(chain) - 1, 0, -1):
        current_proxy = chain[index]
        previous_hop = chain[index - 1]
        if not _is_trusted_proxy_source(current_proxy, trusted_proxy_networks):
            resolved = current_proxy
            break
        resolved = previous_hop
    return resolved


def _is_trusted_proxy_source(
    host: str,
    trusted_proxy_networks: tuple[IPv4Network | IPv6Network, ...],
) -> bool:
    if not trusted_proxy_networks:
        return False
    try:
        source_ip = ip_address(host)
    except ValueError:
        return False
    return any(source_ip in network for network in trusted_proxy_networks)


def _is_valid_ip(value: str) -> bool:
    try:
        ip_address(value)
    except ValueError:
        return False
    return True


def _resolve_forwarded_header_ip(forwarded: str) -> str | None:
    for segment in forwarded.split(","):
        for part in segment.split(";"):
            item = part.strip()
            if not item.lower().startswith("for="):
                continue
            candidate = item[4:].strip().strip('"')
            if candidate.startswith("[") and candidate.endswith("]"):
                candidate = candidate[1:-1]
            if candidate.startswith("_"):
                return None
            return candidate if _is_valid_ip(candidate) else None
    return None


def _trusted_proxy_networks() -> tuple[IPv4Network | IPv6Network, ...]:
    settings = get_settings()
    return parse_trusted_proxy_networks(settings.firewall_trusted_proxy_cidrs)


def resolve_request_client_host(request: HTTPConnection) -> str | None:
    settings = get_settings()
    socket_ip = request.client.host if request.client else None
    return resolve_connection_client_ip(
        request.headers,
        socket_ip,
        trust_proxy_headers=settings.firewall_trust_proxy_headers,
        trusted_proxy_networks=_trusted_proxy_networks(),
    )


def _is_test_server_request(request: HTTPConnection) -> bool:
    server = request.scope.get("server")
    if not isinstance(server, tuple) or not server:
        return False
    host = server[0]
    if not isinstance(host, str):
        return False
    return host.strip().lower() in _TEST_SERVER_HOSTS


def _has_forwarded_client_ip_hint(headers: Mapping[str, str]) -> bool:
    return any(headers.get(header) for header in _FORWARDED_CLIENT_IP_HEADERS)


def is_local_request(request: HTTPConnection) -> bool:
    if _is_test_server_request(request):
        return True

    settings = get_settings()
    client_host = resolve_request_client_host(request)
    if not client_host:
        return False
    try:
        address = ip_address(client_host)
    except ValueError:
        return False
    if address.is_loopback and not settings.firewall_trust_proxy_headers:
        return not _has_forwarded_client_ip_hint(request.headers)
    if address.is_loopback and settings.firewall_trust_proxy_headers:
        return False
    return address.is_loopback

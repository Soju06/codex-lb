from __future__ import annotations

import socket
import struct
from collections.abc import Mapping
from functools import lru_cache
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


def _request_socket_host(request: HTTPConnection) -> str | None:
    return request.client.host if request.client else None


def _single_host_network(host: str) -> IPv4Network | IPv6Network | None:
    try:
        address = ip_address(host)
    except ValueError:
        return None
    return ip_network(f"{address}/{address.max_prefixlen}", strict=False)


def _read_default_ipv4_gateway() -> str | None:
    try:
        with open("/proc/net/route", encoding="utf-8") as route_file:
            next(route_file, None)
            for line in route_file:
                fields = line.split()
                if len(fields) < 4 or fields[1] != "00000000":
                    continue
                gateway = int(fields[2], 16).to_bytes(4, "little")
                return str(ip_address(gateway))
    except OSError:
        return None
    return None


def _read_default_ipv4_interface() -> str | None:
    try:
        with open("/proc/net/route", encoding="utf-8") as route_file:
            next(route_file, None)
            for line in route_file:
                fields = line.split()
                if len(fields) < 4 or fields[1] != "00000000":
                    continue
                interface = fields[0].strip()
                if interface:
                    return interface
    except OSError:
        return None
    return None


def _read_interface_ipv4_network(interface: str) -> IPv4Network | None:
    if not interface:
        return None

    name = interface.encode("utf-8")
    if len(name) >= 16:
        name = name[:15]
    ifreq = struct.pack("256s", name)

    try:
        import fcntl

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            address_bytes = fcntl.ioctl(sock.fileno(), 0x8915, ifreq)[20:24]
            netmask_bytes = fcntl.ioctl(sock.fileno(), 0x891b, ifreq)[20:24]
    except (ImportError, OSError):
        return None

    address = socket.inet_ntoa(address_bytes)
    netmask = socket.inet_ntoa(netmask_bytes)
    try:
        return ip_network(f"{address}/{netmask}", strict=False)
    except ValueError:
        return None


@lru_cache(maxsize=1)
def _auto_detect_host_gateway_networks() -> tuple[IPv4Network | IPv6Network, ...]:
    networks: list[IPv4Network | IPv6Network] = []

    default_gateway = _read_default_ipv4_gateway()
    if default_gateway is not None:
        network = _single_host_network(default_gateway)
        if network is not None:
            networks.append(network)

    default_interface = _read_default_ipv4_interface()
    if default_interface is not None:
        interface_network = _read_interface_ipv4_network(default_interface)
        if interface_network is not None and interface_network not in networks:
            networks.append(interface_network)

    for hostname in ("host.containers.internal", "host.docker.internal"):
        try:
            infos = socket.getaddrinfo(hostname, None)
        except OSError:
            continue
        for info in infos:
            candidate = info[4][0]
            if not isinstance(candidate, str):
                continue
            network = _single_host_network(candidate)
            if network is not None and network not in networks:
                networks.append(network)

    return tuple(networks)


def _insecure_allow_remote_no_auth_host_networks() -> tuple[IPv4Network | IPv6Network, ...]:
    settings = get_settings()
    configured = settings.insecure_allow_remote_no_auth_host_cidrs
    if configured:
        return parse_trusted_proxy_networks(configured)
    return _auto_detect_host_gateway_networks()


def resolve_request_client_host(request: HTTPConnection) -> str | None:
    settings = get_settings()
    socket_ip = _request_socket_host(request)
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


def _parse_host_header_hostname(host_header: str | None) -> str | None:
    if host_header is None:
        return None
    value = host_header.strip()
    if not value:
        return None
    if value.startswith("["):
        closing = value.find("]")
        if closing != -1:
            return value[: closing + 1]
        return value
    if value.count(":") == 1:
        return value.split(":", 1)[0].strip()
    return value


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
    if address.is_loopback:
        host_name = _parse_host_header_hostname(request.headers.get("host"))
        if settings.firewall_trust_proxy_headers:
            return is_local_host(host_name) and _has_forwarded_client_ip_hint(request.headers)
        return is_local_host(host_name) and not _has_forwarded_client_ip_hint(request.headers)
    return address.is_loopback


def is_host_os_request(request: HTTPConnection) -> bool:
    if is_local_request(request):
        return True

    socket_host = _request_socket_host(request)
    if not socket_host:
        return False
    try:
        address = ip_address(socket_host)
    except ValueError:
        return False

    host_name = _parse_host_header_hostname(request.headers.get("host"))
    host_networks = _insecure_allow_remote_no_auth_host_networks()
    if is_local_host(host_name) and _has_forwarded_client_ip_hint(request.headers):
        return False

    return any(address in network for network in host_networks)

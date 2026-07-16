from __future__ import annotations

from collections.abc import Mapping
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network

from starlette.datastructures import Headers
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


def _combined_chain_header(headers: Mapping[str, str], header_name: str) -> str | None:
    if isinstance(headers, Headers):
        values = headers.getlist(header_name)
        return ",".join(values) if values else None
    return headers.get(header_name)


def resolve_connection_client_ip(
    headers: Mapping[str, str],
    socket_ip: str | None,
    *,
    trust_proxy_headers: bool,
    trusted_proxy_networks: tuple[IPv4Network | IPv6Network, ...] = (),
) -> str | None:
    if trust_proxy_headers and socket_ip and is_trusted_proxy_source(socket_ip, trusted_proxy_networks):
        forwarded_for = _combined_chain_header(headers, "x-forwarded-for")
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

        forwarded = _combined_chain_header(headers, "forwarded")
        if forwarded:
            try:
                return _resolve_client_ip_from_forwarded_chain(
                    socket_ip,
                    forwarded,
                    trusted_proxy_networks,
                )
            except ValueError:
                return None

        return None
    return socket_ip


def parse_trusted_proxy_networks(cidrs: list[str]) -> tuple[IPv4Network | IPv6Network, ...]:
    return tuple(ip_network(cidr, strict=False) for cidr in cidrs)


def _resolve_client_ip_from_xff_chain(
    socket_ip: str,
    forwarded_for: str,
    trusted_proxy_networks: tuple[IPv4Network | IPv6Network, ...],
) -> str:
    hops = [entry.strip() for entry in forwarded_for.split(",")]
    if any(not _is_valid_ip(entry) for entry in hops):
        raise ValueError("Invalid X-Forwarded-For chain")
    return _resolve_client_ip_from_hops(socket_ip, hops, trusted_proxy_networks)


def _resolve_client_ip_from_hops(
    socket_ip: str,
    hops: list[str],
    trusted_proxy_networks: tuple[IPv4Network | IPv6Network, ...],
) -> str:
    resolved = socket_ip
    for previous_hop in reversed(hops):
        if not is_trusted_proxy_source(resolved, trusted_proxy_networks):
            break
        resolved = previous_hop
    return resolved


def is_trusted_proxy_source(
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


def _split_forwarded_value(value: str, delimiter: str) -> list[str]:
    parts: list[str] = []
    start = 0
    in_quotes = False
    escaped = False

    for index, character in enumerate(value):
        if escaped:
            escaped = False
            continue
        if in_quotes and character == "\\":
            escaped = True
            continue
        if character == '"':
            in_quotes = not in_quotes
            continue
        if character == delimiter and not in_quotes:
            part = value[start:index].strip()
            if not part:
                raise ValueError("Empty Forwarded header part")
            parts.append(part)
            start = index + 1

    if in_quotes or escaped:
        raise ValueError("Malformed quoted Forwarded header value")

    part = value[start:].strip()
    if not part:
        raise ValueError("Empty Forwarded header part")
    parts.append(part)
    return parts


def _unquote_forwarded_value(raw_value: str) -> str:
    value = raw_value.strip()
    if not value:
        raise ValueError("Empty Forwarded header value")
    if '"' not in value:
        return value
    if len(value) < 2 or not (value.startswith('"') and value.endswith('"')):
        raise ValueError("Malformed quoted Forwarded header value")

    unescaped: list[str] = []
    escaped = False
    for character in value[1:-1]:
        if escaped:
            unescaped.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == '"':
            raise ValueError("Malformed quoted Forwarded header value")
        else:
            unescaped.append(character)
    if escaped:
        raise ValueError("Malformed quoted Forwarded header value")
    return "".join(unescaped)


def _validate_forwarded_port(port: str) -> None:
    if not port.isascii() or not port.isdigit() or not 0 <= int(port) <= 65535:
        raise ValueError("Invalid Forwarded node port")


def _parse_forwarded_node(raw_value: str) -> str:
    value = _unquote_forwarded_value(raw_value)
    if value.lower() == "unknown" or value.startswith("_"):
        raise ValueError("Unsupported Forwarded node identifier")

    candidate = value
    if value.startswith("["):
        closing_bracket = value.find("]")
        if closing_bracket == -1:
            raise ValueError("Invalid bracketed Forwarded node")
        candidate = value[1:closing_bracket]
        suffix = value[closing_bracket + 1 :]
        if suffix:
            if not suffix.startswith(":"):
                raise ValueError("Invalid bracketed Forwarded node")
            _validate_forwarded_port(suffix[1:])
    else:
        try:
            return str(ip_address(value))
        except ValueError:
            candidate, separator, port = value.rpartition(":")
            if not separator or ":" in candidate:
                raise ValueError("Invalid Forwarded node") from None
            _validate_forwarded_port(port)

    try:
        return str(ip_address(candidate))
    except ValueError:
        raise ValueError("Invalid Forwarded node") from None


def _parse_forwarded_for_chain(forwarded: str) -> list[str]:
    hops: list[str] = []
    for element in _split_forwarded_value(forwarded, ","):
        forwarded_for: str | None = None
        for parameter in _split_forwarded_value(element, ";"):
            name, separator, raw_value = parameter.partition("=")
            if not separator or not name.strip() or not raw_value.strip():
                raise ValueError("Malformed Forwarded parameter")
            if name.strip().lower() != "for":
                continue
            if forwarded_for is not None:
                raise ValueError("Duplicate Forwarded for parameter")
            forwarded_for = _parse_forwarded_node(raw_value)
        if forwarded_for is None:
            raise ValueError("Missing Forwarded for parameter")
        hops.append(forwarded_for)
    return hops


def _resolve_client_ip_from_forwarded_chain(
    socket_ip: str,
    forwarded: str,
    trusted_proxy_networks: tuple[IPv4Network | IPv6Network, ...],
) -> str:
    return _resolve_client_ip_from_hops(
        socket_ip,
        _parse_forwarded_for_chain(forwarded),
        trusted_proxy_networks,
    )


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

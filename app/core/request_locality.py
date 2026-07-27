from __future__ import annotations

from collections.abc import Mapping
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network

from starlette.datastructures import Headers
from starlette.requests import HTTPConnection

from app.core.config.settings import get_settings
from app.core.socket_peer import raw_socket_peer_host

_LOCAL_HOSTS = {
    "",
    "localhost",
    "127.0.0.1",
    "::1",
    "[::1]",
}

_TEST_SERVER_HOSTS = {"testserver", "testclient"}
FORWARDED_CHAIN_HEADER_NAMES = frozenset({"x-forwarded-for", "forwarded"})
_SINGLETON_CLIENT_IP_HEADER_NAMES = ("x-real-ip", "true-client-ip", "cf-connecting-ip")
_FORWARDED_CLIENT_IP_HEADERS = FORWARDED_CHAIN_HEADER_NAMES | frozenset(_SINGLETON_CLIENT_IP_HEADER_NAMES)
_HTTP_TOKEN_CHARACTERS = frozenset("!#$%&'*+-.^_`|~0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")


def is_local_host(host: str | None) -> bool:
    if host is None:
        return False
    return host.strip().lower() in _LOCAL_HOSTS


def _combined_chain_header(headers: Mapping[str, str], header_name: str) -> str | None:
    if isinstance(headers, Headers):
        values = headers.getlist(header_name)
        return ",".join(values) if values else None
    return headers.get(header_name)


def _has_non_empty_header_value(
    headers: Mapping[str, str],
    header_name: str,
) -> bool:
    if isinstance(headers, Headers):
        return any(value.strip() for value in headers.getlist(header_name))
    value = headers.get(header_name)
    return bool(value and value.strip())


def _resolve_proxy_header_consensus(
    headers: Mapping[str, str],
    socket_ip: str,
    trusted_proxy_networks: tuple[IPv4Network | IPv6Network, ...],
    allowed_header_names: frozenset[str],
) -> str | None:
    if isinstance(headers, Headers):
        for header_name in _SINGLETON_CLIENT_IP_HEADER_NAMES:
            if (
                header_name in allowed_header_names
                and len(headers.getlist(header_name)) > 1
                and _has_non_empty_header_value(headers, header_name)
            ):
                return None

    resolved_client_ips: list[str] = []
    if "x-forwarded-for" in allowed_header_names and _has_non_empty_header_value(headers, "x-forwarded-for"):
        forwarded_for = _combined_chain_header(headers, "x-forwarded-for")
        assert forwarded_for is not None
        try:
            resolved_client_ips.append(
                _resolve_client_ip_from_xff_chain(
                    socket_ip,
                    forwarded_for,
                    trusted_proxy_networks,
                )
            )
        except ValueError:
            return None

    for header_name in _SINGLETON_CLIENT_IP_HEADER_NAMES:
        if header_name not in allowed_header_names or not _has_non_empty_header_value(headers, header_name):
            continue
        forwarded_ip = headers.get(header_name)
        assert forwarded_ip is not None
        candidate = forwarded_ip.strip()
        if not _is_valid_ip(candidate):
            return None
        resolved_client_ips.append(candidate)

    if "forwarded" in allowed_header_names and _has_non_empty_header_value(headers, "forwarded"):
        forwarded = _combined_chain_header(headers, "forwarded")
        assert forwarded is not None
        try:
            resolved_client_ips.append(
                _resolve_client_ip_from_forwarded_chain(
                    socket_ip,
                    forwarded,
                    trusted_proxy_networks,
                )
            )
        except ValueError:
            return None

    if not resolved_client_ips:
        return None
    first_resolution = resolved_client_ips[0]
    first_address = ip_address(first_resolution)
    if any(ip_address(candidate) != first_address for candidate in resolved_client_ips[1:]):
        return None
    return first_resolution


def resolve_connection_client_ip(
    headers: Mapping[str, str],
    socket_ip: str | None,
    *,
    trust_proxy_headers: bool,
    trusted_proxy_networks: tuple[IPv4Network | IPv6Network, ...] = (),
    allowed_proxy_header_names: frozenset[str] | None = None,
    require_proxy_header_consensus: bool = False,
) -> str | None:
    if trust_proxy_headers and socket_ip and is_trusted_proxy_source(socket_ip, trusted_proxy_networks):
        allowed_header_names = (
            _FORWARDED_CLIENT_IP_HEADERS if allowed_proxy_header_names is None else allowed_proxy_header_names
        )
        if require_proxy_header_consensus:
            return _resolve_proxy_header_consensus(
                headers,
                socket_ip,
                trusted_proxy_networks,
                allowed_header_names,
            )

        if isinstance(headers, Headers):
            for header_name in _SINGLETON_CLIENT_IP_HEADER_NAMES:
                if header_name in allowed_header_names and len(headers.getlist(header_name)) > 1:
                    return None

        forwarded_for = (
            _combined_chain_header(headers, "x-forwarded-for") if "x-forwarded-for" in allowed_header_names else None
        )
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

        for header_name in _SINGLETON_CLIENT_IP_HEADER_NAMES:
            if header_name not in allowed_header_names:
                continue
            forwarded_ip = headers.get(header_name)
            if forwarded_ip:
                candidate = forwarded_ip.strip()
                return candidate if _is_valid_ip(candidate) else None

        forwarded = _combined_chain_header(headers, "forwarded") if "forwarded" in allowed_header_names else None
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


def _is_http_token(value: str) -> bool:
    return bool(value) and all(character in _HTTP_TOKEN_CHARACTERS for character in value)


def _is_forwarded_quoted_character(character: str, *, escaped: bool) -> bool:
    code_point = ord(character)
    if escaped:
        return code_point == 0x09 or code_point == 0x20 or 0x21 <= code_point <= 0x7E or 0x80 <= code_point <= 0xFF
    return (
        code_point == 0x09
        or code_point in (0x20, 0x21)
        or 0x23 <= code_point <= 0x5B
        or 0x5D <= code_point <= 0x7E
        or 0x80 <= code_point <= 0xFF
    )


def _unquote_forwarded_value(raw_value: str) -> str:
    value = raw_value.strip()
    if len(value) < 2 or not (value.startswith('"') and value.endswith('"')):
        raise ValueError("Malformed quoted Forwarded header value")

    unescaped: list[str] = []
    escaped = False
    for character in value[1:-1]:
        if escaped:
            if not _is_forwarded_quoted_character(character, escaped=True):
                raise ValueError("Malformed quoted Forwarded header value")
            unescaped.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == '"' or not _is_forwarded_quoted_character(character, escaped=False):
            raise ValueError("Malformed quoted Forwarded header value")
        else:
            unescaped.append(character)
    if escaped:
        raise ValueError("Malformed quoted Forwarded header value")
    return "".join(unescaped)


def _validate_forwarded_port(port: str) -> None:
    if not 1 <= len(port) <= 5 or not port.isascii() or not port.isdigit():
        raise ValueError("Invalid Forwarded node port")
    if not 0 <= int(port) <= 65535:
        raise ValueError("Invalid Forwarded node port")


def _parse_forwarded_node(value: str, *, is_quoted: bool) -> str:
    if value.lower() == "unknown" or value.startswith("_"):
        raise ValueError("Unsupported Forwarded node identifier")

    candidate = value
    expects_ipv6 = value.startswith("[")
    if expects_ipv6:
        if not is_quoted:
            raise ValueError("Bracketed Forwarded nodes must be quoted")
        closing_bracket = value.find("]")
        if closing_bracket == -1:
            raise ValueError("Invalid bracketed Forwarded node")
        candidate = value[1:closing_bracket]
        if "%" in candidate:
            raise ValueError("Scoped IPv6 Forwarded nodes are unsupported")
        suffix = value[closing_bracket + 1 :]
        if suffix:
            if not suffix.startswith(":"):
                raise ValueError("Invalid bracketed Forwarded node")
            _validate_forwarded_port(suffix[1:])
    else:
        try:
            parsed_ip = ip_address(value)
        except ValueError:
            if not is_quoted:
                raise ValueError("Forwarded nodes with ports must be quoted") from None
            candidate, separator, port = value.rpartition(":")
            if not separator or ":" in candidate:
                raise ValueError("Invalid Forwarded node") from None
            _validate_forwarded_port(port)
        else:
            if parsed_ip.version != 4:
                raise ValueError("IPv6 Forwarded nodes must be bracketed")
            return str(parsed_ip)

    try:
        parsed_ip = ip_address(candidate)
    except ValueError:
        raise ValueError("Invalid Forwarded node") from None
    if (parsed_ip.version == 6) != expects_ipv6:
        raise ValueError("Invalid Forwarded node address family")
    return str(parsed_ip)


def _parse_forwarded_pair(parameter: str) -> tuple[str, str, bool]:
    name, separator, raw_value = parameter.partition("=")
    if not separator or name != name.strip() or raw_value != raw_value.strip():
        raise ValueError("Malformed Forwarded parameter")
    normalized_name = name.lower()
    value = raw_value
    if not _is_http_token(normalized_name) or not value:
        raise ValueError("Malformed Forwarded parameter")
    if value.startswith('"'):
        return normalized_name, _unquote_forwarded_value(value), True
    if not _is_http_token(value):
        raise ValueError("Malformed Forwarded parameter value")
    return normalized_name, value, False


def _parse_forwarded_for_chain(forwarded: str) -> list[str]:
    hops: list[str] = []
    for element in _split_forwarded_value(forwarded, ","):
        forwarded_for: str | None = None
        seen_parameter_names: set[str] = set()
        for parameter in _split_forwarded_value(element, ";"):
            name, value, is_quoted = _parse_forwarded_pair(parameter)
            if name in seen_parameter_names:
                raise ValueError("Duplicate Forwarded parameter")
            seen_parameter_names.add(name)
            if name == "for":
                forwarded_for = _parse_forwarded_node(value, is_quoted=is_quoted)
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
    if isinstance(headers, Headers):
        return any(value for header_name in _FORWARDED_CLIENT_IP_HEADERS for value in headers.getlist(header_name))
    return any(headers.get(header_name) for header_name in _FORWARDED_CLIENT_IP_HEADERS)


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
    trusted_proxy_networks = parse_trusted_proxy_networks(settings.firewall_trusted_proxy_cidrs)
    socket_ip = raw_socket_peer_host(request)
    client_host = resolve_connection_client_ip(
        request.headers,
        socket_ip,
        trust_proxy_headers=settings.firewall_trust_proxy_headers,
        trusted_proxy_networks=trusted_proxy_networks,
        require_proxy_header_consensus=True,
    )
    if not client_host:
        return False
    try:
        address = ip_address(client_host)
    except ValueError:
        return False
    if address.is_loopback:
        host_name = _parse_host_header_hostname(request.headers.get("host"))
        if settings.firewall_trust_proxy_headers:
            return (
                is_local_host(host_name)
                and socket_ip is not None
                and is_trusted_proxy_source(socket_ip, trusted_proxy_networks)
            )
        return is_local_host(host_name) and not _has_forwarded_client_ip_hint(request.headers)
    return address.is_loopback

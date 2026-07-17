from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers
from starlette.requests import Request

import app.core.request_locality as request_locality
from app.core.request_locality import is_local_request


@pytest.fixture(autouse=True)
def _default_request_locality_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        request_locality,
        "get_settings",
        lambda: SimpleNamespace(firewall_trust_proxy_headers=False, firewall_trusted_proxy_cidrs=[]),
    )


def _request(*, client_host: str, host: str) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"host", host.encode("utf-8"))],
        "client": (client_host, 50000),
        "server": (host.split(":", 1)[0], 80),
        "scheme": "http",
        "query_string": b"",
    }
    return Request(scope)


def _resolve_forwarded(
    forwarded: str,
    *,
    socket_ip: str = "127.0.0.1",
    trusted_cidrs: list[str] | None = None,
) -> str | None:
    return request_locality.resolve_connection_client_ip(
        {"forwarded": forwarded},
        socket_ip,
        trust_proxy_headers=True,
        trusted_proxy_networks=request_locality.parse_trusted_proxy_networks(trusted_cidrs or ["127.0.0.1/32"]),
    )


def test_loopback_with_local_host_is_local() -> None:
    request = _request(client_host="127.0.0.1", host="localhost")
    assert is_local_request(request) is True


def test_loopback_with_non_local_host_is_not_local() -> None:
    request = _request(client_host="127.0.0.1", host="lb.example")
    assert is_local_request(request) is False


def test_loopback_with_bracketed_ipv6_local_host_is_local() -> None:
    request = _request(client_host="::1", host="[::1]:8000")
    assert is_local_request(request) is True


def test_loopback_with_unbracketed_ipv6_local_host_is_local() -> None:
    request = _request(client_host="::1", host="::1")
    assert is_local_request(request) is True


def test_trusted_proxy_mode_treats_loopback_without_forwarded_hint_as_remote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        request_locality,
        "get_settings",
        lambda: SimpleNamespace(firewall_trust_proxy_headers=True, firewall_trusted_proxy_cidrs=[]),
    )
    request = _request(client_host="127.0.0.1", host="localhost")
    assert is_local_request(request) is False


def test_trusted_proxy_mode_accepts_loopback_with_forwarded_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        request_locality,
        "get_settings",
        lambda: SimpleNamespace(firewall_trust_proxy_headers=True, firewall_trusted_proxy_cidrs=[]),
    )
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"host", b"localhost"), (b"x-forwarded-for", b"127.0.0.1")],
        "client": ("127.0.0.1", 50000),
        "server": ("localhost", 80),
        "scheme": "http",
        "query_string": b"",
    }
    request = Request(scope)
    assert is_local_request(request) is True


def test_forwarded_chain_stops_at_proxy_appended_remote_client() -> None:
    resolved = _resolve_forwarded("for=127.0.0.1, for=203.0.113.24")

    assert resolved == "203.0.113.24"


def test_forwarded_chain_traverses_only_complete_trusted_proxy_path() -> None:
    resolved = _resolve_forwarded(
        'for=198.51.100.7;proto=https, for=10.0.0.1;host="proxy,internal", for=10.0.0.2',
        socket_ip="10.0.0.3",
        trusted_cidrs=["10.0.0.0/8"],
    )

    assert resolved == "198.51.100.7"


@pytest.mark.parametrize(
    "forwarded",
    [
        "for=127.0.0.1, by=203.0.113.24",
        "for=127.0.0.1; for=203.0.113.24",
        "for=_hidden",
        "for=unknown",
        "for=not-an-ip",
        'for="127.0.0.1',
        "for=127.0.0.1,, for=203.0.113.24",
        'for="127.0.0.1;proto=https"',
        'for="[2001:db8::1]:65536"',
        "for=198.51.100.7:4711",
        "for=[2001:db8::1]",
        "for=[2001:db8::1]:4711",
        "for=2001:db8::1",
        'for="2001:db8::1"',
        'for="[198.51.100.7]"',
        'for="198.51.100.7:000080"',
        "for=203.0.113.24;proto=https;PROTO=http",
        "for=203.0.113.24;bad name=value",
        "for=203.0.113.24;proto=bad value",
        'for=203.0.113.24;proto=abc"def"',
        'for=203.0.113.24;proto="bad\x01value"',
        'for="[fe80::1%eth0]"',
        "for =127.0.0.1",
        "for= 127.0.0.1",
        "for=203.0.113.24; proto",
    ],
)
def test_forwarded_chain_fails_closed_on_malformed_element(forwarded: str) -> None:
    assert _resolve_forwarded(forwarded) is None


@pytest.mark.parametrize(
    ("forwarded", "expected"),
    [
        ('for="198.51.100.7:4711"', "198.51.100.7"),
        ('for="[2001:db8::1]:4711"', "2001:db8::1"),
        ('for="[2001:0db8::1]"', "2001:db8::1"),
    ],
)
def test_forwarded_chain_accepts_ip_nodes_with_optional_port(forwarded: str, expected: str) -> None:
    assert _resolve_forwarded(forwarded) == expected


def test_xff_chain_keeps_right_to_left_trusted_proxy_resolution() -> None:
    resolved = request_locality.resolve_connection_client_ip(
        {"x-forwarded-for": "198.51.100.7, 10.0.0.1, 10.0.0.2"},
        "10.0.0.3",
        trust_proxy_headers=True,
        trusted_proxy_networks=request_locality.parse_trusted_proxy_networks(["10.0.0.0/8"]),
    )

    assert resolved == "198.51.100.7"


def test_xff_chain_combines_duplicate_header_fields() -> None:
    headers = Headers(
        raw=[
            (b"x-forwarded-for", b"127.0.0.1"),
            (b"x-forwarded-for", b"203.0.113.24"),
        ]
    )

    resolved = request_locality.resolve_connection_client_ip(
        headers,
        "127.0.0.1",
        trust_proxy_headers=True,
        trusted_proxy_networks=request_locality.parse_trusted_proxy_networks(["127.0.0.1/32"]),
    )

    assert resolved == "203.0.113.24"


@pytest.mark.parametrize("header_name", ["x-real-ip", "true-client-ip", "cf-connecting-ip"])
def test_connection_resolver_preserves_default_singleton_proxy_headers(header_name: str) -> None:
    resolved = request_locality.resolve_connection_client_ip(
        {header_name: "203.0.113.24"},
        "127.0.0.1",
        trust_proxy_headers=True,
        trusted_proxy_networks=request_locality.parse_trusted_proxy_networks(["127.0.0.1/32"]),
    )

    assert resolved == "203.0.113.24"


@pytest.mark.parametrize("header_name", ["x-real-ip", "true-client-ip", "cf-connecting-ip"])
def test_connection_resolver_rejects_repeated_singleton_proxy_headers(header_name: str) -> None:
    encoded_name = header_name.encode()
    headers = Headers(
        raw=[
            (encoded_name, b"127.0.0.1"),
            (encoded_name, b"203.0.113.24"),
        ]
    )

    resolved = request_locality.resolve_connection_client_ip(
        headers,
        "127.0.0.1",
        trust_proxy_headers=True,
        trusted_proxy_networks=request_locality.parse_trusted_proxy_networks(["127.0.0.1/32"]),
    )

    assert resolved is None


def test_connection_resolver_rejects_repeated_singleton_before_valid_xff() -> None:
    headers = Headers(
        raw=[
            (b"x-forwarded-for", b"127.0.0.1"),
            (b"x-real-ip", b"127.0.0.1"),
            (b"x-real-ip", b"203.0.113.24"),
        ]
    )

    resolved = request_locality.resolve_connection_client_ip(
        headers,
        "127.0.0.1",
        trust_proxy_headers=True,
        trusted_proxy_networks=request_locality.parse_trusted_proxy_networks(["127.0.0.1/32"]),
    )

    assert resolved is None

from __future__ import annotations

import pytest
from starlette.requests import Request

from app.core.middleware.api_firewall import _parse_trusted_proxy_networks, _resolve_client_ip

pytestmark = pytest.mark.unit


def _make_request(
    *,
    headers: list[tuple[bytes, bytes]] | None = None,
    client: tuple[str, int] | None = None,
) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/backend-api/codex/models",
        "headers": headers or [],
        "client": client,
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "http_version": "1.1",
    }
    return Request(scope)


def test_resolve_client_ip_uses_socket_ip_when_proxy_headers_not_trusted():
    request = _make_request(
        headers=[(b"x-forwarded-for", b"198.51.100.10")],
        client=("127.0.0.1", 12345),
    )
    assert _resolve_client_ip(request, trust_proxy_headers=False) == "127.0.0.1"


def test_resolve_client_ip_uses_first_forwarded_ip_when_trusted():
    trusted = _parse_trusted_proxy_networks(["127.0.0.1/32"])
    request = _make_request(
        headers=[(b"x-forwarded-for", b"198.51.100.10")],
        client=("127.0.0.1", 12345),
    )
    assert _resolve_client_ip(request, trust_proxy_headers=True, trusted_proxy_networks=trusted) == "198.51.100.10"


def test_resolve_client_ip_uses_rightmost_untrusted_from_forwarded_chain():
    trusted = _parse_trusted_proxy_networks(["127.0.0.1/32"])
    request = _make_request(
        headers=[(b"x-forwarded-for", b"10.10.10.10, 198.51.100.10")],
        client=("127.0.0.1", 12345),
    )
    assert _resolve_client_ip(request, trust_proxy_headers=True, trusted_proxy_networks=trusted) == "198.51.100.10"


def test_resolve_client_ip_traverses_trusted_proxy_chain_right_to_left():
    trusted = _parse_trusted_proxy_networks(["10.0.0.0/8"])
    request = _make_request(
        headers=[(b"x-forwarded-for", b"198.51.100.10, 10.0.0.1")],
        client=("10.0.0.2", 12345),
    )
    assert _resolve_client_ip(request, trust_proxy_headers=True, trusted_proxy_networks=trusted) == "198.51.100.10"


def test_resolve_client_ip_ignores_forwarded_ip_from_untrusted_source():
    trusted = _parse_trusted_proxy_networks(["127.0.0.1/32"])
    request = _make_request(
        headers=[(b"x-forwarded-for", b"198.51.100.10, 203.0.113.20")],
        client=("198.51.100.25", 12345),
    )
    assert _resolve_client_ip(request, trust_proxy_headers=True, trusted_proxy_networks=trusted) == "198.51.100.25"


def test_resolve_client_ip_falls_back_to_socket_ip_when_forwarded_missing():
    trusted = _parse_trusted_proxy_networks(["127.0.0.1/32"])
    request = _make_request(client=("127.0.0.1", 12345))
    assert _resolve_client_ip(request, trust_proxy_headers=True, trusted_proxy_networks=trusted) == "127.0.0.1"


def test_resolve_client_ip_falls_back_to_socket_ip_on_invalid_forwarded_chain():
    trusted = _parse_trusted_proxy_networks(["127.0.0.1/32"])
    request = _make_request(
        headers=[(b"x-forwarded-for", b"198.51.100.10, not-an-ip")],
        client=("127.0.0.1", 12345),
    )
    assert _resolve_client_ip(request, trust_proxy_headers=True, trusted_proxy_networks=trusted) == "127.0.0.1"


def test_resolve_client_ip_returns_none_when_client_missing():
    request = _make_request(client=None)
    assert _resolve_client_ip(request, trust_proxy_headers=True) is None

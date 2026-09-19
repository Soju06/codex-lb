from __future__ import annotations

from ipaddress import ip_network
from types import SimpleNamespace

import pytest
from starlette.requests import Request

import app.core.request_locality as request_locality
from app.core.request_locality import is_local_request, resolve_connection_client_ip


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


# ---------------------------------------------------------------------------
# resolve_connection_client_ip — trusted proxy peer with and without forwarding
# ---------------------------------------------------------------------------

_LOOPBACK_TRUSTED = (ip_network("127.0.0.1/32"),)


def test_trusted_proxy_peer_without_any_forwarding_header_resolves_to_the_socket_ip() -> None:
    """Local tooling dials loopback directly and sends no forwarding header.

    A trusted proxy always sets one, so a bare request cannot have come through it and
    the socket peer is the real client. Resolving to None here locked local tooling out
    of the dashboard and the keyless proxy path.
    """
    assert (
        resolve_connection_client_ip(
            {},
            "127.0.0.1",
            trust_proxy_headers=True,
            trusted_proxy_networks=_LOOPBACK_TRUSTED,
        )
        == "127.0.0.1"
    )


def test_trusted_proxy_peer_with_forwarded_for_resolves_to_the_forwarded_ip() -> None:
    assert (
        resolve_connection_client_ip(
            {"x-forwarded-for": "100.64.1.5"},
            "127.0.0.1",
            trust_proxy_headers=True,
            trusted_proxy_networks=_LOOPBACK_TRUSTED,
        )
        == "100.64.1.5"
    )


def test_untrusted_peer_with_forwarded_for_keeps_the_socket_ip() -> None:
    assert (
        resolve_connection_client_ip(
            {"x-forwarded-for": "127.0.0.1"},
            "198.51.100.42",
            trust_proxy_headers=True,
            trusted_proxy_networks=_LOOPBACK_TRUSTED,
        )
        == "198.51.100.42"
    )


def test_trusted_proxy_peer_with_malformed_forwarded_for_still_denies() -> None:
    assert (
        resolve_connection_client_ip(
            {"x-forwarded-for": "not-an-ip"},
            "127.0.0.1",
            trust_proxy_headers=True,
            trusted_proxy_networks=_LOOPBACK_TRUSTED,
        )
        is None
    )


def test_trusted_proxy_peer_with_unparseable_forwarded_header_still_denies() -> None:
    assert (
        resolve_connection_client_ip(
            {"forwarded": "for=_hidden"},
            "127.0.0.1",
            trust_proxy_headers=True,
            trusted_proxy_networks=_LOOPBACK_TRUSTED,
        )
        is None
    )


def test_trusted_proxy_peer_with_invalid_real_ip_still_denies() -> None:
    assert (
        resolve_connection_client_ip(
            {"x-real-ip": "nope"},
            "127.0.0.1",
            trust_proxy_headers=True,
            trusted_proxy_networks=_LOOPBACK_TRUSTED,
        )
        is None
    )

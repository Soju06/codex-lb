from __future__ import annotations

from ipaddress import ip_network
from types import SimpleNamespace

import pytest
from starlette.requests import Request

import app.core.request_locality as request_locality
from app.core.request_locality import is_host_os_request, is_local_request


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


def test_host_os_request_accepts_explicit_host_gateway_cidr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        request_locality,
        "get_settings",
        lambda: SimpleNamespace(
            firewall_trust_proxy_headers=False,
            firewall_trusted_proxy_cidrs=[],
            insecure_allow_remote_no_auth_host_cidrs=["10.88.0.1/32"],
        ),
    )
    request = _request(client_host="10.88.0.1", host="lb.example")
    assert is_host_os_request(request) is True


def test_host_os_request_rejects_other_private_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        request_locality,
        "get_settings",
        lambda: SimpleNamespace(
            firewall_trust_proxy_headers=False,
            firewall_trusted_proxy_cidrs=[],
            insecure_allow_remote_no_auth_host_cidrs=["10.88.0.1/32"],
        ),
    )
    request = _request(client_host="10.88.0.2", host="lb.example")
    assert is_host_os_request(request) is False


def test_host_os_request_accepts_localhost_host_header_with_host_network_proof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        request_locality,
        "_insecure_allow_remote_no_auth_host_networks",
        lambda: (ip_network("10.88.0.0/24"),),
    )
    request = _request(client_host="10.88.0.176", host="localhost:2455")
    assert is_host_os_request(request) is True


def test_host_os_request_rejects_localhost_host_header_without_host_network_proof() -> None:
    request = _request(client_host="203.0.113.44", host="localhost:2455")
    assert is_host_os_request(request) is False

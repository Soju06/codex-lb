from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.requests import Request

import app.core.request_locality as request_locality
from app.core.request_locality import is_local_request


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

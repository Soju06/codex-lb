from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.requests import Request

import app.core.auth.dashboard_session_ttl as dashboard_session_ttl
import app.core.request_locality as request_locality
from app.core.auth.dashboard_mode import DashboardAuthMode
from app.core.auth.dashboard_session_ttl import (
    DEFAULT_DASHBOARD_SESSION_TTL_SECONDS,
    REMOTE_DASHBOARD_SESSION_TTL_SECONDS,
    resolve_dashboard_session_ttl_seconds,
)

pytestmark = pytest.mark.unit


def _settings(
    *,
    trust_proxy_headers: bool = False,
    trust_loopback_host_header: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        dashboard_auth_mode=DashboardAuthMode.STANDARD,
        firewall_trust_proxy_headers=trust_proxy_headers,
        firewall_trusted_proxy_cidrs=[],
        dashboard_trust_loopback_host_header_for_long_sessions=trust_loopback_host_header,
    )


def _request(
    *,
    client_host: str,
    host: str,
    headers: list[tuple[bytes, bytes]] | None = None,
) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/dashboard-auth/password/login",
            "raw_path": b"/api/dashboard-auth/password/login",
            "query_string": b"",
            "headers": [(b"host", host.encode()), *(headers or [])],
            "client": (client_host, 12345),
            "server": (host.split(":", 1)[0], 2455),
        }
    )


def _patch_settings(monkeypatch: pytest.MonkeyPatch, settings: SimpleNamespace) -> None:
    monkeypatch.setattr(dashboard_session_ttl, "_get_settings", lambda: settings)
    monkeypatch.setattr(request_locality, "get_settings", lambda: settings)


def test_long_dashboard_session_ttl_applies_to_direct_loopback_request(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, _settings())

    ttl_seconds = resolve_dashboard_session_ttl_seconds(
        _request(client_host="127.0.0.1", host="localhost:2455"),
        DEFAULT_DASHBOARD_SESSION_TTL_SECONDS,
    )

    assert ttl_seconds == DEFAULT_DASHBOARD_SESSION_TTL_SECONDS


def test_long_dashboard_session_ttl_clamps_for_remote_request(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, _settings())

    ttl_seconds = resolve_dashboard_session_ttl_seconds(
        _request(client_host="203.0.113.10", host="lb.example"),
        DEFAULT_DASHBOARD_SESSION_TTL_SECONDS,
    )

    assert ttl_seconds == REMOTE_DASHBOARD_SESSION_TTL_SECONDS


def test_long_dashboard_session_ttl_clamps_for_bridge_ip_without_explicit_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch, _settings())

    ttl_seconds = resolve_dashboard_session_ttl_seconds(
        _request(client_host="172.17.0.1", host="127.0.0.1:2455"),
        DEFAULT_DASHBOARD_SESSION_TTL_SECONDS,
    )

    assert ttl_seconds == REMOTE_DASHBOARD_SESSION_TTL_SECONDS


def test_long_dashboard_session_ttl_accepts_loopback_host_header_with_explicit_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch, _settings(trust_loopback_host_header=True))

    ttl_seconds = resolve_dashboard_session_ttl_seconds(
        _request(client_host="172.17.0.1", host="127.0.0.1:2455"),
        DEFAULT_DASHBOARD_SESSION_TTL_SECONDS,
    )

    assert ttl_seconds == DEFAULT_DASHBOARD_SESSION_TTL_SECONDS


def test_long_dashboard_session_ttl_accepts_only_empty_repeated_forwarded_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch, _settings(trust_loopback_host_header=True))

    ttl_seconds = resolve_dashboard_session_ttl_seconds(
        _request(
            client_host="172.17.0.1",
            host="127.0.0.1:2455",
            headers=[(b"x-forwarded-for", b""), (b"x-forwarded-for", b"")],
        ),
        DEFAULT_DASHBOARD_SESSION_TTL_SECONDS,
    )

    assert ttl_seconds == DEFAULT_DASHBOARD_SESSION_TTL_SECONDS


def test_long_dashboard_session_ttl_clamps_for_later_duplicate_forwarded_identity_from_loopback_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch, _settings(trust_loopback_host_header=True))

    ttl_seconds = resolve_dashboard_session_ttl_seconds(
        _request(
            client_host="127.0.0.1",
            host="127.0.0.1:2455",
            headers=[(b"x-forwarded-for", b""), (b"x-forwarded-for", b"203.0.113.24")],
        ),
        DEFAULT_DASHBOARD_SESSION_TTL_SECONDS,
    )

    assert ttl_seconds == REMOTE_DASHBOARD_SESSION_TTL_SECONDS


def test_long_dashboard_session_ttl_clamps_when_proxy_headers_are_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, _settings(trust_proxy_headers=True))

    ttl_seconds = resolve_dashboard_session_ttl_seconds(
        _request(
            client_host="127.0.0.1",
            host="localhost:2455",
            headers=[(b"x-forwarded-for", b"127.0.0.1")],
        ),
        DEFAULT_DASHBOARD_SESSION_TTL_SECONDS,
    )

    assert ttl_seconds == REMOTE_DASHBOARD_SESSION_TTL_SECONDS


def test_shorter_configured_dashboard_session_ttl_is_preserved_for_remote_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_settings(monkeypatch, _settings())

    ttl_seconds = resolve_dashboard_session_ttl_seconds(
        _request(client_host="203.0.113.10", host="lb.example"),
        7200,
    )

    assert ttl_seconds == 7200

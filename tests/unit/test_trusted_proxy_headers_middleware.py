from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pytest
from starlette.requests import HTTPConnection
from starlette.types import Message, Receive, Scope, Send

import app.main as main
from app.core.middleware.trusted_proxy_headers import TrustedProxyHeadersMiddleware
from app.core.socket_peer import raw_socket_peer_host

pytestmark = pytest.mark.unit

_ScopeType = Literal["http", "websocket"]
_ClientAddress = tuple[str, int]


@dataclass(frozen=True)
class _ObservedConnection:
    scope: Scope
    client: _ClientAddress | None
    scheme: str
    raw_socket_peer_host: str | None


class _RecordingApp:
    def __init__(self) -> None:
        self.connections: list[_ObservedConnection] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        connection = HTTPConnection(scope)
        self.connections.append(
            _ObservedConnection(
                scope=scope,
                client=connection.client,
                scheme=scope["scheme"],
                raw_socket_peer_host=raw_socket_peer_host(connection),
            )
        )


@pytest.mark.asyncio
async def test_http_projects_forwarded_client_and_scheme_while_preserving_raw_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    downstream = _RecordingApp()
    middleware = TrustedProxyHeadersMiddleware(downstream)
    scope = _connection_scope(
        "http",
        client=("127.0.0.1", 43120),
        headers=[
            (b"x-forwarded-for", b"203.0.113.41"),
            (b"x-forwarded-proto", b"https"),
        ],
    )

    await middleware(scope, _receive, _send)

    observed = downstream.connections[0]
    assert observed.scope is scope
    assert observed.client == ("203.0.113.41", 0)
    assert observed.scheme == "https"
    assert observed.raw_socket_peer_host == "127.0.0.1"


@pytest.mark.asyncio
async def test_websocket_projects_forwarded_client_and_wss_while_preserving_raw_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    downstream = _RecordingApp()
    middleware = TrustedProxyHeadersMiddleware(downstream)
    scope = _connection_scope(
        "websocket",
        client=("127.0.0.1", 43121),
        headers=[
            (b"x-forwarded-for", b"198.51.100.12"),
            (b"x-forwarded-proto", b"https"),
        ],
    )

    await middleware(scope, _receive, _send)

    observed = downstream.connections[0]
    assert observed.scope is scope
    assert observed.client == ("198.51.100.12", 0)
    assert observed.scheme == "wss"
    assert observed.raw_socket_peer_host == "127.0.0.1"


@pytest.mark.asyncio
async def test_untrusted_peer_ignores_forwarded_headers_but_is_still_captured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    downstream = _RecordingApp()
    middleware = TrustedProxyHeadersMiddleware(downstream)
    scope = _connection_scope(
        "http",
        client=("192.0.2.15", 43122),
        headers=[
            (b"x-forwarded-for", b"203.0.113.42"),
            (b"x-forwarded-proto", b"https"),
        ],
    )

    await middleware(scope, _receive, _send)

    observed = downstream.connections[0]
    assert observed.client == ("192.0.2.15", 43122)
    assert observed.scheme == "http"
    assert observed.raw_socket_peer_host == "192.0.2.15"


@pytest.mark.parametrize(
    ("trusted_hosts", "raw_peer", "expected_client"),
    [
        ("", "127.0.0.1", ("127.0.0.1", 43123)),
        ("127.0.0.1", "127.0.0.1", ("203.0.113.43", 0)),
        ("10.0.0.0/8", "10.12.0.4", ("203.0.113.43", 0)),
        ("192.0.2.1, 127.0.0.1", "127.0.0.1", ("203.0.113.43", 0)),
        ("*", "192.0.2.16", ("203.0.113.43", 0)),
    ],
)
@pytest.mark.asyncio
async def test_forwarded_allow_ips_preserves_uvicorn_trust_semantics(
    monkeypatch: pytest.MonkeyPatch,
    trusted_hosts: str,
    raw_peer: str,
    expected_client: _ClientAddress,
) -> None:
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", trusted_hosts)
    downstream = _RecordingApp()
    middleware = TrustedProxyHeadersMiddleware(downstream)
    scope = _connection_scope(
        "http",
        client=(raw_peer, 43123),
        headers=[(b"x-forwarded-for", b"203.0.113.43")],
    )

    await middleware(scope, _receive, _send)

    observed = downstream.connections[0]
    assert observed.client == expected_client
    assert observed.raw_socket_peer_host == raw_peer


@pytest.mark.asyncio
async def test_captured_missing_client_remains_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    downstream = _RecordingApp()
    middleware = TrustedProxyHeadersMiddleware(downstream)
    scope = _connection_scope(
        "http",
        client=None,
        headers=[(b"x-forwarded-for", b"203.0.113.44")],
    )

    await middleware(scope, _receive, _send)

    observed = downstream.connections[0]
    assert observed.client == ("203.0.113.44", 0)
    assert observed.raw_socket_peer_host is None


def test_raw_socket_peer_host_fails_closed_without_capture() -> None:
    connection = HTTPConnection(
        _connection_scope(
            "http",
            client=("203.0.113.45", 0),
            headers=[],
        )
    )

    assert raw_socket_peer_host(connection) is None


def test_create_app_registers_trusted_proxy_headers_as_outermost_user_middleware() -> None:
    assert main.app.user_middleware[0].cls is TrustedProxyHeadersMiddleware


def _connection_scope(
    scope_type: _ScopeType,
    *,
    client: _ClientAddress | None,
    headers: list[tuple[bytes, bytes]],
) -> Scope:
    return {
        "type": scope_type,
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "scheme": "ws" if scope_type == "websocket" else "http",
        "method": "GET",
        "path": "/backend-api/codex",
        "raw_path": b"/backend-api/codex",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": client,
        "server": ("testserver", 80),
        "state": {},
    }


async def _receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


async def _send(_: Message) -> None:
    return None

from __future__ import annotations

from typing import Literal

import pytest
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

import app.main as main
from app.core.middleware.trusted_proxy_headers import TrustedProxyHeadersMiddleware
from app.core.socket_peer import raw_socket_peer_host

pytestmark = pytest.mark.unit

_ScopeType = Literal["http", "websocket"]
_Client = tuple[str, int]


class _RecordingApp:
    observed: tuple[_Client | None, str, str | None] | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        connection = HTTPConnection(scope)
        self.observed = (connection.client, scope["scheme"], raw_socket_peer_host(connection))


@pytest.mark.parametrize(
    ("scope_type", "expected_scheme"),
    [("http", "https"), ("websocket", "wss")],
)
@pytest.mark.asyncio
async def test_projection_preserves_raw_peer_for_http_and_websocket(
    monkeypatch: pytest.MonkeyPatch,
    scope_type: _ScopeType,
    expected_scheme: str,
) -> None:
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    observed = await _run(
        scope_type,
        client=("127.0.0.1", 43120),
        headers=[
            (b"x-forwarded-for", b"203.0.113.41"),
            (b"x-forwarded-proto", b"https"),
        ],
    )

    assert observed == (("203.0.113.41", 0), expected_scheme, "127.0.0.1")


@pytest.mark.parametrize(
    ("trusted_hosts", "raw_peer", "expected_client"),
    [
        (None, "127.0.0.1", ("203.0.113.43", 0)),
        (None, "192.0.2.15", ("192.0.2.15", 43123)),
        ("", "127.0.0.1", ("127.0.0.1", 43123)),
        ("10.0.0.0/8", "10.12.0.4", ("203.0.113.43", 0)),
        ("192.0.2.1, 127.0.0.1", "127.0.0.1", ("203.0.113.43", 0)),
        ("*", "192.0.2.16", ("203.0.113.43", 0)),
    ],
)
@pytest.mark.asyncio
async def test_forwarded_allow_ips_keeps_uvicorn_trust_semantics(
    monkeypatch: pytest.MonkeyPatch,
    trusted_hosts: str | None,
    raw_peer: str,
    expected_client: _Client,
) -> None:
    if trusted_hosts is None:
        monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)
    else:
        monkeypatch.setenv("FORWARDED_ALLOW_IPS", trusted_hosts)

    observed = await _run(
        "http",
        client=(raw_peer, 43123),
        headers=[(b"x-forwarded-for", b"203.0.113.43")],
    )

    assert observed == (expected_client, "http", raw_peer)


def test_raw_socket_peer_fails_closed_without_capture() -> None:
    connection = HTTPConnection(_scope("http", client=("203.0.113.45", 0), headers=[]))

    assert raw_socket_peer_host(connection) is None


def test_capture_and_projection_middleware_is_outermost() -> None:
    assert main.app.user_middleware[0].cls is TrustedProxyHeadersMiddleware


async def _run(
    scope_type: _ScopeType,
    *,
    client: _Client | None,
    headers: list[tuple[bytes, bytes]],
) -> tuple[_Client | None, str, str | None]:
    downstream = _RecordingApp()
    middleware: ASGIApp = TrustedProxyHeadersMiddleware(downstream)
    await middleware(_scope(scope_type, client=client, headers=headers), _receive, _send)
    assert downstream.observed is not None
    return downstream.observed


def _scope(
    scope_type: _ScopeType,
    *,
    client: _Client | None,
    headers: list[tuple[bytes, bytes]],
) -> Scope:
    return {
        "type": scope_type,
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "scheme": "ws" if scope_type == "websocket" else "http",
        "method": "GET",
        "path": "/v1/responses",
        "raw_path": b"/v1/responses",
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

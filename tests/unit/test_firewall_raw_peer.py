from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from fastapi import WebSocket
from fastapi.responses import JSONResponse
from httpx import ASGITransport, AsyncClient
from starlette.types import Message, Receive, Scope, Send

import app.core.middleware.api_firewall as api_firewall_module
import app.modules.proxy.api as proxy_api_module
from app.core.middleware.api_firewall import ApiFirewallMiddleware
from app.core.middleware.firewall_cache import FirewallIPCache
from app.core.middleware.trusted_proxy_headers import TrustedProxyHeadersMiddleware
from app.core.request_locality import parse_trusted_proxy_networks

pytestmark = pytest.mark.unit


@dataclass(frozen=True, slots=True)
class FirewallPeerCase:
    trust_proxy_headers: bool
    raw_host: str
    projected_host: str
    trusted_proxy_cidrs: tuple[str, ...]
    allowlist: frozenset[str]
    capture_raw_peer: bool
    expected_allowed: bool


CASES = (
    pytest.param(
        FirewallPeerCase(
            trust_proxy_headers=False,
            raw_host="198.51.100.10",
            projected_host="203.0.113.9",
            trusted_proxy_cidrs=(),
            allowlist=frozenset({"203.0.113.9"}),
            capture_raw_peer=True,
            expected_allowed=False,
        ),
        id="trust-off-raw-unlisted-projected-listed-denies",
    ),
    pytest.param(
        FirewallPeerCase(
            trust_proxy_headers=False,
            raw_host="198.51.100.10",
            projected_host="203.0.113.9",
            trusted_proxy_cidrs=(),
            allowlist=frozenset({"198.51.100.10"}),
            capture_raw_peer=True,
            expected_allowed=True,
        ),
        id="trust-off-raw-listed-projected-unlisted-allows",
    ),
    pytest.param(
        FirewallPeerCase(
            trust_proxy_headers=True,
            raw_host="198.51.100.10",
            projected_host="10.0.0.2",
            trusted_proxy_cidrs=("10.0.0.0/8",),
            allowlist=frozenset({"10.0.0.2"}),
            capture_raw_peer=True,
            expected_allowed=False,
        ),
        id="trust-on-raw-untrusted-projected-trusted-listed-denies",
    ),
    pytest.param(
        FirewallPeerCase(
            trust_proxy_headers=False,
            raw_host="198.51.100.10",
            projected_host="203.0.113.9",
            trusted_proxy_cidrs=(),
            allowlist=frozenset({"203.0.113.9"}),
            capture_raw_peer=False,
            expected_allowed=False,
        ),
        id="missing-capture-active-allowlist-denies",
    ),
    pytest.param(
        FirewallPeerCase(
            trust_proxy_headers=False,
            raw_host="198.51.100.10",
            projected_host="203.0.113.9",
            trusted_proxy_cidrs=(),
            allowlist=frozenset(),
            capture_raw_peer=False,
            expected_allowed=True,
        ),
        id="missing-capture-empty-allowlist-allows",
    ),
)


class FirewallRepositoryFake:
    def __init__(self, allowlist: frozenset[str]) -> None:
        self._allowlist = allowlist

    async def list_ip_addresses(self) -> set[str]:
        return set(self._allowlist)


@asynccontextmanager
async def _session() -> AsyncIterator[None]:
    yield None


def _configure_firewall_backend(monkeypatch: pytest.MonkeyPatch, allowlist: frozenset[str]) -> None:
    repository = FirewallRepositoryFake(allowlist)
    for module in (api_firewall_module, proxy_api_module):
        monkeypatch.setattr(module, "get_background_session", _session)
        monkeypatch.setattr(module, "FirewallRepository", lambda _session_value: repository)


def _headers(case: FirewallPeerCase) -> list[tuple[bytes, bytes]]:
    return [
        (b"x-forwarded-for", case.projected_host.encode()),
        (b"x-forwarded-proto", b"https"),
    ]


@pytest.mark.parametrize("case", CASES)
@pytest.mark.asyncio
async def test_http_firewall_uses_raw_peer_when_projection_differs(
    monkeypatch: pytest.MonkeyPatch,
    case: FirewallPeerCase,
) -> None:
    # Given: an allowlist and capture-then-project transport with opposing peer identities.
    _configure_firewall_backend(monkeypatch, case.allowlist)
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    projected_scope: list[tuple[str | None, str]] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse({"ok": True})
        await response(scope, receive, send)

    firewall = ApiFirewallMiddleware(
        downstream,
        trust_proxy_headers=case.trust_proxy_headers,
        trusted_proxy_networks=parse_trusted_proxy_networks(list(case.trusted_proxy_cidrs)),
        firewall_cache=FirewallIPCache(),
    )

    async def observe_projection(scope: Scope, receive: Receive, send: Send) -> None:
        client = scope.get("client")
        projected_scope.append((client[0] if client else None, scope["scheme"]))
        await firewall(scope, receive, send)

    app = TrustedProxyHeadersMiddleware(observe_projection) if case.capture_raw_peer else observe_projection
    transport_host = case.raw_host if case.capture_raw_peer else case.projected_host

    # When: the protected HTTP request crosses the real firewall middleware.
    async with AsyncClient(
        transport=ASGITransport(app=app, client=(transport_host, 50001)),
        base_url=("http://lb.example" if case.capture_raw_peer else "https://lb.example"),
    ) as client:
        response = await client.get("/v1/models", headers=dict(_headers(case)))

    # Then: raw identity controls enforcement while projection remains visible at the firewall boundary.
    assert projected_scope == [(case.projected_host, "https")]
    assert (response.status_code == 200) is case.expected_allowed


@pytest.mark.parametrize("case", CASES)
@pytest.mark.asyncio
async def test_websocket_firewall_uses_raw_peer_when_projection_differs(
    monkeypatch: pytest.MonkeyPatch,
    case: FirewallPeerCase,
) -> None:
    # Given: the same allowlist and capture-then-project identities on a protected WebSocket scope.
    _configure_firewall_backend(monkeypatch, case.allowlist)
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    monkeypatch.setattr(
        proxy_api_module,
        "get_settings",
        lambda: SimpleNamespace(
            firewall_trust_proxy_headers=case.trust_proxy_headers,
            firewall_trusted_proxy_cidrs=list(case.trusted_proxy_cidrs),
        ),
    )
    projected_scope: list[tuple[str | None, str]] = []
    denial: JSONResponse | None = None

    async def fail_receive() -> Message:
        raise AssertionError("firewall check must not receive a WebSocket frame")

    async def fail_send(_message: Message) -> None:
        raise AssertionError("firewall check must not send a WebSocket frame")

    async def check_firewall(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal denial
        client = scope.get("client")
        projected_scope.append((client[0] if client else None, scope["scheme"]))
        denial = await proxy_api_module._websocket_firewall_denial_response(WebSocket(scope, receive, send))

    scope: Scope = {
        "type": "websocket",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "scheme": ("ws" if case.capture_raw_peer else "wss"),
        "server": ("lb.example", 80),
        "client": ((case.raw_host if case.capture_raw_peer else case.projected_host), 50001),
        "root_path": "",
        "path": "/v1/responses",
        "raw_path": b"/v1/responses",
        "query_string": b"",
        "headers": _headers(case),
        "subprotocols": [],
        "state": {},
        "extensions": {},
    }
    app = TrustedProxyHeadersMiddleware(check_firewall) if case.capture_raw_peer else check_firewall

    # When: the scope crosses projection and the real protected WebSocket firewall helper.
    await app(scope, fail_receive, fail_send)

    # Then: raw identity controls enforcement while the projected WebSocket client and scheme remain intact.
    assert projected_scope == [(case.projected_host, "wss")]
    assert (denial is None) is case.expected_allowed

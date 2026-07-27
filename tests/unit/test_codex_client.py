from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import aiohttp
import pytest
from aiohttp.client_reqrep import ConnectionKey
from python_socks import ProxyType

from app.core.clients.codex import CodexClient, require_route_or_direct_egress_opt_in
from app.core.upstream_proxy import ResolvedProxyEndpoint, ResolvedUpstreamRoute

pytestmark = pytest.mark.unit


@dataclass
class _Response:
    status_code: int = 200
    content: bytes = b'{"ok": true}'
    headers: dict[str, str] | None = None

    def json(self) -> dict[str, bool]:
        return {"ok": True}


class _Session:
    def __init__(self, *, fail_first: bool = False, fail_all: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_first = fail_first
        self.fail_all = fail_all

    async def request(self, method: str, url: str, **kwargs: Any) -> _Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        if self.fail_all:
            raise OSError("proxy http://u:p@proxy.test:8080 failed")
        if self.fail_first and len(self.calls) == 1:
            raise OSError("proxy failed before response")
        return _Response(headers={"content-type": "application/json"})

    async def ws_connect(self, url: str, **kwargs: Any) -> object:
        self.calls.append({"url": url, **kwargs})
        return object()


class _HandshakeFailure(Exception):
    status = 426


class _WsFailSession:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def ws_connect(self, url: str, **kwargs: Any) -> object:
        self.calls.append({"url": url, **kwargs})
        raise _HandshakeFailure("Upgrade Required")


class _WsContext:
    def __init__(self) -> None:
        self.websocket = object()
        self.exited = False

    async def __aenter__(self) -> object:
        return self.websocket

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.exited = True


class _SocksWsSession:
    latest: "_SocksWsSession | None" = None

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.calls: list[dict[str, Any]] = []
        self.context = _WsContext()
        self.closed = False
        _SocksWsSession.latest = self

    def ws_connect(self, url: str, **kwargs: Any) -> _WsContext:
        self.calls.append({"url": url, **kwargs})
        return self.context

    async def close(self) -> None:
        self.closed = True


class _SocksConnector:
    calls: list[dict[str, Any]] = []

    def __init__(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


@pytest.fixture
def route() -> ResolvedUpstreamRoute:
    return ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080, "u", "p"),
        fallbacks=(ResolvedProxyEndpoint("ep_2", "http", "proxy-two.test", 8081),),
    )


@pytest.mark.asyncio
async def test_request_requires_route() -> None:
    client = CodexClient(_Session())
    with pytest.raises(ValueError, match="resolved upstream proxy route"):
        await client.request("GET", "https://upstream.test", route=cast(Any, None))


def test_direct_egress_requires_explicit_opt_in() -> None:
    with pytest.raises(ValueError, match="allow_direct_egress=True"):
        require_route_or_direct_egress_opt_in(
            route=None,
            allow_direct_egress=False,
            operation="test operation",
        )

    require_route_or_direct_egress_opt_in(
        route=None,
        allow_direct_egress=True,
        operation="test operation",
    )


@pytest.mark.asyncio
async def test_request_passes_resolver_proxy_and_builtin_fingerprint(route: ResolvedUpstreamRoute) -> None:
    session = _Session()
    client = CodexClient(session)

    response = await client.request("POST", "https://upstream.test", route=route, json={"x": 1})

    assert session.calls[0]["proxy"] == "http://u:p@proxy.test:8080"
    assert session.calls[0]["json"] == {"x": 1}
    assert response.content == b'{"ok": true}'


@pytest.mark.asyncio
async def test_streaming_request_can_opt_out_of_response_buffering(route: ResolvedUpstreamRoute) -> None:
    session = _Session()
    client = CodexClient(session)

    result = await client.request_with_route_metadata(
        "POST",
        "https://upstream.test",
        route=route,
        buffer_response=False,
        json={"x": 1},
    )

    assert session.calls[0]["proxy"] == "http://u:p@proxy.test:8080"
    assert "buffer_response" not in session.calls[0]
    assert isinstance(result.response, _Response)


@pytest.mark.asyncio
async def test_request_converts_legacy_files_payload_to_form_data(route: ResolvedUpstreamRoute) -> None:
    session = _Session()
    client = CodexClient(session)

    await client.request_with_route_metadata(
        "POST",
        "https://upstream.test/transcribe",
        route=route,
        files={"file": ("audio.wav", b"abc", "audio/wav")},
        data={"prompt": "summarize"},
    )

    assert "files" not in session.calls[0]
    assert isinstance(session.calls[0]["data"], aiohttp.FormData)
    assert session.calls[0]["proxy"] == "http://u:p@proxy.test:8080"


@pytest.mark.asyncio
@pytest.mark.parametrize("override", ["akamai", "extra_fp", "impersonate", "ja3", "proxies", "proxy"])
async def test_runtime_route_and_fingerprint_overrides_are_rejected(
    route: ResolvedUpstreamRoute,
    override: str,
) -> None:
    client = CodexClient(_Session())
    with pytest.raises(ValueError, match="controlled centrally"):
        await client.request("GET", "https://upstream.test", route=route, **{override: "bad"})


@pytest.mark.asyncio
async def test_pre_response_failure_uses_same_pool_fallback(route: ResolvedUpstreamRoute) -> None:
    session = _Session(fail_first=True)
    client = CodexClient(session)

    result = await client.request_with_route_metadata("GET", "https://upstream.test", route=route)

    assert result.fallback_used is True
    assert result.route.endpoint_id == "ep_2"
    assert [call["proxy"] for call in session.calls] == [
        "http://u:p@proxy.test:8080",
        "http://proxy-two.test:8081",
    ]


@pytest.mark.asyncio
async def test_non_idempotent_request_failure_does_not_fallback(route: ResolvedUpstreamRoute) -> None:
    session = _Session(fail_first=True)
    client = CodexClient(session)

    with pytest.raises(RuntimeError) as exc_info:
        await client.request_with_route_metadata("POST", "https://upstream.test", route=route, json={"x": 1})

    assert "ep_1" in str(exc_info.value)
    assert len(session.calls) == 1
    assert session.calls[0]["proxy"] == "http://u:p@proxy.test:8080"


@pytest.mark.asyncio
async def test_transport_errors_do_not_expose_proxy_credentials(route: ResolvedUpstreamRoute) -> None:
    client = CodexClient(_Session(fail_all=True))

    with pytest.raises(RuntimeError) as exc_info:
        await client.request("GET", "https://upstream.test", route=route)

    message = str(exc_info.value)
    assert "ep_2" in message
    assert "OSError" in message
    assert "u:p" not in message
    assert "proxy.test:8080" not in message


@pytest.mark.asyncio
async def test_websocket_transport_error_preserves_handshake_status(route: ResolvedUpstreamRoute) -> None:
    client = CodexClient(_WsFailSession())

    with pytest.raises(RuntimeError) as exc_info:
        await client.open_ws_with_route_metadata("wss://upstream.test", route=route)

    assert getattr(exc_info.value, "status_code") == 426


@pytest.mark.asyncio
async def test_websocket_connector_error_preserves_pre_dispatch_retry_provenance(
    route: ResolvedUpstreamRoute,
) -> None:
    connection_key = ConnectionKey("upstream.test", 443, True, True, None, None, None)

    class _ConnectorFailSession:
        def ws_connect(self, *_args: object, **_kwargs: object) -> object:
            raise aiohttp.ClientConnectorError(connection_key, ConnectionRefusedError("connection refused"))

    client = CodexClient(_ConnectorFailSession())

    with pytest.raises(RuntimeError) as exc_info:
        await client.open_ws_with_route_metadata("wss://upstream.test", route=route)

    assert getattr(exc_info.value, "failure_phase") == "connect"
    assert getattr(exc_info.value, "retryable_same_contract") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("scheme", ["socks5", "socks5h"])
async def test_socks_websocket_uses_proxy_connector_and_closes_session(
    monkeypatch: pytest.MonkeyPatch,
    scheme: str,
) -> None:
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", scheme, "proxy.test", 1080, "u;session", "p@x:y"),
    )
    _SocksConnector.calls = []
    _SocksWsSession.latest = None
    monkeypatch.setattr("app.core.clients.codex.ProxyConnector", _SocksConnector)
    monkeypatch.setattr("app.core.clients.codex.aiohttp.ClientSession", _SocksWsSession)
    client = CodexClient(_Session())

    result = await client.open_ws_with_route_metadata("wss://upstream.test", route=route, heartbeat=30)

    session = _SocksWsSession.latest
    assert session is not None
    assert _SocksConnector.calls[0]["ssl"] is not None
    assert _SocksConnector.calls[0] | {"ssl": "present"} == {
        "host": "proxy.test",
        "port": 1080,
        "proxy_type": ProxyType.SOCKS5,
        "username": "u;session",
        "password": "p@x:y",
        "rdns": True,
        "ssl": "present",
    }
    assert session.calls == [{"url": "wss://upstream.test", "heartbeat": 30}]
    assert "proxy" not in session.calls[0]
    assert result.websocket is session.context.websocket

    await result.context.__aexit__(None, None, None)

    assert session.context.exited is True
    assert session.closed is True

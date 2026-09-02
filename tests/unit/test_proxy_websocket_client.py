from __future__ import annotations

import asyncio
import contextlib
import errno
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import aiohttp
import pytest
from websockets.asyncio.server import serve as websocket_serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosedError, InvalidHandshake, InvalidProxy, InvalidStatus
from websockets.frames import Close
from websockets.http11 import Response

import app.core.clients.proxy_websocket as proxy_websocket_module
from app.core.clients.codex import CodexTransportError, CodexWebSocketResult
from app.core.clients.native_egress import (
    NativeEgressTransportError,
    NativeEgressUnavailable,
    NativeWebSocketMessage,
    NativeWebSocketRequest,
)
from app.core.clients.proxy import ProxyResponseError, is_confirmed_pre_dispatch_transport_error
from app.core.clients.proxy_websocket import (
    UPSTREAM_WEBSOCKET_LIVENESS_TIMEOUT_CODE,
    CodexUpstreamWebSocket,
    NativeUpstreamWebSocket,
    RealtimeWebSocketProtocol,
    UpstreamWebSocketTransportError,
    WebsocketsUpstreamWebSocket,
    connect_live_websocket,
    connect_responses_websocket,
)
from app.core.upstream_proxy import ResolvedProxyEndpoint, ResolvedUpstreamRoute
from tests.unit._proxy_test_helpers import runtime_basic_auth_url


def _proxy_error_code(exc: ProxyResponseError) -> str | None:
    return exc.payload["error"].get("code")


def _proxy_error_message(exc: ProxyResponseError) -> str | None:
    return exc.payload["error"].get("message")


def _proxy_error_type(exc: ProxyResponseError) -> str | None:
    return exc.payload["error"].get("type")


class _UnexpectedAiohttpSession:
    async def ws_connect(self, *args, **kwargs):  # pragma: no cover - red-path guard
        raise AssertionError("aiohttp ws_connect should not be used for upstream websocket transport")


class _UnexpectedHttpClient:
    websocket_session = _UnexpectedAiohttpSession()


class _FakeConnection:
    connection_lost_waiter: asyncio.Future[object]

    def __init__(self, *, subprotocol: str | None = None) -> None:
        self.sent: list[str | bytes] = []
        self.closed = False
        self.subprotocol = subprotocol

    async def send(self, data: str | bytes) -> None:
        self.sent.append(data)

    async def recv(self) -> str:
        return '{"type":"response.completed"}'

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True


class _FakeNativeWebSocket:
    def __init__(self, *, subprotocol: str | None = None) -> None:
        self.sent: list[str | bytes] = []
        self.closed: tuple[int, str] | None = None
        self.subprotocol = subprotocol

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)

    async def receive(self) -> NativeWebSocketMessage:
        return NativeWebSocketMessage(kind="text", text='{"type":"response.completed"}')

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)

    def response_header(self, name: str) -> str | None:
        if name.lower() == "sec-websocket-protocol":
            return self.subprotocol
        return None


@pytest.mark.asyncio
async def test_websockets_response_websocket_consumes_connection_lost_waiter_error():
    connection = _FakeConnection()
    connection.connection_lost_waiter = asyncio.get_running_loop().create_future()

    WebsocketsUpstreamWebSocket(cast(Any, connection))
    connection.connection_lost_waiter.set_exception(RuntimeError("keepalive ping timeout"))
    await asyncio.sleep(0)

    assert connection.connection_lost_waiter.exception() is not None


async def _local_proxy_tunnel_handler(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    proxy_hits: list[str],
) -> None:
    target_writer: asyncio.StreamWriter | None = None
    try:
        request_line = await reader.readline()
        method, target, _version = request_line.decode("ascii").strip().split(" ", 2)
        assert method == "CONNECT"
        host, port_text = target.rsplit(":", 1)
        proxy_hits.append(target)

        while await reader.readline() != b"\r\n":
            pass

        target_reader, target_writer = await asyncio.open_connection(host, int(port_text))
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()

        async def relay(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
            try:
                while data := await src.read(65536):
                    dst.write(data)
                    await dst.drain()
            except (ConnectionError, asyncio.CancelledError):
                pass

        relays = [
            asyncio.create_task(relay(reader, target_writer)),
            asyncio.create_task(relay(target_reader, writer)),
        ]
        try:
            await asyncio.wait(relays, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in relays:
                task.cancel()
            await asyncio.gather(*relays, return_exceptions=True)
    finally:
        writer.close()
        if target_writer is not None:
            target_writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()
        if target_writer is not None:
            with contextlib.suppress(ConnectionError):
                await target_writer.wait_closed()


class _FakeCodexWebSocket:
    def __init__(self, *, protocol: str | None = None) -> None:
        self.closed = False
        self.protocol = protocol
        self.response = SimpleNamespace(headers={"x-codex-turn-state": "turn-routed"})

    async def send_str(self, data: str) -> None:
        del data

    async def send_bytes(self, data: bytes) -> None:
        del data

    async def recv(self) -> tuple[bytes, int]:
        return b'{"type":"response.completed"}', 1

    async def receive(self) -> object:
        return b'{"type":"response.completed"}'

    def exception(self) -> BaseException | None:
        return None

    async def close(self, *, code: int = 1000, message: bytes = b"") -> None:
        del code, message
        self.closed = True


class _FakeCodexErrorWebSocket(_FakeCodexWebSocket):
    def __init__(self, error: BaseException | None) -> None:
        super().__init__()
        self.error = error

    async def receive(self) -> aiohttp.WSMessage:
        return aiohttp.WSMessage(
            aiohttp.WSMsgType.ERROR,
            self.error,
            None,
        )


class _FakeCodexClient:
    def __init__(self, websocket: _FakeCodexWebSocket | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.websocket = websocket or _FakeCodexWebSocket()

    async def open_ws_with_route_metadata(
        self,
        url: str,
        *,
        route: ResolvedUpstreamRoute,
        **kwargs: object,
    ) -> CodexWebSocketResult:
        self.calls.append({"url": url, "route": route, **kwargs})
        return CodexWebSocketResult(
            websocket=self.websocket,
            context=None,
            route=route,
            fallback_used=False,
        )

    async def close(self) -> None:
        return None


class _FailingCodexClient:
    def __init__(self) -> None:
        self.closed = False

    async def open_ws_with_route_metadata(
        self,
        url: str,
        *,
        route: ResolvedUpstreamRoute,
        **kwargs: object,
    ) -> CodexWebSocketResult:
        del url, route, kwargs
        raise CodexTransportError("Codex upstream websocket failed via proxy endpoint ep_1: OSError")

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_live_direct_adapter_preserves_abnormal_close_code_and_reason() -> None:
    class Connection:
        async def recv(self):
            raise ConnectionClosedError(Close(1011, "server restart"), None)

    websocket = WebsocketsUpstreamWebSocket(
        cast(Any, Connection()),
        uses_proxy=False,
        preserve_close_semantics=True,
    )

    message = await websocket.receive()

    assert message.kind == "close"
    assert message.close_code == 1011
    assert message.close_reason == "server restart"
    assert message.error is None


@pytest.mark.asyncio
async def test_direct_adapter_classifies_keepalive_timeout() -> None:
    class Connection:
        async def recv(self):
            raise ConnectionClosedError(None, Close(1011, "keepalive ping timeout"))

    websocket = WebsocketsUpstreamWebSocket(cast(Any, Connection()))

    message = await websocket.receive()

    assert message.kind == "error"
    assert message.error_code == UPSTREAM_WEBSOCKET_LIVENESS_TIMEOUT_CODE


@pytest.mark.asyncio
async def test_direct_adapter_classifies_keepalive_timeout_after_close_ack() -> None:
    class Connection:
        async def recv(self):
            raise ConnectionClosedError(
                Close(1000, "acknowledged"),
                Close(1011, "keepalive ping timeout"),
                False,
            )

    websocket = WebsocketsUpstreamWebSocket(cast(Any, Connection()))

    message = await websocket.receive()

    assert message.kind == "error"
    assert message.error_code == UPSTREAM_WEBSOCKET_LIVENESS_TIMEOUT_CODE


@pytest.mark.asyncio
async def test_native_direct_adapter_classifies_helper_pong_timeout() -> None:
    class NativeConnection(_FakeNativeWebSocket):
        async def receive(self) -> NativeWebSocketMessage:
            raise NativeEgressTransportError(
                "native websocket pong timed out",
                failure_phase="liveness_timeout",
            )

    websocket = NativeUpstreamWebSocket(cast(Any, NativeConnection()))

    message = await websocket.receive()

    assert message.kind == "error"
    assert message.error_code == UPSTREAM_WEBSOCKET_LIVENESS_TIMEOUT_CODE


@pytest.mark.asyncio
async def test_direct_adapter_does_not_trust_peer_keepalive_timeout_marker() -> None:
    class Connection:
        async def recv(self):
            raise ConnectionClosedError(
                Close(1011, "keepalive ping timeout"),
                Close(1011, "keepalive ping timeout"),
                True,
            )

    websocket = WebsocketsUpstreamWebSocket(cast(Any, Connection()))

    message = await websocket.receive()

    assert message.kind == "error"
    assert message.error_code is None


@pytest.mark.asyncio
async def test_routed_adapter_classifies_heartbeat_timeout() -> None:
    websocket = CodexUpstreamWebSocket(
        _FakeCodexErrorWebSocket(aiohttp.ServerTimeoutError("No PONG received after 60.0 seconds"))
    )

    message = await websocket.receive()

    assert message.kind == "error"
    assert message.error_code == UPSTREAM_WEBSOCKET_LIVENESS_TIMEOUT_CODE


@pytest.mark.asyncio
async def test_routed_adapter_classifies_heartbeat_timeout_stored_between_receive_calls() -> None:
    heartbeat_timeout = aiohttp.ServerTimeoutError("No PONG received after 60.0 seconds")

    class ClosedWebSocket(_FakeCodexWebSocket):
        async def receive(self) -> aiohttp.WSMessage:
            return aiohttp.WSMessage(aiohttp.WSMsgType.CLOSED, None, None)

        def exception(self) -> BaseException | None:
            return heartbeat_timeout

    websocket = CodexUpstreamWebSocket(ClosedWebSocket())

    message = await websocket.receive()

    assert message.kind == "error"
    assert message.error_code == UPSTREAM_WEBSOCKET_LIVENESS_TIMEOUT_CODE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    ["request", b"request"],
    ids=["text", "bytes"],
)
async def test_routed_adapter_send_preserves_stored_heartbeat_timeout(
    payload: str | bytes,
) -> None:
    heartbeat_timeout = aiohttp.ServerTimeoutError("No PONG received after 60.0 seconds")

    class ClosedWebSocket(_FakeCodexWebSocket):
        async def send_str(self, data: str) -> None:
            del data
            raise RuntimeError("Cannot write to closing transport")

        async def send_bytes(self, data: bytes) -> None:
            del data
            raise RuntimeError("Cannot write to closing transport")

        def exception(self) -> BaseException | None:
            return heartbeat_timeout

    websocket = CodexUpstreamWebSocket(ClosedWebSocket())

    with pytest.raises(UpstreamWebSocketTransportError) as exc_info:
        if isinstance(payload, str):
            await websocket.send_text(payload)
        else:
            await websocket.send_bytes(payload)

    assert exc_info.value.error_code == UPSTREAM_WEBSOCKET_LIVENESS_TIMEOUT_CODE


@pytest.mark.asyncio
async def test_codex_responses_websocket_closes_owned_client_when_context_exit_fails():
    class _FailingContext:
        async def __aexit__(self, *_args: object) -> None:
            raise RuntimeError("websocket context exit failed")

    codex_client = _FailingCodexClient()
    websocket = CodexUpstreamWebSocket(
        _FakeCodexWebSocket(),
        context=_FailingContext(),
        codex_client=cast(Any, codex_client),
        owns_codex_client=True,
    )

    with pytest.raises(RuntimeError, match="websocket context exit failed"):
        await websocket.close()

    assert codex_client.closed is True


@pytest.mark.asyncio
async def test_connect_responses_websocket_uses_websockets_transport(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )

    websocket = await connect_responses_websocket(
        {
            "openai-beta": "responses_websockets=2026-02-06",
            "session_id": "session-1",
            "User-Agent": "Codex CLI Test",
            "Origin": "https://chatgpt.com",
            "Cookie": "dashboard_session=secret",
        },
        "access-token",
        "account-123",
        allow_direct_egress=True,
    )

    await websocket.send_text("hello")

    assert fake_connection.sent == ["hello"]
    assert seen["url"] == "wss://chatgpt.com/backend-api/codex/responses"
    kwargs = cast(dict[str, object], seen["kwargs"])
    assert kwargs["origin"] == "https://chatgpt.com"
    assert kwargs["user_agent_header"] == "Codex CLI Test"
    assert kwargs["proxy"] is None
    assert kwargs["open_timeout"] == 7.0
    assert "ping_interval" not in kwargs
    assert kwargs["ping_timeout"] == 120.0
    assert kwargs["max_size"] == 4321
    assert kwargs["compression"] == "deflate"
    assert "subprotocols" not in kwargs
    additional_headers = cast(dict[str, str], kwargs["additional_headers"])
    assert additional_headers["Authorization"] == "Bearer access-token"
    assert additional_headers["chatgpt-account-id"] == "account-123"
    assert additional_headers["openai-beta"] == "responses_websockets=2026-02-06"
    assert additional_headers["session_id"] == "session-1"
    assert "Cookie" not in additional_headers
    assert "User-Agent" not in additional_headers
    assert "Origin" not in additional_headers


@pytest.mark.asyncio
async def test_connect_responses_websocket_prefers_native_direct_transport(monkeypatch) -> None:
    native_websocket = _FakeNativeWebSocket(subprotocol="openai")
    native_client = SimpleNamespace(websocket=AsyncMock(return_value=native_websocket))
    python_connect = AsyncMock(side_effect=AssertionError("Python websocket connector must not run"))
    monkeypatch.setattr(proxy_websocket_module, "discover_native_egress_client", lambda: native_client)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", python_connect)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )

    websocket = await connect_responses_websocket(
        {
            "User-Agent": "Codex CLI Test",
            "Origin": "https://chatgpt.com",
            "openai-beta": "responses_websockets=2026-02-06",
        },
        "access-token",
        "account-123",
        allow_direct_egress=True,
    )
    await websocket.send_text("turn")
    message = await websocket.receive()

    request = native_client.websocket.await_args.args[0]
    assert isinstance(request, NativeWebSocketRequest)
    assert request.url == "wss://chatgpt.com/backend-api/codex/responses"
    assert request.connect_timeout_seconds == 7.0
    assert request.max_message_bytes == 4321
    assert request.ping_interval_seconds == 20.0
    assert request.ping_timeout_seconds == 120.0
    assert request.proxy_url is None
    assert request.headers["Authorization"] == "Bearer access-token"
    assert request.headers["User-Agent"] == "Codex CLI Test"
    assert request.headers["Origin"] == "https://chatgpt.com"
    assert native_websocket.sent == ["turn"]
    assert message.kind == "text"
    assert websocket.response_header("sec-websocket-protocol") == "openai"
    python_connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_connect_live_websocket_native_direct_preserves_subprotocol_offer(monkeypatch) -> None:
    native_websocket = _FakeNativeWebSocket(subprotocol="live.v1")
    native_client = SimpleNamespace(websocket=AsyncMock(return_value=native_websocket))
    monkeypatch.setattr(proxy_websocket_module, "discover_native_egress_client", lambda: native_client)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )

    websocket = await connect_live_websocket(
        "rtc_live",
        {"User-Agent": "Codex CLI Test"},
        "access-token",
        "account-123",
        protocol=RealtimeWebSocketProtocol.LIVE_V3,
        allow_direct_egress=True,
        subprotocols=("live.v0", "live.v1"),
    )

    request = native_client.websocket.await_args.args[0]
    assert request.headers["sec-websocket-protocol"] == "live.v0, live.v1"
    assert websocket.response_header("sec-websocket-protocol") == "live.v1"


@pytest.mark.asyncio
async def test_native_direct_websocket_falls_back_only_when_helper_is_unavailable(monkeypatch) -> None:
    native_client = SimpleNamespace(websocket=AsyncMock(side_effect=NativeEgressUnavailable("helper could not start")))
    python_connection = _FakeConnection()
    python_connect = AsyncMock(return_value=python_connection)
    monkeypatch.setattr(proxy_websocket_module, "discover_native_egress_client", lambda: native_client)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", python_connect)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )

    websocket = await connect_responses_websocket(
        {},
        "access-token",
        "account-123",
        allow_direct_egress=True,
    )
    await websocket.send_text("fallback")

    assert python_connection.sent == ["fallback"]
    python_connect.assert_awaited_once()


@pytest.mark.asyncio
async def test_native_direct_websocket_denial_does_not_open_python_transport(monkeypatch) -> None:
    native_client = SimpleNamespace(
        websocket=AsyncMock(
            side_effect=NativeEgressTransportError(
                "native websocket handshake failed",
                failure_phase="connect",
                status_code=429,
                headers=(("content-type", "application/json"),),
                body=b'{"error":{"code":"rate_limit_exceeded","message":"slow down"}}',
            )
        )
    )
    python_connect = AsyncMock(side_effect=AssertionError("denial must not fall back"))
    monkeypatch.setattr(proxy_websocket_module, "discover_native_egress_client", lambda: native_client)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", python_connect)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await connect_responses_websocket(
            {},
            "access-token",
            "account-123",
            allow_direct_egress=True,
        )

    assert exc_info.value.status_code == 502
    assert _proxy_error_code(exc_info.value) == "rate_limit_exceeded"
    python_connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_websocket_network_send_and_receive_are_typed_and_rotate_without_reconnect(monkeypatch):
    class _NetworkFailureConnection(_FakeConnection):
        async def send(self, data: str | bytes) -> None:
            del data
            raise OSError(errno.ENETUNREACH, "Network is unreachable")

        async def recv(self) -> str:
            raise OSError(errno.ENETUNREACH, "Network is unreachable")

    connection = _NetworkFailureConnection()
    websocket_connect = AsyncMock(return_value=connection)
    rotate = AsyncMock(return_value="rotated")
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", websocket_connect)
    monkeypatch.setattr(proxy_websocket_module, "rotate_shared_http_transport", rotate)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )

    websocket = await connect_responses_websocket(
        {"openai-beta": "responses_websockets=2026-02-06"},
        "access-token",
        "account-123",
        allow_direct_egress=True,
    )

    with pytest.raises(UpstreamWebSocketTransportError) as exc_info:
        await websocket.send_text('{"type":"response.create"}')
    message = await websocket.receive()

    assert exc_info.value.error_code == "proxy_network_unavailable"
    assert message.kind == "error"
    assert message.error_code == "proxy_network_unavailable"
    websocket_connect.assert_awaited_once()
    assert rotate.await_count == 2
    assert all(call.kwargs["transport"] == "websocket" for call in rotate.await_args_list)


@pytest.mark.asyncio
async def test_connect_responses_websocket_routed_codex_call_preserves_size_limit(monkeypatch):
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080),
    )
    codex_client = _FakeCodexClient()
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )

    websocket = await connect_responses_websocket(
        {
            "openai-beta": "responses_websockets=2026-02-06",
            "User-Agent": "Codex CLI Test",
            "Origin": "https://chatgpt.com",
        },
        "access-token",
        "account-123",
        route=route,
        codex_client=cast(Any, codex_client),
    )
    await websocket.close()

    assert codex_client.calls
    call = codex_client.calls[0]
    assert call["url"] == "wss://chatgpt.com/backend-api/codex/responses"
    assert call["route"] is route
    assert call["timeout"] == 7.0
    assert call["max_msg_size"] == 4321
    assert call["heartbeat"] == 120.0
    assert call["compress"] == 15
    assert "max_size" not in call
    assert "protocols" not in call
    assert websocket.response_header("x-codex-turn-state") == "turn-routed"


@pytest.mark.asyncio
async def test_connect_responses_websocket_wraps_native_routed_result(monkeypatch) -> None:
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080),
    )
    native_websocket = _FakeNativeWebSocket(subprotocol="openai")

    class RoutedNativeClient:
        closed = False

        async def open_ws_with_route_metadata(self, *_args, **_kwargs) -> CodexWebSocketResult:
            return CodexWebSocketResult(
                websocket=native_websocket,
                context=None,
                route=route,
                fallback_used=False,
                native=True,
            )

        async def close(self) -> None:
            self.closed = True

    codex_client = RoutedNativeClient()
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )

    websocket = await connect_responses_websocket(
        {},
        "access-token",
        "account-123",
        route=route,
        codex_client=cast(Any, codex_client),
    )
    await websocket.send_text("turn")
    message = await websocket.receive()
    await websocket.close()

    assert native_websocket.sent == ["turn"]
    assert message.kind == "text"
    assert websocket.response_header("sec-websocket-protocol") == "openai"
    assert codex_client.closed is False


@pytest.mark.asyncio
async def test_connect_live_websocket_routed_call_disables_denial_replay_and_enables_heartbeat(monkeypatch):
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080),
    )
    codex_client = _FakeCodexClient(_FakeCodexWebSocket(protocol="live.v1"))
    offered_subprotocols = ("live.v0", "live.v1")
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )

    websocket = await connect_live_websocket(
        "rtc_live",
        {"Sec-WebSocket-Protocol": "raw-header-must-not-be-forwarded"},
        "access-token",
        "account-123",
        protocol=RealtimeWebSocketProtocol.LIVE_V3,
        route=route,
        codex_client=cast(Any, codex_client),
        subprotocols=offered_subprotocols,
    )
    await websocket.close()

    call = codex_client.calls[0]
    assert call["retry_handshake_status"] is False
    assert call["retry_network_errors"] is False
    assert call["heartbeat"] == 120.0
    assert call["max_msg_size"] == 4321
    assert call["protocols"] is offered_subprotocols
    headers = cast(dict[str, str], call["headers"])
    assert not any(key.lower() == "sec-websocket-protocol" for key in headers)
    assert websocket.response_header("sec-websocket-protocol") == "live.v1"


def test_routed_live_websocket_exposes_unoffered_raw_subprotocol_for_rejection() -> None:
    websocket = CodexUpstreamWebSocket(
        _FakeCodexWebSocket(protocol=None),
        response_headers={
            "Sec-WebSocket-Protocol": "live.private",
        },
    )

    assert websocket.response_header("sec-websocket-protocol") == "live.private"


@pytest.mark.asyncio
async def test_connect_live_websocket_closes_owned_client_when_handshake_is_cancelled(monkeypatch):
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080),
    )

    class HangingCodexClient:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.close_calls = 0

        async def open_ws_with_route_metadata(self, *_args, **_kwargs):
            self.started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def close(self) -> None:
            self.close_calls += 1

    codex_client = HangingCodexClient()
    monkeypatch.setattr(proxy_websocket_module, "create_codex_session", lambda: object())
    monkeypatch.setattr(proxy_websocket_module, "CodexClient", lambda _session: codex_client)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )

    task = asyncio.create_task(
        connect_live_websocket(
            "rtc_live",
            {},
            "access-token",
            "account-123",
            protocol=RealtimeWebSocketProtocol.LIVE_V3,
            route=route,
        )
    )
    await asyncio.wait_for(codex_client.started.wait(), timeout=1)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert codex_client.close_calls == 1


@pytest.mark.asyncio
async def test_connect_live_websocket_preserves_handshake_status_without_endpoint_disclosure(monkeypatch):
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_secret", "http", "proxy.test", 8080),
    )

    class DeniedCodexClient:
        async def open_ws_with_route_metadata(self, *_args, **_kwargs):
            raise CodexTransportError(
                "sensitive denial via endpoint ep_secret",
                status_code=403,
                error_code="upstream_websocket_handshake_failed",
            )

        async def close(self) -> None:
            return None

    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await connect_live_websocket(
            "rtc_live",
            {},
            "access-token",
            "account-123",
            protocol=RealtimeWebSocketProtocol.LIVE_V3,
            route=route,
            codex_client=cast(Any, DeniedCodexClient()),
        )

    assert exc_info.value.status_code == 403
    assert _proxy_error_code(exc_info.value) == "upstream_websocket_handshake_failed"
    assert "ep_secret" not in (_proxy_error_message(exc_info.value) or "")
    assert "sensitive" not in (_proxy_error_message(exc_info.value) or "")


@pytest.mark.asyncio
async def test_connect_live_websocket_direct_invalid_status_is_credential_safe(monkeypatch):
    denial = Response(
        403,
        "malicious denial reason with account-secret",
        Headers(
            {
                "Content-Type": "application/json",
                "X-Private-Credential": "private-header-secret",
            }
        ),
        body=(b'{"error":{"code":"malicious_error","message":"malicious denial body with bearer-secret"}}'),
    )

    async def fake_websocket_connect(url: str, **kwargs):
        del url, kwargs
        raise InvalidStatus(denial)

    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await connect_live_websocket(
            "rtc_live",
            {},
            "access-token",
            "account-123",
            protocol=RealtimeWebSocketProtocol.LIVE_V3,
            allow_direct_egress=True,
        )

    assert exc_info.value.status_code == 403
    assert _proxy_error_code(exc_info.value) == "upstream_websocket_handshake_failed"
    assert _proxy_error_type(exc_info.value) == "server_error"
    message = _proxy_error_message(exc_info.value)
    assert message == "Upstream websocket handshake failed with HTTP 403"
    assert message is not None
    for secret in (
        "account-secret",
        "private-header-secret",
        "bearer-secret",
        "malicious_error",
    ):
        assert secret not in message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected_message"),
    [
        (InvalidHandshake("private-handshake-secret"), "Invalid upstream websocket handshake"),
        (OSError("private-network-secret"), "Upstream websocket connection failed"),
    ],
    ids=["invalid-handshake", "os-error"],
)
async def test_connect_live_websocket_redacts_generic_direct_failures(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_message: str,
) -> None:
    async def fake_websocket_connect(url: str, **kwargs):
        del url, kwargs
        raise failure

    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await connect_live_websocket(
            "rtc_live",
            {},
            "access-token",
            "account-123",
            protocol=RealtimeWebSocketProtocol.LIVE_V3,
            allow_direct_egress=True,
        )

    assert exc_info.value.status_code == 502
    assert _proxy_error_message(exc_info.value) == expected_message
    assert "private" not in expected_message


@pytest.mark.asyncio
async def test_connect_responses_websocket_routed_transport_error_maps_proxy_error(monkeypatch):
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080),
    )
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await connect_responses_websocket(
            {"openai-beta": "responses_websockets=2026-02-06"},
            "access-token",
            "account-123",
            route=route,
            codex_client=cast(Any, _FailingCodexClient()),
        )

    assert exc_info.value.status_code == 502
    assert _proxy_error_code(exc_info.value) == "upstream_unavailable"
    assert "ep_1" in (_proxy_error_message(exc_info.value) or "")
    # An ambiguous routed transport failure must not authorize replay.
    assert exc_info.value.retryable_same_contract is False


@pytest.mark.asyncio
async def test_connect_responses_websocket_routed_pre_dispatch_failure_carries_provenance(monkeypatch):
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080),
    )
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
        ),
    )

    class _PreDispatchFailingCodexClient(_FailingCodexClient):
        async def open_ws_with_route_metadata(
            self,
            url: str,
            *,
            route: ResolvedUpstreamRoute,
            **kwargs: object,
        ) -> CodexWebSocketResult:
            del url, route, kwargs
            raise CodexTransportError(
                "Codex upstream websocket failed via proxy endpoint ep_1: ClientProxyConnectionError",
                failure_phase="connect",
                retryable_same_contract=True,
            )

    with pytest.raises(ProxyResponseError) as exc_info:
        await connect_responses_websocket(
            {"openai-beta": "responses_websockets=2026-02-06"},
            "access-token",
            "account-123",
            route=route,
            codex_client=cast(Any, _PreDispatchFailingCodexClient()),
        )

    assert exc_info.value.status_code == 502
    assert _proxy_error_code(exc_info.value) == "upstream_unavailable"
    assert exc_info.value.retryable_same_contract is True
    assert exc_info.value.failure_phase == "connect"
    assert exc_info.value.failure_detail == "proxy_connect_pre_dispatch"
    assert is_confirmed_pre_dispatch_transport_error(exc_info.value) is True


@pytest.mark.asyncio
async def test_connect_responses_websocket_routed_tls_verification_failure_is_not_replayable(monkeypatch):
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080),
    )
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
        ),
    )

    class _TLSFailingCodexClient(_FailingCodexClient):
        async def open_ws_with_route_metadata(
            self,
            url: str,
            *,
            route: ResolvedUpstreamRoute,
            **kwargs: object,
        ) -> CodexWebSocketResult:
            del url, route, kwargs
            raise CodexTransportError(
                "Codex upstream websocket failed via proxy endpoint ep_1: ClientConnectorCertificateError",
                failure_phase="connect",
                retryable_same_contract=True,
                is_tls_verification_failure=True,
            )

    with pytest.raises(ProxyResponseError) as exc_info:
        await connect_responses_websocket(
            {"openai-beta": "responses_websockets=2026-02-06"},
            "access-token",
            "account-123",
            route=route,
            codex_client=cast(Any, _TLSFailingCodexClient()),
        )

    assert exc_info.value.retryable_same_contract is False
    assert is_confirmed_pre_dispatch_transport_error(exc_info.value) is False


@pytest.mark.asyncio
async def test_connect_responses_websocket_appends_required_beta_header(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )

    await connect_responses_websocket(
        {"OpenAI-Beta": "assistants=v2"},
        "access-token",
        None,
        allow_direct_egress=True,
    )

    kwargs = cast(dict[str, object], seen["kwargs"])
    additional_headers = cast(dict[str, str], kwargs["additional_headers"])
    assert additional_headers["OpenAI-Beta"] == "assistants=v2, responses_websockets=2026-02-06"


@pytest.mark.asyncio
async def test_connect_responses_websocket_drops_http_responses_beta_and_encoding_header(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )

    await connect_responses_websocket(
        {
            "Connection": "keep-alive, X-Handshake-Debug",
            "Keep-Alive": "timeout=5",
            "Proxy-Authorization": "Basic secret",
            "Proxy-Connection": "keep-alive",
            "TE": "trailers",
            "Trailer": "X-Trailer",
            "Transfer-Encoding": "chunked",
            "Upgrade": "websocket",
            "X-Handshake-Debug": "1",
            "accept-encoding": "gzip, deflate, br, zstd",
            "OpenAI-Beta": "responses=experimental, assistants=v2",
            "session_id": "session-1",
        },
        "access-token",
        None,
        allow_direct_egress=True,
    )

    kwargs = cast(dict[str, object], seen["kwargs"])
    additional_headers = cast(dict[str, str], kwargs["additional_headers"])
    lowered_headers = {key.lower(): value for key, value in additional_headers.items()}
    for header_name in (
        "accept-encoding",
        "connection",
        "keep-alive",
        "proxy-authorization",
        "proxy-connection",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "x-handshake-debug",
    ):
        assert header_name not in lowered_headers
    assert additional_headers["OpenAI-Beta"] == "assistants=v2, responses_websockets=2026-02-06"
    assert additional_headers["session_id"] == "session-1"


@pytest.mark.asyncio
async def test_connect_responses_websocket_maps_invalid_status(monkeypatch):
    async def fake_websocket_connect(url: str, **kwargs):
        raise InvalidStatus(
            Response(
                403,
                "Forbidden",
                Headers({"Content-Type": "application/json"}),
                b'{"error":{"message":"Forbidden","type":"permission_error","code":"forbidden"}}',
            )
        )

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await connect_responses_websocket(
            {"openai-beta": "responses_websockets=2026-02-06"},
            "access-token",
            "account-123",
            allow_direct_egress=True,
        )

    assert exc_info.value.status_code == 403
    assert _proxy_error_code(exc_info.value) == "forbidden"
    assert _proxy_error_type(exc_info.value) == "permission_error"
    assert exc_info.value.failure_detail is None


@pytest.mark.asyncio
async def test_connect_responses_websocket_marks_cloudflare_challenge(monkeypatch):
    async def fake_websocket_connect(url: str, **kwargs):
        del url, kwargs
        raise InvalidStatus(
            Response(
                403,
                "Forbidden",
                Headers(
                    {
                        "Content-Type": "text/html; charset=UTF-8",
                        "cf-mitigated": "challenge",
                    }
                ),
                b"<html><title>Just a moment...</title></html>",
            )
        )

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await connect_responses_websocket(
            {"openai-beta": "responses_websockets=2026-02-06"},
            "access-token",
            "account-123",
            allow_direct_egress=True,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.failure_phase == "connect"
    assert exc_info.value.failure_detail == proxy_websocket_module.UPSTREAM_WEBSOCKET_TRANSPORT_FAILURE_DETAIL


@pytest.mark.asyncio
async def test_connect_responses_websocket_can_opt_in_to_env_proxy(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7890")
    monkeypatch.setenv("all_proxy", "socks5://127.0.0.1:7891")

    await connect_responses_websocket(
        {"openai-beta": "responses_websockets=2026-02-06"},
        "access-token",
        None,
        allow_direct_egress=True,
    )

    kwargs = cast(dict[str, object], seen["kwargs"])
    assert kwargs["proxy"] == "http://127.0.0.1:7890"


@pytest.mark.asyncio
async def test_connect_responses_websocket_disables_proxy_when_env_proxy_is_unset(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )
    for name in (
        "no_proxy",
        "NO_PROXY",
        "wss_proxy",
        "WSS_PROXY",
        "https_proxy",
        "HTTPS_PROXY",
        "socks_proxy",
        "SOCKS_PROXY",
        "all_proxy",
        "ALL_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)

    await connect_responses_websocket(
        {"openai-beta": "responses_websockets=2026-02-06"},
        "access-token",
        None,
        allow_direct_egress=True,
    )

    kwargs = cast(dict[str, object], seen["kwargs"])
    assert kwargs["proxy"] is None


@pytest.mark.asyncio
async def test_connect_responses_websocket_sanitizes_ws_error_payload(monkeypatch):
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080),
    )
    codex_client = _FakeCodexClient(
        _FakeCodexErrorWebSocket(
            OSError("proxy " + runtime_basic_auth_url("user", "pass", "proxy.local:8080") + " websocket failed")
        )
    )
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )
    websocket = await connect_responses_websocket(
        {"openai-beta": "responses_websockets=2026-02-06"},
        "access-token",
        "account-123",
        route=route,
        codex_client=cast(Any, codex_client),
        allow_direct_egress=True,
    )
    message = await websocket.receive()
    await websocket.close()

    assert message.kind == "error"
    assert message.error is not None
    assert "OSError" in message.error
    assert "user:pass" not in message.error
    assert "proxy.local:8080" not in message.error
    assert message.error == "Codex upstream websocket receive failed via proxy endpoint ep_1: OSError"
    assert message.error_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize("with_exception", [False, True], ids=["without-exception", "with-exception"])
async def test_routed_websocket_error_message_defers_ordinary_code_to_relay(with_exception: bool):
    websocket = CodexUpstreamWebSocket(
        _FakeCodexErrorWebSocket(ConnectionResetError("upstream reset") if with_exception else None)
    )

    message = await websocket.receive()

    assert message.kind == "error"
    assert message.error_code is None


@pytest.mark.asyncio
async def test_connect_responses_websocket_uses_all_proxy_fallback(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("socks_proxy", raising=False)
    monkeypatch.delenv("SOCKS_PROXY", raising=False)
    monkeypatch.setenv("all_proxy", "socks5://127.0.0.1:7890")
    monkeypatch.delenv("ALL_PROXY", raising=False)

    await connect_responses_websocket(
        {"openai-beta": "responses_websockets=2026-02-06"},
        "access-token",
        None,
        allow_direct_egress=True,
    )

    kwargs = cast(dict[str, object], seen["kwargs"])
    assert kwargs["proxy"] == "socks5://127.0.0.1:7890"


@pytest.mark.asyncio
async def test_connect_responses_websocket_uses_socks_proxy_before_all_proxy(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("wss_proxy", raising=False)
    monkeypatch.delenv("WSS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.setenv("socks_proxy", "socks5://127.0.0.1:7890")
    monkeypatch.delenv("SOCKS_PROXY", raising=False)
    monkeypatch.setenv("all_proxy", "socks5://127.0.0.1:7891")
    monkeypatch.delenv("ALL_PROXY", raising=False)

    await connect_responses_websocket(
        {"openai-beta": "responses_websockets=2026-02-06"},
        "access-token",
        None,
        allow_direct_egress=True,
    )

    kwargs = cast(dict[str, object], seen["kwargs"])
    assert kwargs["proxy"] == "socks5://127.0.0.1:7890"


@pytest.mark.asyncio
async def test_connect_responses_websocket_uses_socks_proxy_before_https_proxy(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("wss_proxy", raising=False)
    monkeypatch.delenv("WSS_PROXY", raising=False)
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7890")
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.setenv("socks_proxy", "socks5://127.0.0.1:7891")
    monkeypatch.delenv("SOCKS_PROXY", raising=False)
    monkeypatch.setenv("all_proxy", "socks5://127.0.0.1:7892")
    monkeypatch.delenv("ALL_PROXY", raising=False)

    await connect_responses_websocket(
        {"openai-beta": "responses_websockets=2026-02-06"},
        "access-token",
        None,
        allow_direct_egress=True,
    )

    kwargs = cast(dict[str, object], seen["kwargs"])
    assert kwargs["proxy"] == "socks5://127.0.0.1:7891"


@pytest.mark.asyncio
async def test_connect_responses_websocket_normalizes_http_socks_env_proxy(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.setenv("socks_proxy", "http://127.0.0.1:7891")
    monkeypatch.delenv("SOCKS_PROXY", raising=False)

    await connect_responses_websocket(
        {"openai-beta": "responses_websockets=2026-02-06"},
        "access-token",
        None,
        allow_direct_egress=True,
    )

    kwargs = cast(dict[str, object], seen["kwargs"])
    assert kwargs["proxy"] == "socks5h://127.0.0.1:7891"


@pytest.mark.asyncio
async def test_connect_responses_websocket_uses_settings_proxy_env(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    class _Settings(SimpleNamespace):
        def upstream_websocket_proxy_env(self):
            return {"https_proxy": "http://127.0.0.1:7890"}

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: _Settings(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )
    for name in ("https_proxy", "HTTPS_PROXY", "no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)

    await connect_responses_websocket(
        {"openai-beta": "responses_websockets=2026-02-06"},
        "access-token",
        None,
        allow_direct_egress=True,
    )

    kwargs = cast(dict[str, object], seen["kwargs"])
    assert kwargs["proxy"] == "http://127.0.0.1:7890"


@pytest.mark.asyncio
async def test_connect_responses_websocket_respects_settings_no_proxy(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    class _Settings(SimpleNamespace):
        def upstream_websocket_proxy_env(self):
            return {
                "https_proxy": "http://127.0.0.1:7890",
                "no_proxy": "chatgpt.com",
            }

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: _Settings(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )
    for name in ("https_proxy", "HTTPS_PROXY", "no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)

    await connect_responses_websocket(
        {"openai-beta": "responses_websockets=2026-02-06"},
        "access-token",
        None,
        allow_direct_egress=True,
    )

    kwargs = cast(dict[str, object], seen["kwargs"])
    assert kwargs["proxy"] is None


@pytest.mark.asyncio
async def test_connect_responses_websocket_uses_https_proxy_fallback_for_ws(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="http://chatgpt.local/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("ws_proxy", raising=False)
    monkeypatch.delenv("WS_PROXY", raising=False)
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:7889")
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7890")
    monkeypatch.setenv("all_proxy", "socks5://127.0.0.1:7891")

    await connect_responses_websocket(
        {"openai-beta": "responses_websockets=2026-02-06"},
        "access-token",
        None,
        allow_direct_egress=True,
    )

    kwargs = cast(dict[str, object], seen["kwargs"])
    assert seen["url"] == "ws://chatgpt.local/backend-api/codex/responses"
    assert kwargs["proxy"] == "http://127.0.0.1:7890"


@pytest.mark.asyncio
async def test_connect_responses_websocket_traverses_http_proxy_smoke(monkeypatch):
    async def upstream_handler(connection):
        assert await connection.recv() == "hello"
        await connection.send('{"type":"response.completed"}')

    proxy_hits: list[str] = []

    async with websocket_serve(upstream_handler, "127.0.0.1", 0) as upstream_server:
        upstream_socket = next(iter(upstream_server.sockets))
        upstream_port = upstream_socket.getsockname()[1]
        proxy_server = await asyncio.start_server(
            lambda reader, writer: _local_proxy_tunnel_handler(reader, writer, proxy_hits),
            "127.0.0.1",
            0,
        )
        async with proxy_server:
            proxy_port = proxy_server.sockets[0].getsockname()[1]
            monkeypatch.delenv("no_proxy", raising=False)
            monkeypatch.delenv("NO_PROXY", raising=False)
            monkeypatch.delenv("ws_proxy", raising=False)
            monkeypatch.delenv("WS_PROXY", raising=False)
            monkeypatch.delenv("http_proxy", raising=False)
            monkeypatch.delenv("HTTP_PROXY", raising=False)
            monkeypatch.setenv("https_proxy", f"http://127.0.0.1:{proxy_port}")
            monkeypatch.delenv("all_proxy", raising=False)
            monkeypatch.delenv("ALL_PROXY", raising=False)
            monkeypatch.setattr(
                proxy_websocket_module,
                "get_settings",
                lambda: SimpleNamespace(
                    upstream_base_url=f"http://127.0.0.1:{upstream_port}/backend-api",
                    upstream_connect_timeout_seconds=7.0,
                    proxy_downstream_websocket_idle_timeout_seconds=120.0,
                    max_sse_event_bytes=4321,
                    upstream_websocket_trust_env=True,
                ),
            )

            websocket = await connect_responses_websocket(
                {"openai-beta": "responses_websockets=2026-02-06"},
                "access-token",
                None,
                allow_direct_egress=True,
            )
            await websocket.send_text("hello")
            message = await websocket.receive()
            await websocket.close()

    assert message.kind == "text"
    assert message.text == '{"type":"response.completed"}'
    assert proxy_hits == [f"127.0.0.1:{upstream_port}"]


@pytest.mark.asyncio
async def test_connect_responses_websocket_ignores_cgi_http_proxy(monkeypatch):
    fake_connection = _FakeConnection()
    seen: dict[str, object] = {}

    async def fake_websocket_connect(url: str, **kwargs):
        seen["url"] = url
        seen["kwargs"] = kwargs
        return fake_connection

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="http://chatgpt.local/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )
    for name in (
        "no_proxy",
        "NO_PROXY",
        "ws_proxy",
        "WS_PROXY",
        "https_proxy",
        "HTTPS_PROXY",
        "http_proxy",
        "socks_proxy",
        "SOCKS_PROXY",
        "all_proxy",
        "ALL_PROXY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("REQUEST_METHOD", "GET")
    monkeypatch.setenv("HTTP_PROXY", "http://attacker.invalid:8080")

    await connect_responses_websocket(
        {"openai-beta": "responses_websockets=2026-02-06"},
        "access-token",
        None,
        allow_direct_egress=True,
    )

    kwargs = cast(dict[str, object], seen["kwargs"])
    assert seen["url"] == "ws://chatgpt.local/backend-api/codex/responses"
    assert kwargs["proxy"] is None


@pytest.mark.asyncio
async def test_connect_responses_websocket_maps_generic_invalid_handshake(monkeypatch):
    async def fake_websocket_connect(url: str, **kwargs):
        del url, kwargs
        raise InvalidHandshake("proxy CONNECT failed")

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await connect_responses_websocket(
            {"openai-beta": "responses_websockets=2026-02-06"},
            "access-token",
            "account-123",
            allow_direct_egress=True,
        )

    assert exc_info.value.status_code == 502
    assert _proxy_error_code(exc_info.value) == "upstream_unavailable"
    assert _proxy_error_message(exc_info.value) == "proxy CONNECT failed"


@pytest.mark.asyncio
async def test_connect_responses_websocket_maps_invalid_proxy(monkeypatch):
    invalid_proxy = InvalidProxy("http://proxy.invalid", "unsupported proxy scheme")

    async def fake_websocket_connect(url: str, **kwargs):
        del url, kwargs
        raise invalid_proxy

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_base_url="https://chatgpt.com/backend-api",
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await connect_responses_websocket(
            {"openai-beta": "responses_websockets=2026-02-06"},
            "access-token",
            "account-123",
            allow_direct_egress=True,
        )

    assert exc_info.value.status_code == 502
    assert _proxy_error_code(exc_info.value) == "upstream_unavailable"

    assert _proxy_error_message(exc_info.value) == str(invalid_proxy)


@pytest.mark.asyncio
async def test_connect_live_websocket_redacts_invalid_proxy_credentials(monkeypatch):
    async def fake_websocket_connect(url: str, **kwargs):
        del url, kwargs
        raise InvalidProxy(
            runtime_basic_auth_url("proxy-user", "proxy-secret", "proxy.invalid"),
            "unsupported proxy scheme",
        )

    monkeypatch.setattr(proxy_websocket_module, "get_http_client", lambda: _UnexpectedHttpClient(), raising=False)
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", fake_websocket_connect, raising=False)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=True,
        ),
    )

    with pytest.raises(ProxyResponseError) as exc_info:
        await connect_live_websocket(
            "rtc_live",
            {},
            "access-token",
            "account-123",
            protocol=RealtimeWebSocketProtocol.LIVE_V3,
            allow_direct_egress=True,
        )

    assert exc_info.value.status_code == 502
    assert _proxy_error_code(exc_info.value) == "upstream_unavailable"
    message = _proxy_error_message(exc_info.value)
    assert message == "Invalid upstream websocket proxy configuration"
    assert "proxy-user" not in message
    assert "proxy-secret" not in message


def test_responses_websocket_builder_normalizes_non_native_sdk_fingerprint():
    # Regression for the Codex P2 finding: the client-facing /v1/responses
    # websocket egress builder must normalize a non-native SDK fingerprint, not
    # just the internal auto-transport builder, or a direct websocket SDK caller
    # bypasses the priority-downgrade mitigation.
    from unittest.mock import patch

    from app.core.clients import proxy as proxy_module
    from app.core.clients.proxy_websocket import _build_upstream_websocket_headers

    inbound = {
        "User-Agent": "OpenAI/Python 2.24.0",
        "x-openai-client-version": "2.24.0",
        "x-stainless-os": "MacOS",
        "originator": "sdk",
        "Version": "9.9.9",
        "openai-beta": "responses_websockets=2026-02-06",
    }
    with patch.object(proxy_module.get_codex_version_cache(), "cached_version_or_default", return_value="0.142.0"):
        headers = _build_upstream_websocket_headers(inbound, "tok", "acct-1")

    assert headers["User-Agent"] == "codex_cli_rs/0.142.0 (Mac OS 26.5.0; arm64) iTerm.app/3.6.10"
    lowered = {key.lower() for key in headers}
    assert "x-openai-client-version" not in lowered
    assert not any(key.lower().startswith("x-stainless-") for key in headers)
    assert headers["originator"] == "codex_cli_rs"
    assert headers["version"] == "0.142.0"
    assert "Version" not in headers
    assert headers["ChatGPT-Account-Id"] == "acct-1"
    assert "chatgpt-account-id" not in headers
    # The responses websocket beta header is still appended.
    assert "responses_websockets=2026-02-06" in headers["openai-beta"]


def test_responses_websocket_builder_omits_hop_local_routing_hint():
    from app.core.clients.proxy_websocket import _build_upstream_websocket_headers

    headers = _build_upstream_websocket_headers(
        {
            "User-Agent": "codex_exec/0.150.1 (Ubuntu 24.4.0; x86_64) dumb (codex_exec; 0.150.1)",
            "X-Codex-Routing-Hint": "model=gpt-5.6-luna",
        },
        "tok",
        "acct-1",
    )

    assert "x-codex-routing-hint" not in {key.lower() for key in headers}


def test_responses_websocket_builder_strips_internal_responses_lite_header():
    from app.core.clients.proxy_websocket import _build_upstream_websocket_headers

    inbound = {
        "User-Agent": "codex_cli_rs/0.142.0 (Mac OS 27.0.0; arm64) iTerm.app/3.6.10",
        "X-OpenAI-Internal-Codex-Responses-Lite": "1",
        "openai-beta": "responses_websockets=2026-02-06",
    }
    headers = _build_upstream_websocket_headers(inbound, "tok", "acct-1")
    lowered = {key.lower() for key in headers}

    assert "x-openai-internal-codex-responses-lite" not in lowered
    assert "responses_websockets=2026-02-06" in headers["openai-beta"]


def test_responses_websocket_builder_leaves_native_codex_unchanged():
    from app.core.clients.proxy_websocket import _build_upstream_websocket_headers

    native_ua = "codex_cli_rs/0.142.0 (Mac OS 27.0.0; arm64) iTerm.app/3.6.10"
    inbound = {"User-Agent": native_ua, "openai-beta": "responses_websockets=2026-02-06"}
    headers = _build_upstream_websocket_headers(inbound, "tok", "acct-1")

    assert headers["User-Agent"] == native_ua
    assert headers["chatgpt-account-id"] == "acct-1"
    assert "ChatGPT-Account-Id" not in headers


@pytest.fixture
def live_websocket_connect(monkeypatch):
    connector = AsyncMock(return_value=_FakeConnection())
    monkeypatch.setattr(proxy_websocket_module, "websocket_connect", connector)
    monkeypatch.setattr(
        proxy_websocket_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstream_connect_timeout_seconds=7.0,
            proxy_downstream_websocket_idle_timeout_seconds=120.0,
            max_sse_event_bytes=4321,
            upstream_websocket_trust_env=False,
        ),
    )
    return connector


@pytest.mark.asyncio
async def test_live_connector_uses_frameless_url(live_websocket_connect) -> None:
    live_websocket_connect.return_value = _FakeConnection(subprotocol="live.v1")
    offered_subprotocols = ("live.v0", "live.v1")
    websocket = await proxy_websocket_module.connect_live_websocket(
        "rtc_example",
        {
            "OpenAI-Alpha": "quicksilver=v2",
            "Sec-WebSocket-Protocol": "raw-header-must-not-be-forwarded",
        },
        "access-token",
        "account-a",
        protocol=proxy_websocket_module.RealtimeWebSocketProtocol.LIVE_V3,
        query_params=[("intent", "quicksilver"), ("architecture", "avas")],
        subprotocols=offered_subprotocols,
        allow_direct_egress=True,
    )
    await websocket.close()

    assert (
        live_websocket_connect.await_args.args[0]
        == "wss://api.openai.com/v1/live/rtc_example?intent=quicksilver&architecture=avas"
    )
    assert live_websocket_connect.await_args.kwargs["subprotocols"] is offered_subprotocols
    additional_headers = live_websocket_connect.await_args.kwargs["additional_headers"]
    assert not any(key.lower() == "sec-websocket-protocol" for key in additional_headers)
    assert websocket.response_header("sec-websocket-protocol") == "live.v1"


@pytest.mark.asyncio
async def test_live_connector_uses_legacy_realtime_url_with_one_ordered_call_id(
    live_websocket_connect,
) -> None:
    websocket = await proxy_websocket_module.connect_live_websocket(
        "rtc_example",
        {},
        "access-token",
        "account-a",
        protocol=proxy_websocket_module.RealtimeWebSocketProtocol.REALTIME_V1_V2,
        query_params=[("intent", "quicksilver"), ("architecture", "avas")],
        base_url="https://api.openai.com/v1?configured=one",
        allow_direct_egress=True,
    )
    await websocket.close()

    assert (
        live_websocket_connect.await_args.args[0]
        == "wss://api.openai.com/v1/realtime?configured=one&intent=quicksilver&architecture=avas&call_id=rtc_example"
    )


@pytest.mark.asyncio
async def test_live_connector_rejects_duplicate_legacy_call_id(live_websocket_connect) -> None:
    with pytest.raises(ValueError, match="must not include call_id"):
        await proxy_websocket_module.connect_live_websocket(
            "rtc_example",
            {},
            "access-token",
            "account-a",
            protocol=proxy_websocket_module.RealtimeWebSocketProtocol.REALTIME_V1_V2,
            query_params=[("call_id", "rtc_duplicate"), ("intent", "quicksilver")],
            allow_direct_egress=True,
        )

    live_websocket_connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_v3_connector_rejects_query_call_id_before_connect(live_websocket_connect) -> None:
    with pytest.raises(ValueError, match="must not include call_id"):
        await proxy_websocket_module.connect_live_websocket(
            "rtc_a",
            {},
            "access-token",
            "account-a",
            protocol=proxy_websocket_module.RealtimeWebSocketProtocol.LIVE_V3,
            query_params=[("intent", "quicksilver"), ("call_id", "rtc_b")],
            allow_direct_egress=True,
        )

    live_websocket_connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_connector_replaces_identity_and_preserves_frameless_metadata(
    live_websocket_connect,
) -> None:
    websocket = await proxy_websocket_module.connect_live_websocket(
        "rtc_example",
        {
            "Authorization": "Bearer codex-lb-key",
            "ChatGPT-Account-ID": "wrong-account",
            "User-Agent": "frameless-desktop/1.0",
            "OpenAI-Alpha": "quicksilver=v2",
            "OpenAI-Beta": "realtime=v1, responses=experimental, responses_websockets=2026-07-01",
            "x-oai-attestation": "attestation",
            "x-session-id": "session-a",
            "session-id": "session-b",
            "thread-id": "thread-a",
            "originator": "frameless_desktop",
            "X-OpenAI-Fedramp": "true",
            "x-openai-internal-codex-residency": "us",
            "Sec-WebSocket-Key": "must-not-forward",
        },
        "account-token",
        "account-a",
        protocol=proxy_websocket_module.RealtimeWebSocketProtocol.LIVE_V3,
        allow_direct_egress=True,
    )
    await websocket.close()
    first_call = live_websocket_connect.await_args_list[0]
    lowered = {key.lower(): value for key, value in first_call.kwargs["additional_headers"].items()}

    assert lowered["authorization"] == "Bearer account-token"
    assert lowered["chatgpt-account-id"] == "account-a"
    assert lowered["openai-alpha"] == "quicksilver=v2"
    assert lowered["x-oai-attestation"] == "attestation"
    assert lowered["x-session-id"] == "session-a"
    assert lowered["session-id"] == "session-b"
    assert lowered["thread-id"] == "thread-a"
    assert lowered["originator"] == "frameless_desktop"
    assert lowered["x-openai-fedramp"] == "true"
    assert lowered["x-openai-internal-codex-residency"] == "us"
    assert lowered["openai-beta"] == "realtime=v1"
    assert "sec-websocket-key" not in lowered
    assert "codex-lb-key" not in str(first_call)
    assert first_call.kwargs["user_agent_header"] == "frameless-desktop/1.0"

    responses_only_websocket = await proxy_websocket_module.connect_live_websocket(
        "rtc_example",
        {"OpenAI-Beta": "responses=experimental, responses_websockets=2026-07-01"},
        "account-token",
        "account-a",
        protocol=proxy_websocket_module.RealtimeWebSocketProtocol.LIVE_V3,
        allow_direct_egress=True,
    )
    await responses_only_websocket.close()
    responses_only_headers = live_websocket_connect.await_args_list[1].kwargs["additional_headers"]
    assert "openai-beta" not in {key.lower() for key in responses_only_headers}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "supplied_alpha",
    ["quicksilver=v1", None, "quicksilver=v2"],
    ids=["v1", "v2", "v3"],
)
async def test_live_connector_preserves_version_specific_alpha_without_synthesis(
    live_websocket_connect,
    supplied_alpha: str | None,
) -> None:
    inbound = {"OpenAI-Alpha": supplied_alpha} if supplied_alpha is not None else {}

    websocket = await proxy_websocket_module.connect_live_websocket(
        "rtc_example",
        inbound,
        "account-token",
        "account-a",
        protocol=proxy_websocket_module.RealtimeWebSocketProtocol.LIVE_V3,
        allow_direct_egress=True,
    )
    await websocket.close()
    headers = live_websocket_connect.await_args.kwargs["additional_headers"]
    lowered = {key.lower(): value for key, value in headers.items()}

    assert lowered.get("openai-alpha") == supplied_alpha
    assert "openai-beta" not in lowered
    assert "sec-websocket-protocol" not in lowered

from __future__ import annotations

from typing import Any, cast

import pytest
from curl_cffi.const import CurlWsFlag

import app.core.clients.proxy as proxy_module
from app.core.clients.codex import CodexTransportError, CodexWebSocketResult
from app.core.clients.files import create_file, finalize_file
from app.core.clients.proxy import (
    ProxyResponseError,
    UpstreamProxyRouteTrace,
    codex_control_request,
    compact_responses,
    stream_responses,
    thread_goal_request,
    transcribe_audio,
)
from app.core.clients.proxy_websocket import connect_responses_websocket
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.upstream_proxy import ResolvedProxyEndpoint, ResolvedUpstreamRoute

pytestmark = pytest.mark.unit


class _CodexClient:
    def __init__(self, response: object | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response = response or _Response()

    async def request(self, method: str, url: str, *, route: ResolvedUpstreamRoute, **kwargs: Any) -> object:
        self.calls.append({"method": method, "url": url, "route": route, **kwargs})
        return self.response


class _FailingRouteMetadataCodexClient:
    async def request_with_route_metadata(
        self,
        method: str,
        url: str,
        *,
        route: ResolvedUpstreamRoute,
        **kwargs: Any,
    ) -> object:
        del method, url, route, kwargs
        raise RuntimeError("proxy http://user:pass@proxy.test:8080 connect failed")


class _Response:
    status_code = 200
    headers = {"content-type": "application/json"}
    content = b'{"ok": true}'

    def json(self) -> dict[str, bool]:
        return {"ok": True}


class _CompactResponse:
    status_code = 200
    headers = {"content-type": "application/json"}
    content = b'{"object": "response.compact", "id": "compact_1"}'

    def json(self) -> dict[str, str]:
        return {"object": "response.compact", "id": "compact_1"}


class _TranscribeResponse:
    status_code = 200
    headers = {"content-type": "application/json"}
    content = b'{"text": "hello"}'

    def json(self) -> dict[str, str]:
        return {"text": "hello"}


class _FileResponse:
    status_code = 200
    headers = {"content-type": "application/json"}
    content = b'{"file_id": "file_1", "status": "success"}'
    text = '{"file_id": "file_1", "status": "success"}'


class _StreamResponse:
    status_code = 200
    headers = {"content-type": "text/event-stream"}

    async def aiter_content(self):
        yield b'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n'


class _StreamErrorResponse:
    status_code = 200
    headers = {"content-type": "text/event-stream"}

    async def aiter_content(self):
        raise OSError("proxy http://user:pass@proxy.test:8080 read failed")
        yield b""


class _TransportErrorCodexClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def request(self, method: str, url: str, *, route: ResolvedUpstreamRoute, **kwargs: Any) -> object:
        self.calls.append({"method": method, "url": url, "route": route, **kwargs})
        raise CodexTransportError("Codex upstream request failed via proxy endpoint ep_1: OSError")


class _FakeCodexWebSocket:
    def __init__(self, *, fail_receive: bool = False, fail_send: bool = False) -> None:
        self.sent: list[str | bytes] = []
        self.closed = False
        self.fail_receive = fail_receive
        self.fail_send = fail_send

    def send_str(self, payload: str) -> None:
        if self.fail_send:
            raise OSError("proxy http://user:pass@proxy.test:8080 send failed")
        self.sent.append(payload)

    def send_bytes(self, payload: bytes) -> None:
        if self.fail_send:
            raise OSError("proxy http://user:pass@proxy.test:8080 send failed")
        self.sent.append(payload)

    async def recv(self) -> tuple[bytes, int]:
        if self.fail_receive:
            raise OSError("proxy http://user:pass@proxy.test:8080 websocket failed")
        return b'{"type":"response.completed"}', int(CurlWsFlag.TEXT)

    def close(self) -> None:
        self.closed = True


class _FakeWsContext:
    def __init__(self, websocket: _FakeCodexWebSocket) -> None:
        self.websocket = websocket
        self.exited = False

    async def __aenter__(self) -> _FakeCodexWebSocket:
        return self.websocket

    async def __aexit__(self, *args: object) -> None:
        self.exited = True


class _WsCodexClient:
    def __init__(self, *, fail_receive: bool = False, fail_send: bool = False) -> None:
        self.websocket = _FakeCodexWebSocket(fail_receive=fail_receive, fail_send=fail_send)
        self.context = _FakeWsContext(self.websocket)
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    async def ws_connect(self, url: str, *, route: ResolvedUpstreamRoute, **kwargs: Any) -> _FakeWsContext:
        self.calls.append({"url": url, "route": route, **kwargs})
        return self.context

    async def close(self) -> None:
        self.closed = True


class _RawWsCodexClient:
    def __init__(self) -> None:
        self.websocket = _FakeCodexWebSocket()
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    async def open_ws_with_route_metadata(
        self,
        url: str,
        *,
        route: ResolvedUpstreamRoute,
        **kwargs: Any,
    ) -> CodexWebSocketResult:
        self.calls.append({"url": url, "route": route, **kwargs})
        return CodexWebSocketResult(
            websocket=self.websocket,
            context=None,
            route=route,
            fallback_used=False,
        )

    async def close(self) -> None:
        self.closed = True


class _AutoFallbackCodexClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def open_ws_with_route_metadata(self, url: str, *, route: ResolvedUpstreamRoute, **kwargs: Any) -> object:
        self.calls.append({"transport": "websocket", "url": url, "route": route, **kwargs})
        raise CodexTransportError("websocket handshake rejected", status_code=426)

    async def request(self, method: str, url: str, *, route: ResolvedUpstreamRoute, **kwargs: Any) -> object:
        self.calls.append({"transport": "http", "method": method, "url": url, "route": route, **kwargs})
        return _StreamResponse()


@pytest.fixture
def route() -> ResolvedUpstreamRoute:
    return ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080),
    )


@pytest.mark.asyncio
async def test_thread_goal_request_uses_codex_client_when_route_is_resolved(route: ResolvedUpstreamRoute) -> None:
    client = _CodexClient()
    trace = UpstreamProxyRouteTrace()

    result = await thread_goal_request(
        "get",
        {"thread_id": "thread_1"},
        {"user-agent": "codex"},
        "access",
        "chatgpt_account",
        base_url="https://chatgpt.test",
        route=route,
        codex_client=cast(Any, client),
        route_trace=trace,
    )

    assert result == {"ok": True}
    assert client.calls[0]["url"] == "https://chatgpt.test/codex/thread/goal/get"
    assert client.calls[0]["route"] is route
    assert trace.endpoint_id == "ep_1"


@pytest.mark.asyncio
async def test_codex_control_request_uses_codex_client_when_route_is_resolved(route: ResolvedUpstreamRoute) -> None:
    client = _CodexClient()
    trace = UpstreamProxyRouteTrace()

    response = await codex_control_request(
        "sessions",
        method="GET",
        payload=None,
        query_params={"limit": "1"},
        headers={"accept": "application/json"},
        access_token="access",
        account_id="chatgpt_account",
        base_url="https://chatgpt.test",
        route=route,
        codex_client=cast(Any, client),
        route_trace=trace,
    )

    assert response.status_code == 200
    assert response.body == b'{"ok": true}'
    assert client.calls[0]["url"] == "https://chatgpt.test/codex/sessions"
    assert client.calls[0]["route"] is route
    assert trace.endpoint_id == "ep_1"


@pytest.mark.asyncio
async def test_compact_responses_uses_codex_client_when_route_is_resolved(route: ResolvedUpstreamRoute) -> None:
    client = _CodexClient(_CompactResponse())
    trace = UpstreamProxyRouteTrace()
    payload = ResponsesCompactRequest(model="gpt-5.2", instructions="Summarize.", input="hello")

    response = await compact_responses(
        payload,
        {"user-agent": "codex"},
        "access",
        "chatgpt_account",
        session=cast(Any, object()),
        route=route,
        codex_client=cast(Any, client),
        route_trace=trace,
    )

    assert response.object == "response.compact"
    assert response.id == "compact_1"
    assert client.calls[0]["url"].endswith("/backend-api/codex/responses/compact")
    assert client.calls[0]["route"] is route
    assert client.calls[0]["json"]["model"] == "gpt-5.2"
    assert trace.endpoint_id == "ep_1"


@pytest.mark.asyncio
async def test_transcribe_audio_uses_codex_client_when_route_is_resolved(route: ResolvedUpstreamRoute) -> None:
    client = _CodexClient(_TranscribeResponse())
    trace = UpstreamProxyRouteTrace()

    response = await transcribe_audio(
        b"audio",
        filename="sample.wav",
        content_type="audio/wav",
        prompt="say hello",
        headers={"user-agent": "codex"},
        access_token="access",
        account_id="chatgpt_account",
        session=cast(Any, object()),
        route=route,
        codex_client=cast(Any, client),
        route_trace=trace,
    )

    assert response == {"text": "hello"}
    assert client.calls[0]["url"].endswith("/backend-api/transcribe")
    assert client.calls[0]["route"] is route
    assert client.calls[0]["files"]["file"] == ("sample.wav", b"audio", "audio/wav")
    assert client.calls[0]["data"] == {"prompt": "say hello"}
    assert trace.endpoint_id == "ep_1"


@pytest.mark.asyncio
async def test_transcribe_audio_route_transport_errors_do_not_expose_proxy_credentials(
    route: ResolvedUpstreamRoute,
) -> None:
    with pytest.raises(ProxyResponseError) as exc_info:
        await transcribe_audio(
            b"audio",
            filename="sample.wav",
            content_type="audio/wav",
            prompt=None,
            headers={"user-agent": "codex"},
            access_token="access",
            account_id="chatgpt_account",
            session=cast(Any, object()),
            route=route,
            codex_client=cast(Any, _FailingRouteMetadataCodexClient()),
        )

    exc = exc_info.value
    assert exc.status_code == 502
    error = exc.payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "upstream_unavailable"
    message = str(error["message"])
    assert "ep_1" in message
    assert "RuntimeError" in message
    assert "user:pass" not in message
    assert "proxy.test:8080" not in message


@pytest.mark.asyncio
async def test_file_create_and_finalize_use_codex_client_when_route_is_resolved(route: ResolvedUpstreamRoute) -> None:
    client = _CodexClient(_FileResponse())

    created = await create_file(
        payload={"file_name": "a.txt", "file_size": 3, "use_case": "codex"},
        headers={"user-agent": "codex"},
        access_token="access",
        account_id="chatgpt_account",
        base_url="https://chatgpt.test/backend-api",
        route=route,
        codex_client=cast(Any, client),
    )
    finalized = await finalize_file(
        file_id="file_1",
        headers={"user-agent": "codex"},
        access_token="access",
        account_id="chatgpt_account",
        base_url="https://chatgpt.test/backend-api",
        route=route,
        codex_client=cast(Any, client),
    )

    assert created["file_id"] == "file_1"
    assert finalized["status"] == "success"
    assert [call["url"] for call in client.calls] == [
        "https://chatgpt.test/backend-api/files",
        "https://chatgpt.test/backend-api/files/file_1/uploaded",
    ]
    assert all(call["route"] is route for call in client.calls)


@pytest.mark.asyncio
async def test_stream_responses_uses_codex_client_when_route_is_resolved(route: ResolvedUpstreamRoute) -> None:
    client = _CodexClient(_StreamResponse())
    trace = UpstreamProxyRouteTrace()
    payload = ResponsesRequest(model="gpt-5.2", instructions="Reply.", input="hello", stream=True)

    events = [
        event
        async for event in stream_responses(
            payload,
            {"user-agent": "codex"},
            "access",
            "chatgpt_account",
            session=cast(Any, object()),
            upstream_stream_transport_override="http",
            route=route,
            codex_client=cast(Any, client),
            route_trace=trace,
        )
    ]

    assert events == ['data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n']
    assert client.calls[0]["url"].endswith("/backend-api/codex/responses")
    assert client.calls[0]["stream"] is True
    assert trace.endpoint_id == "ep_1"


@pytest.mark.asyncio
async def test_stream_responses_websocket_transport_uses_codex_client_when_route_is_resolved(
    route: ResolvedUpstreamRoute,
) -> None:
    client = _WsCodexClient()
    trace = UpstreamProxyRouteTrace()
    payload = ResponsesRequest(model="gpt-5.2", instructions="Reply.", input="hello", stream=True)

    events = [
        event
        async for event in stream_responses(
            payload,
            {"user-agent": "codex"},
            "access",
            "chatgpt_account",
            session=cast(Any, object()),
            upstream_stream_transport_override="websocket",
            route=route,
            codex_client=cast(Any, client),
            route_trace=trace,
        )
    ]

    assert events == ['event: response.completed\ndata: {"type":"response.completed"}\n\n']
    assert client.calls[0]["url"].endswith("/backend-api/codex/responses")
    assert client.calls[0]["url"].startswith("wss://")
    assert client.calls[0]["route"] is route
    assert '"type":"response.create"' in str(client.websocket.sent[0])
    assert trace.endpoint_id == "ep_1"


@pytest.mark.asyncio
async def test_stream_responses_routed_websocket_closes_raw_result_without_context(
    route: ResolvedUpstreamRoute,
) -> None:
    client = _RawWsCodexClient()
    trace = UpstreamProxyRouteTrace()
    payload = ResponsesRequest(model="gpt-5.2", instructions="Reply.", input="hello", stream=True)

    events = [
        event
        async for event in stream_responses(
            payload,
            {"user-agent": "codex"},
            "access",
            "chatgpt_account",
            session=cast(Any, object()),
            upstream_stream_transport_override="websocket",
            route=route,
            codex_client=cast(Any, client),
            route_trace=trace,
        )
    ]

    assert events == ['event: response.completed\ndata: {"type":"response.completed"}\n\n']
    assert client.calls[0]["url"].endswith("/backend-api/codex/responses")
    assert client.websocket.closed is True
    assert client.closed is False
    assert trace.endpoint_id == "ep_1"


@pytest.mark.asyncio
async def test_stream_responses_routed_auto_websocket_426_falls_back_to_http(
    route: ResolvedUpstreamRoute,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _AutoFallbackCodexClient()
    payload = ResponsesRequest(model="gpt-5.2", instructions="Reply.", input="hello", stream=True)

    monkeypatch.setattr(
        proxy_module,
        "get_model_registry",
        lambda: type(
            "_Registry",
            (),
            {"prefers_websockets": lambda self, model: True},
        )(),
    )

    events = [
        event
        async for event in stream_responses(
            payload,
            {"user-agent": "codex"},
            "access",
            "chatgpt_account",
            session=cast(Any, object()),
            upstream_stream_transport_override="auto",
            route=route,
            codex_client=cast(Any, client),
        )
    ]

    assert events == ['data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n']
    assert [call["transport"] for call in client.calls] == ["websocket", "http"]
    assert client.calls[1]["method"] == "POST"


@pytest.mark.asyncio
async def test_stream_responses_route_errors_do_not_expose_proxy_credentials(route: ResolvedUpstreamRoute) -> None:
    client = _CodexClient(_StreamErrorResponse())
    payload = ResponsesRequest(model="gpt-5.2", instructions="Reply.", input="hello", stream=True)

    events = [
        event
        async for event in stream_responses(
            payload,
            {"user-agent": "codex"},
            "access",
            "chatgpt_account",
            session=cast(Any, object()),
            upstream_stream_transport_override="http",
            route=route,
            codex_client=cast(Any, client),
        )
    ]

    combined = "".join(events)
    assert "ep_1" in combined
    assert "OSError" in combined
    assert "user:pass" not in combined
    assert "proxy.test:8080" not in combined


@pytest.mark.asyncio
async def test_stream_responses_routed_transport_errors_are_unavailable(route: ResolvedUpstreamRoute) -> None:
    client = _TransportErrorCodexClient()
    payload = ResponsesRequest(model="gpt-5.2", instructions="Reply.", input="hello", stream=True)

    events = [
        event
        async for event in stream_responses(
            payload,
            {"user-agent": "codex"},
            "access",
            "chatgpt_account",
            session=cast(Any, object()),
            upstream_stream_transport_override="http",
            route=route,
            codex_client=cast(Any, client),
        )
    ]

    combined = "".join(events)
    assert '"code":"upstream_unavailable"' in combined
    assert "ep_1" in combined


@pytest.mark.asyncio
async def test_responses_websocket_uses_codex_client_when_route_is_resolved(route: ResolvedUpstreamRoute) -> None:
    client = _WsCodexClient()

    websocket = await connect_responses_websocket(
        {"user-agent": "codex", "Origin": "https://chatgpt.test"},
        "access",
        "chatgpt_account",
        base_url="https://chatgpt.test/backend-api",
        route=route,
        codex_client=cast(Any, client),
    )

    await websocket.send_text('{"type":"response.create"}')
    message = await websocket.receive()
    await websocket.close()

    assert message.kind == "text"
    assert message.text == '{"type":"response.completed"}'
    assert client.calls[0]["url"] == "wss://chatgpt.test/backend-api/codex/responses"
    assert client.calls[0]["route"] is route
    assert client.calls[0]["headers"]["user-agent"] == "codex"
    assert client.calls[0]["headers"]["Origin"] == "https://chatgpt.test"
    assert client.websocket.sent == ['{"type":"response.create"}']
    assert client.context.exited is True
    assert client.closed is False


@pytest.mark.asyncio
async def test_responses_websocket_receive_errors_do_not_expose_proxy_credentials(
    route: ResolvedUpstreamRoute,
) -> None:
    client = _WsCodexClient(fail_receive=True)

    websocket = await connect_responses_websocket(
        {"user-agent": "codex"},
        "access",
        "chatgpt_account",
        base_url="https://chatgpt.test/backend-api",
        route=route,
        codex_client=cast(Any, client),
    )

    message = await websocket.receive()
    await websocket.close()

    assert message.kind == "error"
    assert message.error is not None
    assert "ep_1" in message.error
    assert "OSError" in message.error
    assert "user:pass" not in message.error
    assert "proxy.test:8080" not in message.error


@pytest.mark.asyncio
async def test_responses_websocket_send_errors_do_not_expose_proxy_credentials(
    route: ResolvedUpstreamRoute,
) -> None:
    client = _WsCodexClient(fail_send=True)

    websocket = await connect_responses_websocket(
        {"user-agent": "codex"},
        "access",
        "chatgpt_account",
        base_url="https://chatgpt.test/backend-api",
        route=route,
        codex_client=cast(Any, client),
    )

    with pytest.raises(RuntimeError) as exc_info:
        await websocket.send_text('{"type":"response.create"}')
    await websocket.close()

    message = str(exc_info.value)
    assert "ep_1" in message
    assert "OSError" in message
    assert "user:pass" not in message
    assert "proxy.test:8080" not in message

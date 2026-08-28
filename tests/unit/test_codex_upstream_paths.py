from __future__ import annotations

import errno
import socket
from typing import Any, cast
from unittest.mock import AsyncMock

import aiohttp
import pytest
from aiohttp.client_reqrep import ConnectionKey

import app.core.clients.proxy as proxy_module
from app.core.clients.codex import CodexClient, CodexRequestResult, CodexTransportError, CodexWebSocketResult
from app.core.clients.files import create_file, finalize_file
from app.core.clients.proxy import (
    ProxyResponseError,
    UpstreamProxyRouteTrace,
    codex_control_request,
    compact_responses,
    is_confirmed_pre_dispatch_transport_error,
    stream_responses,
    thread_goal_request,
    transcribe_audio,
)
from app.core.clients.proxy_websocket import UpstreamWebSocketTransportError, connect_responses_websocket
from app.core.openai.requests import ResponsesCompactRequest, ResponsesRequest
from app.core.upstream_proxy import ResolvedProxyEndpoint, ResolvedUpstreamRoute
from tests.unit._proxy_test_helpers import runtime_basic_auth_url

pytestmark = pytest.mark.unit


class _CodexClient:
    def __init__(self, response: object | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.response = response or _Response()

    async def request(self, method: str, url: str, *, route: ResolvedUpstreamRoute, **kwargs: Any) -> object:
        self.calls.append({"method": method, "url": url, "route": route, **kwargs})
        return self.response


class _RouteMetadataCodexClient(_CodexClient):
    async def request_with_route_metadata(
        self,
        method: str,
        url: str,
        *,
        route: ResolvedUpstreamRoute,
        **kwargs: Any,
    ) -> CodexRequestResult:
        self.calls.append({"method": method, "url": url, "route": route, **kwargs})
        return CodexRequestResult(response=self.response, route=route, fallback_used=False)


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
        raise RuntimeError("proxy " + runtime_basic_auth_url("user", "pass", "proxy.test:8080") + " connect failed")


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


class _CompactStreamContent:
    async def iter_chunked(self, size: int):
        del size
        yield (
            b'data: {"type":"response.output_item.done","output_index":0,'
            b'"item":{"id":"msg_compact_1","type":"message","role":"assistant",'
            b'"status":"completed","content":[{"type":"output_text","text":"enc_compact_1"}]}}\n\n'
            b'data: {"type":"response.completed","response":'
            b'{"object":"response","id":"resp_compact_1","status":"completed","output":[]}}\n\n'
        )


class _CompactStreamWithoutOutputIndexContent:
    async def iter_chunked(self, size: int):
        del size
        yield (
            b'data: {"type":"response.output_item.done",'
            b'"item":{"id":"msg_compact_without_index","type":"message",'
            b'"status":"completed","content":[{"type":"output_text",'
            b'"text":"enc_compact_without_index"}]}}\n\n'
            b'data: {"type":"response.completed","response":'
            b'{"object":"response","id":"resp_compact_without_index",'
            b'"status":"completed","output":[]}}\n\n'
        )


class _CompactStreamResponse:
    status_code = 200
    headers: dict[str, str] = {}
    content = _CompactStreamContent()


class _CompactStreamWithoutOutputIndexResponse:
    status_code = 200
    headers: dict[str, str] = {}
    content = _CompactStreamWithoutOutputIndexContent()


class _BufferedCompactStreamResponse:
    status = 200
    status_code = 200
    headers = {"content-type": "text/event-stream"}
    content = (
        b'data: {"type":"response.completed","response":{"object":"response","id":"resp_compact_buffered",'
        b'"status":"completed","service_tier":"default","output":['
        b'{"id":"msg_history","type":"message","role":"assistant","status":"completed",'
        b'"content":[{"type":"output_text","text":"historical plaintext"}]},'
        b'{"id":"cmp_buffered","type":"compaction_summary","encrypted_content":"enc_buffered"}]}}\n\n'
    )


class _BufferedStrCompactStreamResponse:
    status = 200
    status_code = 200
    headers = {"content-type": "text/event-stream"}
    content = (
        'data: {"type":"response.completed","response":{"object":"response","id":"resp_compact_str",'
        '"status":"completed","output":['
        '{"id":"cmp_str","type":"compaction_summary","encrypted_content":"enc_str"}]}}\n\n'
    )


class _BufferedMessageOnlyCompactStreamResponse:
    status = 200
    status_code = 200
    headers = {"content-type": "text/event-stream"}
    content = (
        b'data: {"type":"response.completed","response":{"object":"response","id":"resp_compact_messages",'
        b'"status":"completed","output":['
        b'{"id":"msg_history","type":"message","role":"assistant","status":"completed",'
        b'"content":[{"type":"output_text","text":"historical plaintext"}]},'
        b'{"id":"msg_summary","type":"message","role":"assistant","status":"completed",'
        b'"content":[{"type":"output_text","text":"enc_summary"}]}]}}\n\n'
    )


class _CompactTerminalErrorStreamResponse:
    status = 200
    status_code = 200
    headers = {"content-type": "text/event-stream"}

    def __init__(self, error_type: str, error_code: str) -> None:
        self.content = (
            b'data: {"type":"error","error_type":"'
            + error_type.encode("utf-8")
            + b'","code":"'
            + error_code.encode("utf-8")
            + b'","message":"compact rejected","param":"previous_response_id"}\n\n'
        )


class _CompactTerminalFailedStreamResponse:
    status = 200
    status_code = 200
    headers = {"content-type": "text/event-stream"}
    content = (
        b'data: {"type":"response.failed","response":{"status_code":400,'
        b'"error":{"code":"previous_response_not_found","message":"missing anchor",'
        b'"type":"invalid_request_error","param":"previous_response_id"}}}\n\n'
    )


class _CompactTerminalFailedStreamWithoutStatusResponse:
    status = 200
    status_code = 200
    headers = {"content-type": "text/event-stream"}

    def __init__(self, error_type: str, error_code: str) -> None:
        self.content = (
            b'data: {"type":"response.failed","response":{"status":"failed","error":{"code":"'
            + error_code.encode("utf-8")
            + b'","message":"mapped from error detail","type":"'
            + error_type.encode("utf-8")
            + b'"}}}\n\n'
        )


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


class _FakeStreamContent:
    async def iter_chunked(self, size: int):
        yield b'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n'


class _StreamResponse:
    status_code = 200
    headers = {"content-type": "text/event-stream"}
    content = _FakeStreamContent()


class _FakeStreamErrorContent:
    async def iter_chunked(self, size: int):
        raise OSError("proxy " + runtime_basic_auth_url("user", "***", "proxy.test:8080") + " read failed")
        yield b""


class _StreamErrorResponse:
    status_code = 200
    headers = {"content-type": "text/event-stream"}
    content = _FakeStreamErrorContent()


class _BufferedBodyNetworkFailureResponse:
    status_code = 200
    headers = {"content-type": "application/json"}

    async def read(self) -> bytes:
        raise OSError(errno.ENETUNREACH, "Network is unreachable")


class _TransportErrorCodexClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def request(self, method: str, url: str, *, route: ResolvedUpstreamRoute, **kwargs: Any) -> object:
        self.calls.append({"method": method, "url": url, "route": route, **kwargs})
        raise CodexTransportError("Codex upstream request failed via proxy endpoint ep_1: OSError")


class _NetworkFailureSession:
    def __init__(self, error: OSError) -> None:
        self.error = error

    async def request(self, method: str, url: str, **kwargs: Any) -> object:
        del method, url, kwargs
        raise self.error

    async def ws_connect(self, url: str, **kwargs: Any) -> object:
        del url, kwargs
        raise self.error


class _ResponseSession:
    def __init__(self, response: object) -> None:
        self.response = response

    async def request(self, method: str, url: str, **kwargs: Any) -> object:
        del method, url, kwargs
        return self.response


class _FakeCodexWebSocket:
    def __init__(self, *, fail_receive: bool = False, fail_send: bool = False) -> None:
        self.sent: list[str | bytes] = []
        self.closed = False
        self.fail_receive = fail_receive
        self.fail_send = fail_send

    def send_str(self, payload: str) -> None:
        if self.fail_send:
            raise OSError("proxy " + runtime_basic_auth_url("user", "pass", "proxy.test:8080") + " send failed")
        self.sent.append(payload)

    def send_bytes(self, payload: bytes) -> None:
        if self.fail_send:
            raise OSError("proxy " + runtime_basic_auth_url("user", "pass", "proxy.test:8080") + " send failed")
        self.sent.append(payload)

    async def receive(self) -> aiohttp.WSMessage:
        if self.fail_receive:
            raise OSError("proxy " + runtime_basic_auth_url("user", "***", "proxy.test:8080") + " websocket failed")
        return aiohttp.WSMessage(aiohttp.WSMsgType.TEXT, '{"type":"response.completed"}', None)

    def close(self, *, code: int = 1000, message: bytes = b"") -> None:
        del code, message
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
    client = _CodexClient(_CompactStreamResponse())
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

    assert response.object == "response.compaction"
    assert response.id == "resp_compact_1"
    assert response.model_extra is not None
    assert response.model_extra["output"] == [
        {"type": "compaction", "status": "completed", "encrypted_content": "enc_compact_1"}
    ]
    assert client.calls[0]["url"].endswith("/backend-api/codex/responses")
    assert client.calls[0]["route"] is route
    assert client.calls[0]["json"]["model"] == "gpt-5.2"
    assert client.calls[0]["json"]["store"] is False
    assert client.calls[0]["json"]["stream"] is True
    assert client.calls[0]["headers"]["Accept"] == "text/event-stream"
    assert trace.endpoint_id == "ep_1"


@pytest.mark.asyncio
async def test_compact_responses_recovers_terminal_item_without_output_index(
    route: ResolvedUpstreamRoute,
) -> None:
    client = _RouteMetadataCodexClient(_CompactStreamWithoutOutputIndexResponse())
    payload = ResponsesCompactRequest(model="gpt-5.2", instructions="Summarize.", input="hello")

    response = await compact_responses(
        payload,
        {"user-agent": "codex"},
        "access",
        "chatgpt_account",
        session=cast(Any, object()),
        route=route,
        codex_client=cast(Any, client),
    )

    assert response.model_extra is not None
    assert response.model_extra["output"] == [
        {
            "type": "compaction",
            "status": "completed",
            "encrypted_content": "enc_compact_without_index",
        }
    ]


@pytest.mark.asyncio
async def test_compact_responses_routed_buffered_sse_keeps_compact_protocol(route: ResolvedUpstreamRoute) -> None:
    client = _RouteMetadataCodexClient(_BufferedCompactStreamResponse())
    payload = ResponsesCompactRequest(model="gpt-5.2", instructions="Summarize.", input="hello")

    response = await compact_responses(
        payload,
        {"user-agent": "codex"},
        "access",
        "chatgpt_account",
        session=cast(Any, object()),
        route=route,
        codex_client=cast(Any, client),
    )

    sent_input = client.calls[0]["json"]["input"]
    assert sent_input[-1] == {"type": "compaction_trigger"}
    assert sum(1 for item in sent_input if isinstance(item, dict) and item.get("type") == "compaction_trigger") == 1
    assert response.id == "resp_compact_buffered"
    assert response.model_extra is not None
    assert response.model_extra["service_tier"] == "default"
    assert response.model_extra["output"] == [
        {"id": "cmp_buffered", "type": "compaction", "encrypted_content": "enc_buffered"}
    ]


@pytest.mark.asyncio
async def test_compact_responses_routed_buffered_str_sse_body_keeps_compact_protocol(
    route: ResolvedUpstreamRoute,
) -> None:
    client = _RouteMetadataCodexClient(_BufferedStrCompactStreamResponse())
    payload = ResponsesCompactRequest(model="gpt-5.2", instructions="Summarize.", input="hello")

    response = await compact_responses(
        payload,
        {"user-agent": "codex"},
        "access",
        "chatgpt_account",
        session=cast(Any, object()),
        route=route,
        codex_client=cast(Any, client),
    )

    assert response.id == "resp_compact_str"
    assert response.model_extra is not None
    assert response.model_extra["output"] == [{"id": "cmp_str", "type": "compaction", "encrypted_content": "enc_str"}]


@pytest.mark.asyncio
async def test_compact_responses_message_fallback_selects_last_message(
    route: ResolvedUpstreamRoute,
) -> None:
    client = _RouteMetadataCodexClient(_BufferedMessageOnlyCompactStreamResponse())
    payload = ResponsesCompactRequest(model="gpt-5.2", instructions="Summarize.", input="hello")

    response = await compact_responses(
        payload,
        {"user-agent": "codex"},
        "access",
        "chatgpt_account",
        session=cast(Any, object()),
        route=route,
        codex_client=cast(Any, client),
    )

    assert response.id == "resp_compact_messages"
    assert response.model_extra is not None
    assert response.model_extra["output"] == [
        {"type": "compaction", "status": "completed", "encrypted_content": "enc_summary"}
    ]


@pytest.mark.asyncio
async def test_compact_responses_routed_terminal_sse_error_keeps_openai_envelope(
    route: ResolvedUpstreamRoute,
) -> None:
    client = _RouteMetadataCodexClient(_CompactTerminalFailedStreamResponse())
    payload = ResponsesCompactRequest(model="gpt-5.2", instructions="Summarize.", input="hello")

    with pytest.raises(ProxyResponseError) as exc_info:
        await compact_responses(
            payload,
            {"user-agent": "codex"},
            "access",
            "chatgpt_account",
            session=cast(Any, object()),
            route=route,
            codex_client=cast(Any, client),
        )

    error = exc_info.value.payload["error"]
    assert error["code"] == "previous_response_not_found"
    assert error["message"] == "missing anchor"
    assert error["type"] == "invalid_request_error"
    assert error["param"] == "previous_response_id"
    assert exc_info.value.failure_phase == "upstream"
    assert exc_info.value.upstream_status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_type", "error_code", "expected_status"),
    [
        ("invalid_request_error", "invalid_request_error", 400),
        ("authentication_error", "invalid_api_key", 401),
        ("authentication_error", "invalid_authentication", 401),
        ("authentication_error", "token_invalidated", 401),
        ("rate_limit_error", "rate_limit_exceeded", 429),
        ("server_error", "insufficient_quota", 429),
    ],
)
async def test_compact_responses_terminal_sse_error_infers_status_from_error_detail(
    route: ResolvedUpstreamRoute,
    error_type: str,
    error_code: str,
    expected_status: int,
) -> None:
    client = _RouteMetadataCodexClient(_CompactTerminalFailedStreamWithoutStatusResponse(error_type, error_code))
    payload = ResponsesCompactRequest(model="gpt-5.2", instructions="Summarize.", input="hello")

    with pytest.raises(ProxyResponseError) as exc_info:
        await compact_responses(
            payload,
            {"user-agent": "codex"},
            "access",
            "chatgpt_account",
            session=cast(Any, object()),
            route=route,
            codex_client=cast(Any, client),
        )

    assert exc_info.value.status_code == expected_status
    assert exc_info.value.upstream_status_code == expected_status
    assert exc_info.value.payload["error"]["type"] == error_type
    assert exc_info.value.payload["error"]["code"] == error_code


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_type", "error_code", "expected_status"),
    [
        ("invalid_request_error", "invalid_request_error", 400),
        ("rate_limit_error", "rate_limit_exceeded", 429),
    ],
)
async def test_compact_responses_routed_top_level_sse_error_preserves_type(
    route: ResolvedUpstreamRoute,
    error_type: str,
    error_code: str,
    expected_status: int,
) -> None:
    client = _RouteMetadataCodexClient(_CompactTerminalErrorStreamResponse(error_type, error_code))
    payload = ResponsesCompactRequest(model="gpt-5.2", instructions="Summarize.", input="hello")

    with pytest.raises(ProxyResponseError) as exc_info:
        await compact_responses(
            payload,
            {"user-agent": "codex"},
            "access",
            "chatgpt_account",
            session=cast(Any, object()),
            route=route,
            codex_client=cast(Any, client),
        )

    assert exc_info.value.status_code == expected_status
    error = exc_info.value.payload["error"]
    assert error["type"] == error_type
    assert error["code"] == error_code
    assert error["message"] == "compact rejected"
    assert error["param"] == "previous_response_id"


@pytest.mark.parametrize("error_type", [None, "", "   ", 123])
def test_compact_top_level_sse_error_type_uses_server_error_fallback(
    error_type: object,
) -> None:
    payload: dict[str, Any] = {
        "type": "error",
        "code": "upstream_error",
        "message": "compact failed",
    }
    if error_type is not None:
        payload["error_type"] = error_type

    detail = proxy_module._compact_sse_terminal_error_payload(payload, "error")

    assert detail["error"]["type"] == "server_error"


@pytest.mark.parametrize(
    ("payload", "expected_status"),
    [
        ({"type": "response.failed", "status_code": 200}, 502),
        ({"type": "response.failed", "status_code": 599}, 599),
    ],
)
def test_compact_sse_status_code_accepts_only_http_error_statuses(
    payload: dict[str, Any],
    expected_status: int,
) -> None:
    assert proxy_module._compact_sse_terminal_status_code(payload) == expected_status


@pytest.mark.asyncio
async def test_compact_responses_uses_upstream_chatgpt_account_id_header(route: ResolvedUpstreamRoute) -> None:
    client = _CodexClient(_CompactResponse())
    payload = ResponsesCompactRequest(model="gpt-5.2", instructions="Summarize.", input="hello")

    await compact_responses(
        payload,
        {"user-agent": "codex"},
        "access",
        "local_account_id",
        session=cast(Any, object()),
        route=route,
        codex_client=cast(Any, client),
        chatgpt_account_id="upstream_chatgpt_account_id",
    )

    assert client.calls[0]["headers"]["ChatGPT-Account-Id"] == "upstream_chatgpt_account_id"


@pytest.mark.asyncio
async def test_compact_responses_preserves_legacy_account_id_header(
    route: ResolvedUpstreamRoute,
) -> None:
    client = _CodexClient(_CompactResponse())
    payload = ResponsesCompactRequest(model="gpt-5.2", instructions="Summarize.", input="hello")

    await compact_responses(
        payload,
        {"user-agent": "codex"},
        "access",
        "legacy_upstream_account_id",
        session=cast(Any, object()),
        route=route,
        codex_client=cast(Any, client),
        chatgpt_account_id=None,
    )

    assert client.calls[0]["headers"]["ChatGPT-Account-Id"] == "legacy_upstream_account_id"


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
    assert client.calls[0]["buffer_response"] is False
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
@pytest.mark.parametrize(
    "network_error",
    [
        socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution"),
        OSError(errno.ENETUNREACH, "Network is unreachable"),
    ],
    ids=["dns", "route"],
)
async def test_stream_responses_keeps_ambiguous_routed_process_network_failures_unreplayed(
    route: ResolvedUpstreamRoute,
    network_error: OSError,
) -> None:
    client = CodexClient(_NetworkFailureSession(network_error))
    payload = ResponsesRequest(model="gpt-5.2", instructions="Reply.", input="hello", stream=True)
    session = object()
    events = [
        event
        async for event in stream_responses(
            payload,
            {"user-agent": "codex"},
            "access",
            "chatgpt_account",
            session=cast(Any, session),
            upstream_stream_transport_override="http",
            route=route,
            codex_client=client,
        )
    ]

    event = proxy_module.parse_sse_data_json(events[0])
    assert event is not None
    response = event.get("response")
    assert isinstance(response, dict)
    error = response.get("error")
    assert isinstance(error, dict)
    assert error["code"] == "proxy_network_unavailable"
    assert "ep_1" in str(error["message"])
    assert str(network_error) not in str(error["message"])


@pytest.mark.asyncio
async def test_stream_responses_marks_typed_routed_connector_failure_replay_safe(
    route: ResolvedUpstreamRoute,
) -> None:
    key = ConnectionKey("proxy.test", 8080, False, False, None, None, None)
    network_error = aiohttp.ClientConnectorError(
        key,
        socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution"),
    )
    client = CodexClient(_NetworkFailureSession(network_error))
    payload = ResponsesRequest(model="gpt-5.2", instructions="Reply.", input="hello", stream=True)

    with pytest.raises(ProxyResponseError) as exc_info:
        async for _ in stream_responses(
            payload,
            {"user-agent": "codex"},
            "access",
            "chatgpt_account",
            session=cast(Any, object()),
            upstream_stream_transport_override="http",
            route=route,
            codex_client=client,
        ):
            pass

    assert exc_info.value.payload["error"]["code"] == "proxy_network_unavailable"
    assert exc_info.value.retryable_same_contract is True
    assert exc_info.value.failure_phase == "connect"
    assert exc_info.value.failed_session is None


@pytest.mark.asyncio
async def test_stream_responses_propagates_confirmed_pre_dispatch_failure_for_status_retry(
    route: ResolvedUpstreamRoute,
) -> None:
    key = ConnectionKey("proxy.test", 8080, False, False, None, None, None)
    network_error = aiohttp.ClientProxyConnectionError(key, ConnectionRefusedError("connection refused"))
    client = CodexClient(_NetworkFailureSession(network_error))
    payload = ResponsesRequest(model="gpt-5.2", instructions="Reply.", input="hello", stream=True)

    with pytest.raises(ProxyResponseError) as exc_info:
        async for _ in stream_responses(
            payload,
            {"user-agent": "codex"},
            "access",
            "chatgpt_account",
            session=cast(Any, object()),
            upstream_stream_transport_override="http",
            route=route,
            codex_client=client,
            raise_for_status=True,
        ):
            pass

    assert exc_info.value.status_code == 502
    assert exc_info.value.payload["error"]["code"] == "upstream_unavailable"
    assert exc_info.value.retryable_same_contract is True
    assert exc_info.value.failure_phase == "connect"
    assert is_confirmed_pre_dispatch_transport_error(exc_info.value) is True
    assert "connection refused" not in str(exc_info.value.payload["error"]["message"])


@pytest.mark.asyncio
async def test_stream_responses_ambiguous_transport_failure_stays_terminal_without_retry_authorization(
    route: ResolvedUpstreamRoute,
) -> None:
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
            codex_client=cast(Any, _TransportErrorCodexClient()),
            raise_for_status=True,
        )
    ]

    # Dispatch is unknown, so even ``raise_for_status`` callers receive the
    # terminal downstream event rather than a replay-authorizing exception.
    combined = "".join(events)
    assert '"type":"response.failed"' in combined or '"type": "response.failed"' in combined
    assert '"code":"upstream_unavailable"' in combined or '"code": "upstream_unavailable"' in combined


@pytest.mark.asyncio
async def test_codex_client_buffered_body_network_failure_is_neutral_but_unsafe(
    route: ResolvedUpstreamRoute,
) -> None:
    client = CodexClient(_ResponseSession(_BufferedBodyNetworkFailureResponse()))

    with pytest.raises(CodexTransportError) as exc_info:
        await client.request("POST", "https://chatgpt.test/oauth/token", route=route, json={})

    assert exc_info.value.error_code == "proxy_network_unavailable"
    assert exc_info.value.retryable_same_contract is False
    assert exc_info.value.failure_phase == "body_read"


@pytest.mark.asyncio
async def test_compact_preserves_ambiguous_routed_process_network_code_without_replay(
    route: ResolvedUpstreamRoute,
) -> None:
    client = CodexClient(_NetworkFailureSession(OSError(errno.ENETUNREACH, "Network is unreachable")))
    payload = ResponsesCompactRequest(model="gpt-5.2", instructions="Reply.", input=[])

    with pytest.raises(ProxyResponseError) as exc_info:
        await compact_responses(
            payload,
            {"user-agent": "codex"},
            "access",
            "chatgpt_account",
            session=cast(Any, object()),
            route=route,
            codex_client=client,
        )

    assert exc_info.value.payload["error"]["code"] == "proxy_network_unavailable"
    assert exc_info.value.retryable_same_contract is False
    assert exc_info.value.failure_phase == "request"


@pytest.mark.asyncio
async def test_stream_responses_keeps_missing_proxy_hostname_endpoint_scoped(
    route: ResolvedUpstreamRoute,
) -> None:
    network_error = socket.gaierror(socket.EAI_NONAME, "Name or service not known")
    client = CodexClient(_NetworkFailureSession(network_error))
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
            codex_client=client,
        )
    ]

    assert '"code":"upstream_unavailable"' in "".join(events)


@pytest.mark.asyncio
async def test_responses_websocket_uses_codex_client_when_route_is_resolved(route: ResolvedUpstreamRoute) -> None:
    client = _WsCodexClient()

    websocket = await connect_responses_websocket(
        {"user-agent": "codex_cli_rs/0.142.0", "Origin": "https://chatgpt.test"},
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
    # Native Codex UA is preserved unchanged through the responses websocket egress.
    assert client.calls[0]["headers"]["user-agent"] == "codex_cli_rs/0.142.0"
    assert client.calls[0]["headers"]["Origin"] == "https://chatgpt.test"
    assert client.websocket.sent == ['{"type":"response.create"}']
    assert client.context.exited is True
    assert client.closed is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "network_error",
    [
        socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution"),
        OSError(errno.ENETUNREACH, "Network is unreachable"),
    ],
    ids=["dns", "route"],
)
async def test_responses_websocket_preserves_routed_process_network_failures(
    route: ResolvedUpstreamRoute,
    network_error: OSError,
) -> None:
    client = CodexClient(_NetworkFailureSession(network_error))

    with pytest.raises(ProxyResponseError) as exc_info:
        await connect_responses_websocket(
            {"user-agent": "codex"},
            "access",
            "chatgpt_account",
            base_url="https://chatgpt.test/backend-api",
            route=route,
            codex_client=client,
        )

    error = exc_info.value.payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "proxy_network_unavailable"
    assert "ep_1" in str(error["message"])
    assert str(network_error) not in str(error["message"])


@pytest.mark.asyncio
async def test_responses_websocket_keeps_missing_proxy_hostname_endpoint_scoped(
    route: ResolvedUpstreamRoute,
) -> None:
    client = CodexClient(_NetworkFailureSession(socket.gaierror(socket.EAI_NONAME, "Name or service not known")))

    with pytest.raises(ProxyResponseError) as exc_info:
        await connect_responses_websocket(
            {"user-agent": "codex"},
            "access",
            "chatgpt_account",
            base_url="https://chatgpt.test/backend-api",
            route=route,
            codex_client=client,
        )

    error = exc_info.value.payload["error"]
    assert isinstance(error, dict)
    assert error["code"] == "upstream_unavailable"


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


@pytest.mark.asyncio
async def test_responses_websocket_post_connect_network_failures_preserve_safe_code(
    route: ResolvedUpstreamRoute,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _NetworkFailureWebSocket(_FakeCodexWebSocket):
        def send_str(self, payload: str) -> None:
            del payload
            raise OSError(
                errno.ENETUNREACH,
                "proxy " + runtime_basic_auth_url("user", "pass", "proxy.test:8080") + " unreachable",
            )

        async def receive(self) -> aiohttp.WSMessage:
            raise OSError(
                errno.ENETUNREACH,
                "proxy " + runtime_basic_auth_url("user", "pass", "proxy.test:8080") + " unreachable",
            )

    client = _WsCodexClient()
    client.websocket = _NetworkFailureWebSocket()
    client.context = _FakeWsContext(client.websocket)
    rotate = AsyncMock(return_value="rotated")
    monkeypatch.setattr("app.core.clients.proxy_websocket.rotate_shared_http_transport", rotate)
    websocket = await connect_responses_websocket(
        {"user-agent": "codex"},
        "access",
        "chatgpt_account",
        base_url="https://chatgpt.test/backend-api",
        route=route,
        codex_client=cast(Any, client),
    )

    with pytest.raises(UpstreamWebSocketTransportError) as exc_info:
        await websocket.send_text('{"type":"response.create"}')
    assert exc_info.value.error_code == "proxy_network_unavailable"
    assert "user:pass" not in str(exc_info.value)

    message = await websocket.receive()
    await websocket.close()

    assert message.kind == "error"
    assert message.error_code == "proxy_network_unavailable"
    assert message.error is not None
    assert "user:pass" not in message.error
    assert rotate.await_count == 2
    assert all(call.kwargs["transport"] == "websocket" for call in rotate.await_args_list)

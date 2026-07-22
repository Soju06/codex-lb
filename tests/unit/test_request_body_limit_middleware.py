from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import cast

import pytest
from fastapi import Body, Depends, FastAPI, HTTPException
from httpx import ASGITransport, AsyncByteStream, AsyncClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import Message, Receive, Scope, Send

from app.core.config.settings import get_settings
from app.core.handlers import add_exception_handlers
from app.core.middleware.path_rewrite import BackendApiCodexV1AliasMiddleware
from app.core.middleware.request_body_limit import RequestBodyLimitMiddleware, add_request_body_limit_middleware
from app.core.middleware.request_decompression import add_request_decompression_middleware
from app.main import create_app

pytestmark = pytest.mark.unit


def _configure_limits(monkeypatch: pytest.MonkeyPatch, *, general: int, responses: int | None = None) -> None:
    monkeypatch.setenv("CODEX_LB_MAX_DECOMPRESSED_BODY_BYTES", str(general))
    monkeypatch.setenv("CODEX_LB_MAX_DECOMPRESSED_RESPONSES_BODY_BYTES", str(responses or general))
    get_settings.cache_clear()


def _http_scope(
    path: str = "/echo",
    *,
    root_path: str = "",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": root_path,
        "headers": headers or [],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }


def _request_messages(*chunks: bytes) -> list[Message]:
    return [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]


def _json_response_body(messages: list[Message]) -> dict[str, object]:
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return json.loads(body)


class _BodyConsumer:
    def __init__(self) -> None:
        self.calls = 0
        self.chunks: list[bytes] = []
        self.message_types: list[str] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.calls += 1
        while True:
            message = await receive()
            self.message_types.append(message["type"])
            if message["type"] != "http.request":
                return
            self.chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})


async def _run_direct(
    middleware: RequestBodyLimitMiddleware,
    scope: Scope,
    messages: list[Message],
) -> list[Message]:
    pending = iter(messages)
    sent: list[Message] = []

    async def receive() -> Message:
        return next(pending)

    async def send(message: Message) -> None:
        sent.append(message)

    await middleware(scope, receive, send)
    return sent


@pytest.mark.asyncio
async def test_declared_oversize_is_rejected_without_receive_or_downstream(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_limits(monkeypatch, general=5)
    inner = _BodyConsumer()
    middleware = RequestBodyLimitMiddleware(inner)
    sent: list[Message] = []

    async def receive() -> Message:
        raise AssertionError("receive must not run for a declared oversized body")

    async def send(message: Message) -> None:
        sent.append(message)

    await middleware(_http_scope(headers=[(b"content-length", b"6")]), receive, send)

    assert inner.calls == 0
    assert sent[0]["status"] == 413
    assert _json_response_body(sent)["error"] == {
        "code": "payload_too_large",
        "message": "Request body exceeds the maximum allowed size",
    }


@pytest.mark.asyncio
async def test_exact_boundary_stream_is_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_limits(monkeypatch, general=5)
    inner = _BodyConsumer()

    sent = await _run_direct(
        RequestBodyLimitMiddleware(inner),
        _http_scope(),
        _request_messages(b"12", b"345"),
    )

    assert inner.chunks == [b"12", b"345"]
    assert sent[0]["status"] == 204


@pytest.mark.asyncio
async def test_ingress_guard_forwards_each_allowed_chunk_without_prebuffering(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_limits(monkeypatch, general=5)
    events: list[str] = []
    chunks = iter(_request_messages(b"12", b"345"))
    sent: list[Message] = []
    source_reads = 0

    async def receive() -> Message:
        nonlocal source_reads
        source_reads += 1
        if source_reads == 2:
            assert "inner:12" in events
        events.append(f"source:{source_reads}")
        return next(chunks)

    async def send(message: Message) -> None:
        sent.append(message)

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        first = await receive()
        events.append(f"inner:{first['body'].decode('ascii')}")
        second = await receive()
        events.append(f"inner:{second['body'].decode('ascii')}")
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    await RequestBodyLimitMiddleware(inner)(_http_scope(), receive, send)

    assert events == ["source:1", "inner:12", "source:2", "inner:345"]
    assert sent[0]["status"] == 204


@pytest.mark.asyncio
async def test_streamed_overflow_hides_crossing_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_limits(monkeypatch, general=5)
    inner = _BodyConsumer()

    sent = await _run_direct(
        RequestBodyLimitMiddleware(inner),
        _http_scope(),
        _request_messages(b"12", b"3456"),
    )

    assert inner.chunks == [b"12"]
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_understated_content_length_cannot_bypass_stream_count(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_limits(monkeypatch, general=5)
    inner = _BodyConsumer()

    sent = await _run_direct(
        RequestBodyLimitMiddleware(inner),
        _http_scope(headers=[(b"content-length", b"1")]),
        _request_messages(b"123", b"456"),
    )

    assert inner.chunks == [b"123"]
    assert sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_malformed_content_length_falls_back_to_stream_count(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_limits(monkeypatch, general=5)
    inner = _BodyConsumer()

    sent = await _run_direct(
        RequestBodyLimitMiddleware(inner),
        _http_scope(headers=[(b"content-length", b"bogus")]),
        _request_messages(b"123", b"456"),
    )

    assert inner.chunks == [b"123"]
    assert sent[0]["status"] == 413


@pytest.mark.parametrize(
    ("path", "expected_code", "expected_type"),
    [
        ("/v1/chat/completions", "payload_too_large", "invalid_request_error"),
        ("/backend-api/codex/responses", "payload_too_large", "invalid_request_error"),
        ("/api/codex/rate-limit-reset-credits/consume", "payload_too_large", "invalid_request_error"),
        ("/internal/bridge/responses", "payload_too_large", "invalid_request_error"),
        ("/api/settings", "payload_too_large", None),
    ],
)
@pytest.mark.asyncio
async def test_oversize_uses_path_family_error_envelope(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    expected_code: str,
    expected_type: str | None,
) -> None:
    _configure_limits(monkeypatch, general=5)
    sent = await _run_direct(
        RequestBodyLimitMiddleware(_BodyConsumer()),
        _http_scope(path, headers=[(b"content-length", b"6")]),
        _request_messages(b"123456"),
    )

    error = cast(dict[str, object], _json_response_body(sent)["error"])
    assert error["code"] == expected_code
    assert error.get("type") == expected_type


@pytest.mark.asyncio
async def test_responses_paths_use_larger_budget_including_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_limits(monkeypatch, general=5, responses=8)
    for path in (
        "/v1/responses",
        "/v1/responses/",
        "/backend-api/codex/responses",
        "/backend-api/codex/responses/",
    ):
        inner = _BodyConsumer()
        sent = await _run_direct(
            RequestBodyLimitMiddleware(inner),
            _http_scope(path),
            _request_messages(b"12345678"),
        )
        assert inner.chunks == [b"12345678"]
        assert sent[0]["status"] == 204


@pytest.mark.asyncio
async def test_root_path_responses_uses_route_budget_and_openai_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_limits(monkeypatch, general=5, responses=8)
    mounted_scope = _http_scope("/api/v1/responses", root_path="/api")

    admitted_inner = _BodyConsumer()
    admitted = await _run_direct(
        RequestBodyLimitMiddleware(admitted_inner),
        mounted_scope,
        _request_messages(b"12345678"),
    )

    assert admitted_inner.chunks == [b"12345678"]
    assert admitted[0]["status"] == 204

    rejected = await _run_direct(
        RequestBodyLimitMiddleware(_BodyConsumer()),
        {**mounted_scope, "headers": [(b"content-length", b"9")]},
        _request_messages(b"123456789"),
    )

    assert rejected[0]["status"] == 413
    assert _json_response_body(rejected)["error"] == {
        "message": "Request body exceeds the maximum allowed size",
        "type": "invalid_request_error",
        "code": "payload_too_large",
    }


@pytest.mark.asyncio
async def test_outer_alias_rewrite_selects_responses_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_limits(monkeypatch, general=5, responses=8)
    inner = _BodyConsumer()
    app = BackendApiCodexV1AliasMiddleware(RequestBodyLimitMiddleware(inner))
    sent: list[Message] = []
    pending = iter(_request_messages(b"12345678"))

    async def receive() -> Message:
        return next(pending)

    async def send(message: Message) -> None:
        sent.append(message)

    scope = _http_scope("/backend-api/codex/v1/responses/")
    await app(dict(scope), receive, send)

    assert inner.chunks == [b"12345678"]
    assert sent[0]["status"] == 204


@pytest.mark.asyncio
async def test_unencoded_multipart_is_exempt_but_encoded_multipart_is_guarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_limits(monkeypatch, general=5)
    multipart = (b"content-type", b"multipart/form-data; boundary=test")

    plain_inner = _BodyConsumer()
    plain_sent = await _run_direct(
        RequestBodyLimitMiddleware(plain_inner),
        _http_scope(headers=[multipart, (b"content-length", b"8")]),
        _request_messages(b"12345678"),
    )
    assert plain_inner.chunks == [b"12345678"]
    assert plain_sent[0]["status"] == 204

    encoded_inner = _BodyConsumer()
    encoded_sent = await _run_direct(
        RequestBodyLimitMiddleware(encoded_inner),
        _http_scope(headers=[multipart, (b"content-encoding", b"identity"), (b"content-length", b"8")]),
        _request_messages(b"12345678"),
    )
    assert encoded_inner.calls == 0
    assert encoded_sent[0]["status"] == 413


@pytest.mark.asyncio
async def test_disconnect_and_unrelated_receive_failures_are_not_converted(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_limits(monkeypatch, general=5)
    disconnect_inner = _BodyConsumer()
    await _run_direct(
        RequestBodyLimitMiddleware(disconnect_inner),
        _http_scope(),
        [{"type": "http.disconnect"}],
    )
    assert disconnect_inner.message_types == ["http.disconnect"]

    async def receive_failure() -> Message:
        raise RuntimeError("receive failed")

    async def send(_: Message) -> None:
        raise AssertionError("send must not run")

    with pytest.raises(RuntimeError, match="receive failed"):
        await RequestBodyLimitMiddleware(_BodyConsumer())(_http_scope(), receive_failure, send)

    async def receive_cancelled() -> Message:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await RequestBodyLimitMiddleware(_BodyConsumer())(_http_scope(), receive_cancelled, send)


@pytest.mark.asyncio
async def test_non_http_scopes_pass_through(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_limits(monkeypatch, general=5)
    seen: list[Scope] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(scope)

    async def receive() -> Message:
        return {"type": "websocket.connect"}

    async def send(_: Message) -> None:
        return None

    for scope_type in ("websocket", "lifespan"):
        scope: Scope = {"type": scope_type}
        await RequestBodyLimitMiddleware(inner)(scope, receive, send)

    assert [scope["type"] for scope in seen] == ["websocket", "lifespan"]


@pytest.mark.asyncio
async def test_overflow_after_response_start_does_not_send_second_start(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_limits(monkeypatch, general=5)
    sent: list[Message] = []

    async def inner(scope: Scope, receive: Receive, send: Send) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await receive()

    pending = iter(_request_messages(b"123456"))

    async def receive() -> Message:
        return next(pending)

    async def send(message: Message) -> None:
        sent.append(message)

    with pytest.raises(Exception) as exc_info:
        await RequestBodyLimitMiddleware(inner)(_http_scope(), receive, send)

    assert type(exc_info.value).__name__ == "_RequestBodyTooLarge"
    assert [message["type"] for message in sent] == ["http.response.start"]


class _ChunkedBody(AsyncByteStream):
    def __init__(self, *chunks: bytes) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


def _build_typed_body_app(auth_calls: list[bool]) -> FastAPI:
    app = FastAPI()
    add_request_decompression_middleware(app)
    add_request_body_limit_middleware(app)
    add_exception_handlers(app)

    async def require_auth() -> None:
        auth_calls.append(True)
        raise HTTPException(status_code=401, detail="missing credentials")

    @app.post("/v1/typed", dependencies=[Depends(require_auth)])
    async def typed_body(payload: dict[str, object] = Body(...)) -> dict[str, object]:
        return payload

    return app


@pytest.mark.asyncio
async def test_typed_chunked_body_restores_receive_overflow_to_413(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_limits(monkeypatch, general=5)
    auth_calls: list[bool] = []
    transport = ASGITransport(app=_build_typed_body_app(auth_calls))

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/typed",
            content=_ChunkedBody(b'{"x":', b'"too long"}'),
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json()["error"] == {
        "message": "Request body exceeds the maximum allowed size",
        "type": "invalid_request_error",
        "code": "payload_too_large",
    }
    assert auth_calls == []


@pytest.mark.asyncio
async def test_admitted_typed_body_runs_route_authorization_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_limits(monkeypatch, general=32)
    auth_calls: list[bool] = []
    transport = ASGITransport(app=_build_typed_body_app(auth_calls))

    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/v1/typed",
            content=_ChunkedBody(b"{}"),
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 401
    assert auth_calls == [True]


def test_production_middleware_order_keeps_alias_and_admission_outside_body_read() -> None:
    middleware = create_app().user_middleware
    alias_index = next(index for index, item in enumerate(middleware) if item.cls is BackendApiCodexV1AliasMiddleware)
    limit_index = next(index for index, item in enumerate(middleware) if item.cls is RequestBodyLimitMiddleware)
    decompression_index = next(
        index
        for index, item in enumerate(middleware)
        if item.cls is BaseHTTPMiddleware and item.kwargs.get("dispatch").__name__ == "request_decompression_middleware"
    )

    assert alias_index < limit_index < decompression_index

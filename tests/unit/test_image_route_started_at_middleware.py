from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, StreamingResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.exceptions import ProxyAuthError
from app.core.handlers import add_exception_handlers
from app.core.handlers import exceptions as exceptions_module
from app.core.handlers.exceptions import ImageRouteStartedAtMiddleware
from app.core.middleware.app_version import AppVersionMiddleware
from app.core.middleware.trusted_proxy_headers import TrustedProxyHeadersMiddleware
from app.main import create_app
from app.modules.proxy.images_observability import IMAGE_ROUTE_STARTED_AT_STATE

pytestmark = pytest.mark.unit

_IMAGE_PATHS = (
    "/v1/images/generations",
    "/v1/images/edits",
    "/backend-api/codex/images/generations",
    "/backend-api/codex/images/edits",
)


def _http_scope(path: str, *, root_path: str = "", json_body: bool = False) -> Scope:
    headers = [(b"host", b"testserver")]
    if json_body:
        headers.append((b"content-type", b"application/json"))
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": root_path,
        "headers": headers,
        "client": ("127.0.0.1", 1),
        "server": ("testserver", 80),
    }


def _server_receive() -> Receive:
    delivered = False

    async def receive() -> Message:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": b"{}", "more_body": False}

    return receive


class _RecordingApp:
    def __init__(self) -> None:
        self.scopes: list[Scope] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.scopes.append(scope)


async def _drive(app: ASGIApp, scope: Scope) -> list[Message]:
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    await app(scope, _server_receive(), send)
    return sent


async def _sse_body() -> AsyncIterator[bytes]:
    yield b"data: one\n\n"
    yield b"data: two\n\n"
    yield b"data: [DONE]\n\n"


def _build_relay_app(*, with_middleware: bool) -> FastAPI:
    """Constant-body routes on the image paths, used to compare forwarded messages."""
    app = FastAPI()
    if with_middleware:
        add_exception_handlers(app)

    @app.post("/v1/images/generations")
    async def generations() -> JSONResponse:
        return JSONResponse({"data": [{"b64_json": "AAAA"}]})

    @app.post("/v1/images/edits")
    async def edits() -> StreamingResponse:
        return StreamingResponse(_sse_body(), media_type="text/event-stream")

    return app


def test_production_stack_has_no_basehttp_middleware() -> None:
    app = create_app()
    middleware = app.user_middleware

    def _index_of(cls: object) -> int:
        return next(index for index, entry in enumerate(middleware) if entry.cls is cls)

    assert all(entry.cls is not BaseHTTPMiddleware for entry in middleware)
    assert _index_of(TrustedProxyHeadersMiddleware) < _index_of(ImageRouteStartedAtMiddleware)
    assert _index_of(ImageRouteStartedAtMiddleware) < _index_of(AppVersionMiddleware)
    # Inspecting user_middleware must not force the stack to build (see test_otel).
    assert app.middleware_stack is None


@pytest.mark.parametrize("path", _IMAGE_PATHS)
async def test_image_route_scope_receives_float_started_at(path: str) -> None:
    downstream = _RecordingApp()
    before = time.perf_counter()

    await _drive(ImageRouteStartedAtMiddleware(downstream), _http_scope(path))

    (scope,) = downstream.scopes
    started_at = scope["state"][IMAGE_ROUTE_STARTED_AT_STATE]
    assert isinstance(started_at, float)
    assert before <= started_at <= time.perf_counter()


async def test_image_route_started_at_reuses_existing_scope_state() -> None:
    downstream = _RecordingApp()
    scope = _http_scope("/v1/images/generations")
    existing_state: dict[str, Any] = {"other": "kept"}
    scope["state"] = existing_state

    await _drive(ImageRouteStartedAtMiddleware(downstream), scope)

    assert downstream.scopes[0]["state"] is existing_state
    assert existing_state["other"] == "kept"
    assert isinstance(existing_state[IMAGE_ROUTE_STARTED_AT_STATE], float)


async def test_image_route_started_at_strips_root_path() -> None:
    downstream = _RecordingApp()

    await _drive(
        ImageRouteStartedAtMiddleware(downstream),
        _http_scope("/prefix/v1/images/edits", root_path="/prefix"),
    )

    assert isinstance(downstream.scopes[0]["state"][IMAGE_ROUTE_STARTED_AT_STATE], float)


@pytest.mark.parametrize("path", ["/v1/responses", "/v1/images/generations/", "/api/dashboard/overview"])
async def test_non_image_http_scope_is_left_untouched(path: str) -> None:
    downstream = _RecordingApp()
    scope = _http_scope(path)

    await _drive(ImageRouteStartedAtMiddleware(downstream), scope)

    assert downstream.scopes == [scope]
    assert "state" not in scope


async def test_websocket_scope_passes_through_untouched() -> None:
    downstream = _RecordingApp()
    scope: Scope = {"type": "websocket", "path": "/v1/images/generations", "root_path": ""}

    await _drive(ImageRouteStartedAtMiddleware(downstream), scope)

    assert downstream.scopes == [scope]
    assert "state" not in scope


async def test_request_state_exposes_started_at_to_route_handler() -> None:
    app = FastAPI()
    add_exception_handlers(app)

    @app.post("/v1/images/generations")
    async def generations(request: Request) -> JSONResponse:
        started_at = getattr(request.state, IMAGE_ROUTE_STARTED_AT_STATE, None)
        return JSONResponse({"started_at_type": type(started_at).__name__})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/v1/images/generations", json={"model": "gpt-image-2"})

    assert response.status_code == 200
    assert response.json() == {"started_at_type": "float"}


async def test_exception_observability_consumes_middleware_started_at(monkeypatch: pytest.MonkeyPatch) -> None:
    recorded: list[float] = []

    def fake_record(**kwargs: Any) -> None:
        recorded.append(kwargs["started_at"])

    monkeypatch.setattr(exceptions_module, "record_images_route_observability", fake_record)
    app = FastAPI()
    add_exception_handlers(app)

    @app.post("/v1/images/generations")
    async def deny() -> JSONResponse:
        raise ProxyAuthError("missing credentials")

    scope = _http_scope("/v1/images/generations", json_body=True)

    sent = await _drive(app, scope)

    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 401
    # The handler read the ingress stamp through request.state, not the perf_counter fallback.
    assert recorded == [scope["state"][IMAGE_ROUTE_STARTED_AT_STATE]]


@pytest.mark.parametrize("path", ["/v1/images/generations", "/v1/images/edits"])
async def test_forwarded_messages_are_identical_with_and_without_middleware(path: str) -> None:
    without = await _drive(_build_relay_app(with_middleware=False), _http_scope(path, json_body=True))
    with_middleware = await _drive(_build_relay_app(with_middleware=True), _http_scope(path, json_body=True))

    assert with_middleware == without
    assert [message["type"] for message in without][:2] == ["http.response.start", "http.response.body"]
    assert without[-1].get("more_body", False) is False


async def test_mid_stream_generator_failure_propagates_without_terminator() -> None:
    app = FastAPI()
    add_exception_handlers(app)

    @app.post("/v1/images/edits")
    async def failing_stream() -> StreamingResponse:
        async def body() -> AsyncIterator[bytes]:
            yield b"one"
            yield b"two"
            raise RuntimeError("upstream broke mid-stream")

        return StreamingResponse(body(), media_type="text/event-stream")

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    with pytest.raises(RuntimeError, match="mid-stream"):
        await app(_http_scope("/v1/images/edits"), _server_receive(), send)

    bodies = [(message["body"], message["more_body"]) for message in sent if message["type"] == "http.response.body"]
    # BaseHTTPMiddleware used to append a synthetic ``more_body=False`` chunk before
    # re-raising; pure ASGI leaves the stream unterminated so the server closes it.
    assert bodies == [(b"one", True), (b"two", True)]

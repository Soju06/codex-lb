from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncByteStream, AsyncClient
from starlette.datastructures import Headers
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import Message, Scope

from app.core.auth.dependencies import validate_dashboard_session, validate_proxy_api_key
from app.core.config.settings import get_settings
from app.core.exceptions import DashboardPermissionError, ProxyAuthError
from app.core.middleware.firewall_cache import get_firewall_ip_cache
from app.core.middleware.multipart_content_encoding import (
    MultipartContentEncodingMiddleware,
    UnsupportedMultipartContentEncoding,
    multipart_error_response,
    raise_for_unsupported_multipart_content_encoding,
)
from app.core.middleware.path_rewrite import BackendApiCodexV1AliasMiddleware
from app.core.middleware.request_body_limit import RequestBodyLimitMiddleware
from app.main import create_app

pytestmark = pytest.mark.unit

_DEDICATED_MULTIPART_PATHS = (
    "/api/accounts/import",
    "/backend-api/transcribe",
    "/v1/audio/transcriptions",
    "/v1/images/edits",
)


def _scope(
    path: str,
    *,
    content_type: bytes = b"multipart/form-data; boundary=test",
    content_encoding: bytes | None = b"gzip",
    root_path: str = "",
) -> Scope:
    headers = [(b"content-type", content_type)]
    if content_encoding is not None:
        headers.append((b"content-encoding", content_encoding))
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
        "headers": headers,
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }


async def _run(scope: Scope) -> tuple[list[Message], list[Headers], list[bool], int]:
    sent: list[Message] = []
    downstream_headers: list[Headers] = []
    downstream_unsupported: list[bool] = []
    receive_calls = 0

    async def receive() -> Message:
        nonlocal receive_calls
        receive_calls += 1
        return {"type": "http.request", "body": b"body", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    async def inner(inner_scope: Scope, receive, send) -> None:
        downstream_headers.append(Headers(scope=inner_scope))
        try:
            raise_for_unsupported_multipart_content_encoding(Request(inner_scope))
        except UnsupportedMultipartContentEncoding:
            downstream_unsupported.append(True)
        else:
            downstream_unsupported.append(False)
        message = await receive()
        assert message["type"] == "http.request"
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    await MultipartContentEncodingMiddleware(inner)(scope, receive, send)
    return sent, downstream_headers, downstream_unsupported, receive_calls


@pytest.mark.asyncio
async def test_encoded_upload_is_marked_and_removed_before_decompression() -> None:
    sent, downstream, unsupported, receive_calls = await _run(_scope("/v1/images/edits"))

    assert sent[0]["status"] == 204
    assert downstream[0].get("content-encoding") is None
    assert unsupported == [True]
    assert receive_calls == 1


@pytest.mark.asyncio
async def test_encoding_gate_does_not_read_body_before_authorization() -> None:
    receive_calls = 0
    sent: list[Message] = []

    async def receive() -> Message:
        nonlocal receive_calls
        receive_calls += 1
        raise AssertionError("encoding admission must not read the request body")

    async def send(message: Message) -> None:
        sent.append(message)

    async def inner(scope: Scope, receive, send) -> None:
        del receive
        with pytest.raises(UnsupportedMultipartContentEncoding):
            raise_for_unsupported_multipart_content_encoding(Request(scope))
        await send({"type": "http.response.start", "status": 401, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    await MultipartContentEncodingMiddleware(inner)(_scope("/api/accounts/import"), receive, send)

    assert sent[0]["status"] == 401
    assert receive_calls == 0


@pytest.mark.asyncio
async def test_identity_is_removed_without_consuming_or_replacing_body() -> None:
    sent, downstream, unsupported, receive_calls = await _run(
        _scope("/backend-api/transcribe", content_encoding=b" identity, identity ")
    )

    assert sent[0]["status"] == 204
    assert len(downstream) == 1
    assert downstream[0].get("content-encoding") is None
    assert unsupported == [False]
    assert receive_calls == 1


@pytest.mark.asyncio
async def test_duplicate_content_encoding_headers_are_combined() -> None:
    scope = _scope("/v1/images/edits", content_encoding=b"identity")
    headers = list(scope["headers"])
    headers.append((b"content-encoding", b"gzip"))
    scope["headers"] = headers

    sent, downstream, unsupported, receive_calls = await _run(scope)

    assert sent[0]["status"] == 204
    assert downstream[0].getlist("content-encoding") == []
    assert unsupported == [True]
    assert receive_calls == 1


@pytest.mark.asyncio
async def test_mounted_and_trailing_slash_path_is_classified_application_relative() -> None:
    sent, downstream, unsupported, receive_calls = await _run(
        _scope("/prefix/v1/audio/transcriptions/", root_path="/prefix")
    )

    assert sent[0]["status"] == 204
    assert downstream[0].get("content-encoding") is None
    assert unsupported == [True]
    assert receive_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [_scope("/v1/chat/completions"), _scope("/v1/images/edits", content_encoding=None)])
async def test_unrelated_or_unencoded_requests_pass_through(scope: Scope) -> None:
    sent, downstream, unsupported, receive_calls = await _run(scope)

    assert sent[0]["status"] == 204
    assert len(downstream) == 1
    assert unsupported == [False]
    assert receive_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", [b"application/json", b""])
async def test_protected_path_marks_encoding_even_when_content_type_is_not_multipart(content_type: bytes) -> None:
    sent, downstream, unsupported, receive_calls = await _run(_scope("/v1/images/edits", content_type=content_type))

    assert sent[0]["status"] == 204
    assert downstream[0].get("content-encoding") is None
    assert unsupported == [True]
    assert receive_calls == 1


@pytest.mark.asyncio
async def test_wrong_method_is_untouched() -> None:
    scope = _scope("/v1/images/edits")
    scope["method"] = "GET"

    sent, downstream, unsupported, receive_calls = await _run(scope)

    assert sent[0]["status"] == 204
    assert downstream[0].get("content-encoding") == "gzip"
    assert unsupported == [False]
    assert receive_calls == 1


@pytest.mark.asyncio
async def test_non_http_scope_passes_through() -> None:
    seen: list[str] = []

    async def inner(scope: Scope, receive, send) -> None:
        del receive, send
        seen.append(scope["type"])

    async def receive() -> Message:
        return {"type": "websocket.connect"}

    async def send(_: Message) -> None:
        return None

    await MultipartContentEncodingMiddleware(inner)({"type": "websocket"}, receive, send)

    assert seen == ["websocket"]


@pytest.mark.parametrize(
    ("path", "root_path", "expected_code", "expected_type"),
    [
        ("/prefix/api/accounts/import", "/prefix", "invalid_request", None),
        ("/prefix/v1/images/edits", "/prefix", "invalid_request_error", "invalid_request_error"),
    ],
)
def test_multipart_error_envelope_uses_application_relative_mounted_path(
    path: str,
    root_path: str,
    expected_code: str,
    expected_type: str | None,
) -> None:
    response = multipart_error_response(
        Request(_scope(path, root_path=root_path)),
        status_code=400,
        code="invalid_request",
        message="invalid upload",
    )

    payload = json.loads(bytes(response.body))
    assert payload["error"]["code"] == expected_code
    assert payload["error"].get("type") == expected_type


class _TrackingBody(AsyncByteStream):
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.iterations = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.iterations += 1
        yield self.body


def _configure_tiny_generic_ingress_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODEX_LB_MAX_DECOMPRESSED_BODY_BYTES", "5")
    monkeypatch.setenv("CODEX_LB_MAX_DECOMPRESSED_RESPONSES_BODY_BYTES", "5")
    get_settings.cache_clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _DEDICATED_MULTIPART_PATHS)
@pytest.mark.parametrize(
    ("content_type", "content_encoding"),
    [
        ("multipart/form-data; boundary=unused", None),
        ("multipart/form-data; boundary=unused", "identity"),
        ("multipart/form-data; boundary=unused", "gzip"),
        ("application/json", "identity"),
        ("application/json", "gzip"),
    ],
)
async def test_production_stack_keeps_dedicated_uploads_auth_first_above_generic_limit(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    content_type: str,
    content_encoding: str | None,
) -> None:
    _configure_tiny_generic_ingress_budget(monkeypatch)
    await get_firewall_ip_cache().set("127.0.0.1", True)
    authorization_calls: list[str] = []
    body = _TrackingBody(b"unread")

    async def reject_dashboard(request: Request) -> None:
        authorization_calls.append(request.url.path)
        raise DashboardPermissionError("read only", code="read_only_access")

    async def reject_proxy(request: Request) -> None:
        authorization_calls.append(request.url.path)
        raise ProxyAuthError("missing API key")

    app = create_app()
    app.dependency_overrides[validate_dashboard_session] = reject_dashboard
    app.dependency_overrides[validate_proxy_api_key] = reject_proxy
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            headers = {
                "Content-Type": content_type,
                "Content-Length": "6",
            }
            if content_encoding is not None:
                headers["Content-Encoding"] = content_encoding
            response = await client.post(path, content=body, headers=headers)
    finally:
        get_settings.cache_clear()

    assert response.status_code == (403 if path.startswith("/api/") else 401)
    assert authorization_calls == [path]
    assert body.iterations == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("content_encoding", [None, "identity", "gzip"])
async def test_production_stack_keeps_unrelated_multipart_under_generic_guard(
    monkeypatch: pytest.MonkeyPatch,
    content_encoding: str | None,
) -> None:
    _configure_tiny_generic_ingress_budget(monkeypatch)
    await get_firewall_ip_cache().set("127.0.0.1", True)
    authorization_calls: list[str] = []
    body = _TrackingBody(b"unread")

    async def reject_proxy(request: Request) -> None:
        authorization_calls.append(request.url.path)
        raise ProxyAuthError("missing API key")

    app = create_app()
    app.dependency_overrides[validate_proxy_api_key] = reject_proxy
    transport = ASGITransport(app=app)

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            headers = {
                "Content-Type": "multipart/form-data; boundary=unused",
                "Content-Length": "6",
            }
            if content_encoding is not None:
                headers["Content-Encoding"] = content_encoding
            response = await client.post("/v1/chat/completions", content=body, headers=headers)
    finally:
        get_settings.cache_clear()

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert authorization_calls == []
    assert body.iterations == 0


def test_production_middleware_order_composes_route_and_generic_ingress_guards() -> None:
    middleware = create_app().user_middleware
    alias_index = next(index for index, item in enumerate(middleware) if item.cls is BackendApiCodexV1AliasMiddleware)
    multipart_index = next(
        index for index, item in enumerate(middleware) if item.cls is MultipartContentEncodingMiddleware
    )
    limit_index = next(index for index, item in enumerate(middleware) if item.cls is RequestBodyLimitMiddleware)
    decompression_index = next(
        index
        for index, item in enumerate(middleware)
        if item.cls is BaseHTTPMiddleware and item.kwargs.get("dispatch").__name__ == "request_decompression_middleware"
    )

    assert alias_index < multipart_index < limit_index < decompression_index

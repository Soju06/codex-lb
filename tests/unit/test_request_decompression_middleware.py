from __future__ import annotations

import gzip
import json
import zlib
from collections.abc import Awaitable, Callable
from typing import cast

import pytest
import zstandard as zstd
from fastapi import FastAPI, Request
from fastapi.responses import Response
from httpx import ASGITransport, AsyncClient
from starlette.requests import ClientDisconnect

from app.core.middleware.request_body_limit import add_request_body_limit_middleware
from app.core.middleware.request_decompression import add_request_decompression_middleware

pytestmark = pytest.mark.unit

_Dispatch = Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]


def _build_echo_app(*, touch_headers: bool = False) -> FastAPI:
    app = FastAPI()
    add_request_decompression_middleware(app)
    add_request_body_limit_middleware(app)

    if touch_headers:

        @app.middleware("http")
        async def touch_headers_middleware(request: Request, call_next):
            _ = request.headers.get("content-encoding")
            return await call_next(request)

    @app.post("/echo")
    async def echo(request: Request):
        data = await request.json()
        return {"content_encoding": request.headers.get("content-encoding"), "data": data}

    @app.post("/backend-api/codex/responses")
    async def responses(request: Request):
        data = await request.json()
        return {"content_encoding": request.headers.get("content-encoding"), "data": data}

    @app.post("/backend-api/codex/responses/")
    async def responses_slash(request: Request):
        data = await request.json()
        return {"content_encoding": request.headers.get("content-encoding"), "data": data}

    return app


@pytest.mark.asyncio
async def test_request_decompression_clears_cached_headers():
    app = _build_echo_app(touch_headers=True)

    payload = {"hello": "world"}
    body = json.dumps(payload).encode("utf-8")
    compressed = zstd.ZstdCompressor().compress(body)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/echo",
            content=compressed,
            headers={"Content-Encoding": "zstd", "Content-Type": "application/json"},
        )

    assert resp.status_code == 200
    response_data = resp.json()
    assert response_data["content_encoding"] is None
    assert response_data["data"] == payload


@pytest.mark.asyncio
async def test_request_decompression_supports_gzip():
    app = _build_echo_app()

    payload = {"hello": "gzip"}
    body = json.dumps(payload).encode("utf-8")
    compressed = gzip.compress(body)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/echo",
            content=compressed,
            headers={"Content-Encoding": "gzip", "Content-Type": "application/json"},
        )

    assert resp.status_code == 200
    response_data = resp.json()
    assert response_data["content_encoding"] is None
    assert response_data["data"] == payload


@pytest.mark.asyncio
async def test_request_decompression_supports_deflate():
    app = _build_echo_app()

    payload = {"hello": "deflate"}
    body = json.dumps(payload).encode("utf-8")
    compressed = zlib.compress(body)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/echo",
            content=compressed,
            headers={"Content-Encoding": "deflate", "Content-Type": "application/json"},
        )

    assert resp.status_code == 200
    response_data = resp.json()
    assert response_data["content_encoding"] is None
    assert response_data["data"] == payload


@pytest.mark.asyncio
async def test_request_decompression_allows_identity():
    app = _build_echo_app()

    payload = {"hello": "identity"}
    body = json.dumps(payload).encode("utf-8")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/echo",
            content=body,
            headers={"Content-Encoding": "identity", "Content-Type": "application/json"},
        )

    assert resp.status_code == 200
    response_data = resp.json()
    assert response_data["content_encoding"] is None
    assert response_data["data"] == payload


@pytest.mark.asyncio
async def test_request_decompression_supports_multiple_encodings():
    app = _build_echo_app()

    payload = {"hello": "multi"}
    body = json.dumps(payload).encode("utf-8")
    gzip_body = gzip.compress(body)
    compressed = zstd.ZstdCompressor().compress(gzip_body)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/echo",
            content=compressed,
            headers={"Content-Encoding": "gzip, zstd", "Content-Type": "application/json"},
        )

    assert resp.status_code == 200
    response_data = resp.json()
    assert response_data["content_encoding"] is None
    assert response_data["data"] == payload


@pytest.mark.asyncio
async def test_request_decompression_rejects_unsupported_encoding():
    app = _build_echo_app()

    payload = {"hello": "br"}
    body = json.dumps(payload).encode("utf-8")
    compressed = gzip.compress(body)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/echo",
            content=compressed,
            headers={"Content-Encoding": "br", "Content-Type": "application/json"},
        )

    assert resp.status_code == 400
    response_data = resp.json()
    assert response_data["error"]["code"] == "invalid_request"
    assert response_data["error"]["message"] == "Unsupported Content-Encoding"


@pytest.mark.parametrize(
    ("encoding", "encode"),
    [
        ("identity", lambda body: body),
        ("gzip", gzip.compress),
        ("deflate", zlib.compress),
        ("zstd", lambda body: zstd.ZstdCompressor().compress(body)),
        ("gzip, zstd", lambda body: zstd.ZstdCompressor().compress(gzip.compress(body))),
    ],
)
@pytest.mark.asyncio
async def test_request_decompression_rejects_raw_encoded_body_over_limit(
    monkeypatch,
    encoding,
    encode,
):
    payload = {"input": "x" * 512}
    encoded = encode(json.dumps(payload).encode("utf-8"))
    monkeypatch.setenv("CODEX_LB_MAX_DECOMPRESSED_BODY_BYTES", str(len(encoded) - 1))

    transport = ASGITransport(app=_build_echo_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/echo",
            content=encoded,
            headers={"Content-Encoding": encoding, "Content-Type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


@pytest.mark.parametrize(
    ("encoding", "encode"),
    [
        ("identity", lambda body: body),
        ("gzip", gzip.compress),
        ("deflate", zlib.compress),
        ("zstd", lambda body: zstd.ZstdCompressor().compress(body)),
        ("gzip, zstd", lambda body: zstd.ZstdCompressor().compress(gzip.compress(body))),
    ],
)
@pytest.mark.asyncio
async def test_request_decompression_allows_exact_decoded_boundary(
    monkeypatch,
    encoding,
    encode,
):
    payload = {"input": "x" * 512}
    body = json.dumps(payload).encode("utf-8")
    encoded = encode(body)
    monkeypatch.setenv("CODEX_LB_MAX_DECOMPRESSED_BODY_BYTES", str(len(body)))

    transport = ASGITransport(app=_build_echo_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/echo",
            content=encoded,
            headers={"Content-Encoding": encoding, "Content-Type": "application/json"},
        )

    assert response.status_code == 200
    assert response.json()["data"] == payload


@pytest.mark.parametrize(
    ("encoding", "encode"),
    [
        ("identity", lambda body: body),
        ("gzip", gzip.compress),
        ("deflate", zlib.compress),
        ("zstd", lambda body: zstd.ZstdCompressor().compress(body)),
        ("gzip, zstd", lambda body: zstd.ZstdCompressor().compress(gzip.compress(body))),
    ],
)
@pytest.mark.asyncio
async def test_request_decompression_rejects_one_byte_over_decoded_boundary(
    monkeypatch,
    encoding,
    encode,
):
    body = json.dumps({"input": "x" * 512}).encode("utf-8")
    encoded = encode(body)
    monkeypatch.setenv("CODEX_LB_MAX_DECOMPRESSED_BODY_BYTES", str(len(body) - 1))

    transport = ASGITransport(app=_build_echo_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/echo",
            content=encoded,
            headers={"Content-Encoding": encoding, "Content-Type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


@pytest.mark.asyncio
async def test_request_decompression_uses_openai_envelope_for_unsupported_encoding(monkeypatch):
    monkeypatch.setenv("CODEX_LB_MAX_DECOMPRESSED_BODY_BYTES", "2048")
    monkeypatch.setenv("CODEX_LB_MAX_DECOMPRESSED_RESPONSES_BODY_BYTES", "2048")

    transport = ASGITransport(app=_build_echo_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/backend-api/codex/responses",
            content=b"not-brotli",
            headers={"Content-Encoding": "br", "Content-Type": "application/json"},
        )

    assert response.status_code == 400
    assert response.json()["error"] == {
        "message": "Unsupported Content-Encoding",
        "type": "invalid_request_error",
        "code": "invalid_request_error",
    }


@pytest.mark.asyncio
async def test_request_decompression_uses_openai_envelope_for_malformed_body(monkeypatch):
    monkeypatch.setenv("CODEX_LB_MAX_DECOMPRESSED_BODY_BYTES", "2048")
    monkeypatch.setenv("CODEX_LB_MAX_DECOMPRESSED_RESPONSES_BODY_BYTES", "2048")

    transport = ASGITransport(app=_build_echo_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/backend-api/codex/responses",
            content=b"not-zstd",
            headers={"Content-Encoding": "zstd", "Content-Type": "application/json"},
        )

    assert response.status_code == 400
    assert response.json()["error"] == {
        "message": "Request body is compressed but could not be decompressed",
        "type": "invalid_request_error",
        "code": "invalid_request_error",
    }


@pytest.mark.asyncio
async def test_request_decompression_uses_openai_envelope_for_expanded_overflow(monkeypatch):
    monkeypatch.setenv("CODEX_LB_MAX_DECOMPRESSED_BODY_BYTES", "128")
    monkeypatch.setenv("CODEX_LB_MAX_DECOMPRESSED_RESPONSES_BODY_BYTES", "128")
    body = json.dumps({"input": "x" * 512}).encode("utf-8")
    compressed = zstd.ZstdCompressor().compress(body)
    assert len(compressed) < 128 < len(body)

    transport = ASGITransport(app=_build_echo_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/backend-api/codex/responses",
            content=compressed,
            headers={"Content-Encoding": "zstd", "Content-Type": "application/json"},
        )

    assert response.status_code == 413
    assert response.json()["error"] == {
        "message": "Request body exceeds the maximum allowed size",
        "type": "invalid_request_error",
        "code": "payload_too_large",
    }


@pytest.mark.asyncio
async def test_request_decompression_allows_larger_responses_payload(monkeypatch):
    monkeypatch.setenv("CODEX_LB_MAX_DECOMPRESSED_BODY_BYTES", "128")
    monkeypatch.setenv("CODEX_LB_MAX_DECOMPRESSED_RESPONSES_BODY_BYTES", "2048")

    app = _build_echo_app()

    payload = {"input": "x" * 512}
    body = json.dumps(payload).encode("utf-8")
    compressed = zstd.ZstdCompressor().compress(body)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/backend-api/codex/responses",
            content=compressed,
            headers={"Content-Encoding": "zstd", "Content-Type": "application/json"},
        )

    assert resp.status_code == 200
    response_data = resp.json()
    assert response_data["content_encoding"] is None
    assert response_data["data"] == payload


@pytest.mark.asyncio
async def test_request_decompression_allows_larger_responses_payload_under_root_path(monkeypatch):
    monkeypatch.setenv("CODEX_LB_MAX_DECOMPRESSED_BODY_BYTES", "128")
    monkeypatch.setenv("CODEX_LB_MAX_DECOMPRESSED_RESPONSES_BODY_BYTES", "2048")

    payload = {"input": "x" * 512}
    body = json.dumps(payload).encode("utf-8")
    compressed = zstd.ZstdCompressor().compress(body)
    assert len(compressed) < 128 < len(body)

    transport = ASGITransport(app=_build_echo_app(), root_path="/api")
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/backend-api/codex/responses",
            content=compressed,
            headers={"Content-Encoding": "zstd", "Content-Type": "application/json"},
        )

    assert response.status_code == 200
    assert response.json()["data"] == payload


@pytest.mark.asyncio
async def test_request_decompression_allows_larger_trailing_slash_responses_payload(monkeypatch):
    monkeypatch.setenv("CODEX_LB_MAX_DECOMPRESSED_BODY_BYTES", "128")
    monkeypatch.setenv("CODEX_LB_MAX_DECOMPRESSED_RESPONSES_BODY_BYTES", "2048")

    app = _build_echo_app()

    payload = {"input": "x" * 512}
    body = json.dumps(payload).encode("utf-8")
    compressed = zstd.ZstdCompressor().compress(body)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/backend-api/codex/responses/",
            content=compressed,
            headers={"Content-Encoding": "zstd", "Content-Type": "application/json"},
        )

    assert resp.status_code == 200
    response_data = resp.json()
    assert response_data["content_encoding"] is None
    assert response_data["data"] == payload


@pytest.mark.asyncio
async def test_request_decompression_keeps_default_limit_for_other_routes(monkeypatch):
    monkeypatch.setenv("CODEX_LB_MAX_DECOMPRESSED_BODY_BYTES", "128")
    monkeypatch.setenv("CODEX_LB_MAX_DECOMPRESSED_RESPONSES_BODY_BYTES", "2048")

    app = _build_echo_app()

    payload = {"input": "x" * 512}
    body = json.dumps(payload).encode("utf-8")
    compressed = zstd.ZstdCompressor().compress(body)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(
            "/echo",
            content=compressed,
            headers={"Content-Encoding": "zstd", "Content-Type": "application/json"},
        )

    assert resp.status_code == 413
    response_data = resp.json()
    assert response_data["error"]["code"] == "payload_too_large"


@pytest.mark.asyncio
async def test_request_decompression_propagates_client_disconnect():
    app = FastAPI()
    add_request_decompression_middleware(app)
    dispatch = cast(_Dispatch, app.user_middleware[0].kwargs["dispatch"])

    async def receive() -> dict[str, object]:
        return {"type": "http.disconnect"}

    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/echo",
            "raw_path": b"/echo",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"content-encoding", b"gzip"), (b"content-type", b"application/json")],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        },
        receive=receive,
    )

    async def call_next(_: Request):
        raise AssertionError("call_next should not run after client disconnect")

    with pytest.raises(ClientDisconnect):
        await dispatch(request, call_next)


@pytest.mark.asyncio
async def test_request_decompression_propagates_body_read_failures():
    app = FastAPI()
    add_request_decompression_middleware(app)
    dispatch = cast(_Dispatch, app.user_middleware[0].kwargs["dispatch"])

    async def receive() -> dict[str, object]:
        raise RuntimeError("receive failed")

    request = Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/echo",
            "raw_path": b"/echo",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"content-encoding", b"gzip"), (b"content-type", b"application/json")],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        },
        receive=receive,
    )

    async def call_next(_: Request):
        raise AssertionError("call_next should not run when body read fails")

    with pytest.raises(RuntimeError, match="receive failed"):
        await dispatch(request, call_next)

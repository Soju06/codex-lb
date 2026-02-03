from __future__ import annotations

import gzip
import json
import zlib

import pytest
import zstandard as zstd
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.core.middleware.request_decompression import add_request_decompression_middleware

pytestmark = pytest.mark.unit


def _build_echo_app(*, touch_headers: bool = False) -> FastAPI:
    app = FastAPI()
    add_request_decompression_middleware(app)

    if touch_headers:

        @app.middleware("http")
        async def touch_headers_middleware(request: Request, call_next):
            _ = request.headers.get("content-encoding")
            return await call_next(request)

    @app.post("/echo")
    async def echo(request: Request):
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

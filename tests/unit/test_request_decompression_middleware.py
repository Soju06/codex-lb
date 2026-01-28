from __future__ import annotations

import json

import pytest
import zstandard as zstd
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from app.core.middleware.request_decompression import add_request_decompression_middleware

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_request_decompression_clears_cached_headers():
    app = FastAPI()
    add_request_decompression_middleware(app)

    @app.middleware("http")
    async def touch_headers(request: Request, call_next):
        _ = request.headers.get("content-encoding")
        return await call_next(request)

    @app.post("/echo")
    async def echo(request: Request):
        data = await request.json()
        return {"content_encoding": request.headers.get("content-encoding"), "data": data}

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

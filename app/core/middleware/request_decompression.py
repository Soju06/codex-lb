from __future__ import annotations

import io
from collections.abc import Awaitable, Callable

import zstandard as zstd
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.core.errors import dashboard_error


def _replace_request_body(request: Request, body: bytes) -> None:
    request._body = body
    headers: list[tuple[bytes, bytes]] = []
    for key, value in request.scope.get("headers", []):
        if key.lower() in (b"content-encoding", b"content-length"):
            continue
        headers.append((key, value))
    headers.append((b"content-length", str(len(body)).encode("ascii")))
    request.scope["headers"] = headers


def add_request_decompression_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_decompression_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        content_encoding = request.headers.get("content-encoding")
        if not content_encoding:
            return await call_next(request)
        encodings = [enc.strip().lower() for enc in content_encoding.split(",") if enc.strip()]
        if encodings != ["zstd"]:
            return await call_next(request)
        body = await request.body()
        try:
            decompressed = zstd.ZstdDecompressor().decompress(body)
        except Exception:
            try:
                with zstd.ZstdDecompressor().stream_reader(io.BytesIO(body)) as reader:
                    decompressed = reader.read()
            except Exception:
                return JSONResponse(
                    status_code=400,
                    content=dashboard_error(
                        "invalid_request",
                        "Request body is zstd-compressed but could not be decompressed",
                    ),
                )
        _replace_request_body(request, decompressed)
        return await call_next(request)

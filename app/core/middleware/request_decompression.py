from __future__ import annotations

import io
from collections.abc import Awaitable, Callable
from typing import Protocol

import zstandard as zstd
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.core.config.settings import get_settings
from app.core.errors import dashboard_error


class _DecompressedBodyTooLarge(Exception):
    def __init__(self, max_size: int) -> None:
        super().__init__(f"Decompressed body exceeded {max_size} bytes")
        self.max_size = max_size


class _Readable(Protocol):
    def read(self, size: int = ...) -> bytes: ...


def _read_limited(reader: _Readable, max_size: int) -> bytes:
    buffer = bytearray()
    total = 0
    chunk_size = 64 * 1024
    while True:
        chunk = reader.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_size:
            raise _DecompressedBodyTooLarge(max_size)
        buffer.extend(chunk)
    return bytes(buffer)


def _replace_request_body(request: Request, body: bytes) -> None:
    request._body = body
    headers: list[tuple[bytes, bytes]] = []
    for key, value in request.scope.get("headers", []):
        if key.lower() in (b"content-encoding", b"content-length"):
            continue
        headers.append((key, value))
    headers.append((b"content-length", str(len(body)).encode("ascii")))
    request.scope["headers"] = headers
    # Ensure subsequent request.headers reflects the updated scope headers.
    request._headers = None


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
        settings = get_settings()
        max_size = settings.max_decompressed_body_bytes
        try:
            decompressed = zstd.ZstdDecompressor().decompress(body, max_output_size=max_size)
            if len(decompressed) > max_size:
                raise _DecompressedBodyTooLarge(max_size)
        except _DecompressedBodyTooLarge:
            return JSONResponse(
                status_code=413,
                content=dashboard_error(
                    "payload_too_large",
                    "Request body exceeds the maximum allowed size",
                ),
            )
        except Exception:
            try:
                with zstd.ZstdDecompressor().stream_reader(io.BytesIO(body)) as reader:
                    decompressed = _read_limited(reader, max_size)
            except _DecompressedBodyTooLarge:
                return JSONResponse(
                    status_code=413,
                    content=dashboard_error(
                        "payload_too_large",
                        "Request body exceeds the maximum allowed size",
                    ),
                )
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

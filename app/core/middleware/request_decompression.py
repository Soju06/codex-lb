from __future__ import annotations

import gzip
import io
import zlib
from collections.abc import Awaitable, Callable
from typing import Protocol

import zstandard as zstd
from fastapi import FastAPI, Request
from fastapi.responses import Response
from starlette._utils import get_route_path
from starlette.requests import ClientDisconnect

from app.core.middleware.request_body_limit import (
    REQUEST_BODY_TOO_LARGE_MESSAGE,
    request_body_limit_for_path,
    request_ingress_error_response,
)


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


def _decompress_gzip(data: bytes, max_size: int) -> bytes:
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as reader:
        return _read_limited(reader, max_size)


def _decompress_deflate(data: bytes, max_size: int) -> bytes:
    decompressor = zlib.decompressobj()
    buffer = bytearray()
    chunk_size = 64 * 1024
    for start in range(0, len(data), chunk_size):
        chunk = data[start : start + chunk_size]
        # Bound output growth to avoid oversized allocations.
        while chunk:
            remaining = max_size - len(buffer)
            decompressed = decompressor.decompress(chunk, max_length=remaining + 1)
            if len(decompressed) > remaining:
                raise _DecompressedBodyTooLarge(max_size)
            buffer.extend(decompressed)
            chunk = decompressor.unconsumed_tail
    while True:
        remaining = max_size - len(buffer)
        drained = decompressor.decompress(b"", max_length=remaining + 1)
        if len(drained) > remaining:
            raise _DecompressedBodyTooLarge(max_size)
        if not drained:
            break
        buffer.extend(drained)
    if not decompressor.eof:
        raise zlib.error("Incomplete deflate stream")
    return bytes(buffer)


def _decompress_zstd(data: bytes, max_size: int) -> bytes:
    try:
        decompressed = zstd.ZstdDecompressor().decompress(data, max_output_size=max_size)
        if len(decompressed) > max_size:
            raise _DecompressedBodyTooLarge(max_size)
        return decompressed
    except _DecompressedBodyTooLarge:
        raise
    except Exception:
        with zstd.ZstdDecompressor().stream_reader(io.BytesIO(data)) as reader:
            return _read_limited(reader, max_size)


def _decompress_body(data: bytes, encodings: list[str], max_size: int) -> bytes:
    supported = {"zstd", "gzip", "deflate", "identity"}
    if any(encoding not in supported for encoding in encodings):
        raise ValueError("Unsupported content-encoding")
    result = data
    for encoding in reversed(encodings):
        if encoding == "zstd":
            result = _decompress_zstd(result, max_size)
        elif encoding == "gzip":
            result = _decompress_gzip(result, max_size)
        elif encoding == "deflate":
            result = _decompress_deflate(result, max_size)
        elif encoding == "identity":
            pass
        if len(result) > max_size:
            raise _DecompressedBodyTooLarge(max_size)
    return result


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
    request.__dict__.pop("_headers", None)


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
        if not encodings:
            return await call_next(request)
        max_size = request_body_limit_for_path(get_route_path(request.scope))
        try:
            body = await request.body()
        except ClientDisconnect:
            raise
        try:
            decompressed = _decompress_body(body, encodings, max_size)
        except _DecompressedBodyTooLarge:
            return request_ingress_error_response(
                request,
                status_code=413,
                code="payload_too_large",
                message=REQUEST_BODY_TOO_LARGE_MESSAGE,
            )
        except ValueError:
            return request_ingress_error_response(
                request,
                status_code=400,
                code="invalid_request",
                message="Unsupported Content-Encoding",
            )
        except Exception:
            return request_ingress_error_response(
                request,
                status_code=400,
                code="invalid_request",
                message="Request body is compressed but could not be decompressed",
            )
        _replace_request_body(request, decompressed)
        return await call_next(request)

from __future__ import annotations

import gzip
import io
import zlib
from typing import Protocol

import zstandard as zstd
from fastapi import FastAPI
from starlette._utils import get_route_path
from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect, Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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


def _rewrite_scope_headers_for_body(scope: Scope, body_length: int) -> None:
    """Drop content-encoding/content-length and declare the decompressed length."""
    headers: list[tuple[bytes, bytes]] = []
    for key, value in scope.get("headers", []):
        if key.lower() in (b"content-encoding", b"content-length"):
            continue
        headers.append((key, value))
    headers.append((b"content-length", str(body_length).encode("ascii")))
    scope["headers"] = headers


async def _drain_request_body(receive: Receive) -> bytes:
    """Read the full request body from ``receive``.

    Mirrors ``Request.body()`` semantics: a mid-body ``http.disconnect`` raises
    ``ClientDisconnect``, and receive failures propagate. The caller's
    ``receive`` is the body-limit middleware's limited receive, so the wire-size
    cap (``_RequestBodyTooLarge``) propagates through this drain unchanged.
    """
    chunks = bytearray()
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            raise ClientDisconnect()
        chunks.extend(message.get("body", b""))
        if not message.get("more_body", False):
            return bytes(chunks)


class RequestDecompressionMiddleware:
    """Decompress zstd/gzip/deflate request bodies with per-layer decode budgets.

    Pure ASGI replacement for the previous ``BaseHTTPMiddleware`` dispatch:
    requests without ``Content-Encoding`` (the common case) pass straight
    through. Encoded requests are drained through the upstream receive (the
    body-limit middleware's limited receive, so the wire-size cap still applies
    to the compressed bytes), decompressed under the per-path budget, and the
    downstream app is given a replay receive that yields the decompressed body
    once and then delegates to the original receive so ``http.disconnect`` is
    still observed (this replaces the ``_CachedRequest`` body replay the
    BaseHTTP wrapper used to provide).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_encoding = Headers(scope=scope).get("content-encoding")
        if not content_encoding:
            await self.app(scope, receive, send)
            return
        encodings = [enc.strip().lower() for enc in content_encoding.split(",") if enc.strip()]
        if not encodings:
            await self.app(scope, receive, send)
            return

        max_size = request_body_limit_for_path(get_route_path(scope))
        body = await _drain_request_body(receive)
        try:
            decompressed = _decompress_body(body, encodings, max_size)
        except _DecompressedBodyTooLarge:
            response = request_ingress_error_response(
                Request(scope),
                status_code=413,
                code="payload_too_large",
                message=REQUEST_BODY_TOO_LARGE_MESSAGE,
            )
            await response(scope, receive, send)
            return
        except ValueError:
            response = request_ingress_error_response(
                Request(scope),
                status_code=400,
                code="invalid_request",
                message="Unsupported Content-Encoding",
            )
            await response(scope, receive, send)
            return
        except Exception:
            response = request_ingress_error_response(
                Request(scope),
                status_code=400,
                code="invalid_request",
                message="Request body is compressed but could not be decompressed",
            )
            await response(scope, receive, send)
            return

        _rewrite_scope_headers_for_body(scope, len(decompressed))
        body_replayed = False

        async def replay_receive() -> Message:
            nonlocal body_replayed
            if not body_replayed:
                body_replayed = True
                return {"type": "http.request", "body": decompressed, "more_body": False}
            return await receive()

        await self.app(scope, replay_receive, send)


def add_request_decompression_middleware(app: FastAPI) -> None:
    app.add_middleware(RequestDecompressionMiddleware)

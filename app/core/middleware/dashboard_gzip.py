from __future__ import annotations

from starlette.middleware.gzip import GZipMiddleware
from starlette.types import ASGIApp, Receive, Scope, Send

# Only dashboard-facing paths are compressed. Proxy paths (/backend-api,
# /v1, websockets) stream SSE and must never pass through a compressing
# wrapper; dashboard API responses are JSON and the built SPA assets are
# text-heavy (the uncompressed JS bundle alone is ~1.7 MB).
_COMPRESSED_PATH_PREFIXES = ("/api/", "/assets/")


class DashboardGZipMiddleware:
    """Apply gzip to dashboard API and static-asset responses only."""

    def __init__(self, app: ASGIApp, minimum_size: int = 1024) -> None:
        self._plain = app
        self._gzip = GZipMiddleware(app, minimum_size=minimum_size)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] == "http"
            and scope.get("path", "").startswith(_COMPRESSED_PATH_PREFIXES)
            and not _has_range_header(scope)
        ):
            await self._gzip(scope, receive, send)
            return
        await self._plain(scope, receive, send)


def _has_range_header(scope: Scope) -> bool:
    """Ranged requests bypass gzip: FileResponse builds the 206 and its
    Content-Range against the uncompressed file, so compressing the body
    afterwards would describe offsets the encoded bytes no longer match.
    Header-name casing is normalized because not every ASGI server
    lowercases request header names."""
    return any(name.lower() == b"range" for name, _ in scope.get("headers", ()))


def add_dashboard_gzip_middleware(app) -> None:
    app.add_middleware(DashboardGZipMiddleware)

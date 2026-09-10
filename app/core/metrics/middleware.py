from __future__ import annotations

import time

from starlette._utils import get_route_path
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    active_connections,
    request_duration_seconds,
    requests_total,
)

_SUPPORTED_METHODS = frozenset(("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"))


def _normalize_path(path: str) -> str:
    if path.startswith("/v1/"):
        return "/v1/..."
    if path.startswith("/api/"):
        return "/api/..."
    if path.startswith("/health/"):
        return "/health/..."
    if path == "/health":
        return path
    if path.startswith("/backend-api/"):
        return "/backend-api/..."
    if path.startswith("/internal/"):
        return "/internal/..."
    return "/other"


def _normalize_method(method: str) -> str:
    return method if method in _SUPPORTED_METHODS else "OTHER"


class MetricsMiddleware:
    def __init__(self, app: ASGIApp, *, enabled: bool = True) -> None:
        self.app = app
        self.enabled = enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.enabled or not PROMETHEUS_AVAILABLE:
            await self.app(scope, receive, send)
            return

        assert active_connections is not None
        assert requests_total is not None
        assert request_duration_seconds is not None

        start = time.monotonic()
        status_code = 500
        method = _normalize_method(scope.get("method", "GET"))
        path = _normalize_path(get_route_path(scope))

        active_connections.inc()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration = time.monotonic() - start
            requests_total.labels(method=method, path=path, status=str(status_code)).inc()
            request_duration_seconds.labels(method=method, path=path).observe(duration)
            active_connections.dec()


__all__ = ["MetricsMiddleware", "_normalize_path"]

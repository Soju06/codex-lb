from __future__ import annotations

import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.metrics.prometheus import (
    PROMETHEUS_AVAILABLE,
    active_connections,
    request_duration_seconds,
    requests_total,
)


def _normalize_path(path: str) -> str:
    if path.startswith("/v1/"):
        return "/v1/..."
    if path.startswith("/api/"):
        return "/api/..."
    if path.startswith("/health/"):
        return "/health/..."
    if len(path) > 50:
        return path[:50]
    return path or "/"


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
        method = scope.get("method", "GET")
        path = _normalize_path(scope.get("path", "/"))

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

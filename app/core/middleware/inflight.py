from __future__ import annotations

from importlib import import_module

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_DRAIN_ALLOWED_HTTP_PATHS = frozenset(
    {
        "/health/live",
        "/internal/bridge/responses",
    }
)


class InFlightMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Graceful drain waits for finite HTTP request lifetimes only. Long-lived
        # websocket sessions are handled independently and must not pin drain.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        shutdown_state = import_module("app.core.shutdown")

        # Return 503 when draining, except for health checks
        path = scope.get("path", "")
        if shutdown_state.is_draining() and path not in _DRAIN_ALLOWED_HTTP_PATHS:
            response = JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "type": "service_unavailable",
                        "message": "Server is draining",
                    }
                },
            )
            await response(scope, receive, send)
            return

        shutdown_state.increment_in_flight()
        try:
            await self.app(scope, receive, send)
        finally:
            shutdown_state.decrement_in_flight()

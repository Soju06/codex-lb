from __future__ import annotations

from importlib import import_module
from types import ModuleType

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_DRAIN_ALLOWED_HTTP_PATHS = frozenset(
    {
        "/health/live",
        "/internal/drain/start",
        "/internal/drain/stop",
        "/internal/drain/status",
        "/internal/bridge/responses",
    }
)

_IN_FLIGHT_EXCLUDED_HTTP_PATHS = frozenset(
    {
        "/health/live",
        "/health/ready",
        "/health/startup",
        "/internal/drain/start",
        "/internal/drain/stop",
        "/internal/drain/status",
    }
)

_IN_FLIGHT_WEBSOCKET_PATHS = frozenset(
    {
        "/backend-api/codex/responses",
        "/v1/responses",
    }
)


class InFlightMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        # Resolved lazily on the first request (import-cycle safety) and cached
        # so the hot path skips the per-request sys.modules lookup.
        self._shutdown_state: ModuleType | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope["type"]
        if scope_type not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        shutdown_state = self._shutdown_state
        if shutdown_state is None:
            shutdown_state = self._shutdown_state = import_module("app.core.shutdown")

        if scope_type == "websocket":
            # Register before checking the drain barrier. A synchronous signal
            # can interleave anywhere between Python bytecodes; increment-first
            # guarantees that the scope is either rejected and unregistered or
            # already visible to shutdown as pre-barrier work.
            shutdown_state.increment_in_flight()
            if shutdown_state.is_draining():
                shutdown_state.decrement_in_flight()
                await send({"type": "websocket.close", "code": 1013, "reason": "Server is draining"})
                return

            if scope.get("path", "") not in _IN_FLIGHT_WEBSOCKET_PATHS:
                # Admission is global, but only Responses sockets participate
                # in turn-aware drain. Other long-lived protocols are closed by
                # Uvicorn after the pre-connection drain barrier.
                shutdown_state.decrement_in_flight()
                await self.app(scope, receive, send)
                return

            try:
                await self.app(scope, receive, send)
            finally:
                shutdown_state.decrement_in_flight()
            return

        path = scope.get("path", "")
        track_in_flight = path not in _IN_FLIGHT_EXCLUDED_HTTP_PATHS
        if track_in_flight:
            # As with Responses WebSockets, register before checking the
            # barrier so a synchronous signal cannot admit invisible work.
            shutdown_state.increment_in_flight()
        try:
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
            await self.app(scope, receive, send)
        finally:
            if track_in_flight:
                shutdown_state.decrement_in_flight()

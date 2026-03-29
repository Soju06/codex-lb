from __future__ import annotations

from asyncio import Semaphore

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.resilience.memory_monitor import is_memory_pressure


class BulkheadSemaphore:
    def __init__(self, proxy_limit: int = 200, dashboard_limit: int = 50, background_limit: int = 10) -> None:
        self._proxy = Semaphore(proxy_limit) if proxy_limit > 0 else None
        self._dashboard = Semaphore(dashboard_limit) if dashboard_limit > 0 else None
        self._background = Semaphore(background_limit) if background_limit > 0 else None

    def get_semaphore(self, path: str) -> Semaphore | None:
        if path.startswith("/v1/") or path.startswith("/backend-api/"):
            return self._proxy
        if path.startswith("/api/") or path.startswith("/health/"):
            return self._dashboard
        return self._proxy


class BulkheadMiddleware:
    def __init__(self, app: ASGIApp, *, bulkhead: BulkheadSemaphore) -> None:
        self.app = app
        self._bulkhead = bulkhead

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        if path.startswith("/health"):
            await self.app(scope, receive, send)
            return

        if is_memory_pressure():
            body = b'{"detail":"Service temporarily unavailable (memory pressure)"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 503,
                    "headers": [(b"retry-after", b"5"), (b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        sem = self._bulkhead.get_semaphore(path)
        if sem is None:
            await self.app(scope, receive, send)
            return

        if sem._value <= 0:
            body = b'{"detail":"Service temporarily unavailable (bulkhead full)"}'
            await send(
                {
                    "type": "http.response.start",
                    "status": 503,
                    "headers": [(b"retry-after", b"5"), (b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        await sem.acquire()

        try:
            await self.app(scope, receive, send)
        finally:
            sem.release()


_bulkhead: BulkheadSemaphore | None = None


def get_bulkhead(proxy_limit: int = 200, dashboard_limit: int = 50) -> BulkheadSemaphore:
    global _bulkhead
    if _bulkhead is None:
        _bulkhead = BulkheadSemaphore(proxy_limit=proxy_limit, dashboard_limit=dashboard_limit)
    return _bulkhead


__all__ = ["BulkheadMiddleware", "BulkheadSemaphore", "get_bulkhead"]

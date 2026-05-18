from __future__ import annotations

import asyncio

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.resilience.overload import (
    deny_websocket_with_http_response,
    is_proxy_path,
    local_overload_error,
    merge_retry_after_headers,
    send_json_http_response,
)


class BackpressureMiddleware:
    def __init__(self, app: ASGIApp, *, max_concurrent: int) -> None:
        self.app = app
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path.startswith("/health"):
            await self.app(scope, receive, send)
            return

        if self._semaphore.locked():
            message = "codex-lb is temporarily overloaded by local backpressure"
            if scope["type"] == "websocket":
                await deny_websocket_with_http_response(
                    receive,
                    send,
                    status_code=429,
                    payload=local_overload_error(message) if is_proxy_path(path) else {"detail": message},
                    headers=merge_retry_after_headers(),
                )
                return
            await send_json_http_response(
                send,
                status_code=429,
                payload=local_overload_error(message) if is_proxy_path(path) else {"detail": message},
                headers=merge_retry_after_headers(),
            )
            return

        await self._semaphore.acquire()
        try:
            await self.app(scope, receive, send)
        finally:
            self._semaphore.release()


__all__ = ["BackpressureMiddleware"]

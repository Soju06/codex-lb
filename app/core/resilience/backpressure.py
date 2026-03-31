from __future__ import annotations

import asyncio

from starlette.types import ASGIApp, Receive, Scope, Send


async def _reject_websocket(receive: Receive, send: Send, *, reason: str) -> None:
    try:
        event = await receive()
        if event.get("type") != "websocket.connect":
            return
    except Exception:
        return
    await send({"type": "websocket.close", "code": 1013, "reason": reason})


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

        if self._semaphore._value <= 0:
            if scope["type"] == "websocket":
                await _reject_websocket(receive, send, reason="Too Many Requests")
                return
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [(b"retry-after", b"5"), (b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": b'{"detail":"Too Many Requests"}'})
            return

        await self._semaphore.acquire()
        try:
            await self.app(scope, receive, send)
        finally:
            self._semaphore.release()


__all__ = ["BackpressureMiddleware"]

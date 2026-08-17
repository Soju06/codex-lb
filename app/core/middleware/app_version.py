from __future__ import annotations

from fastapi import FastAPI
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import __version__


class AppVersionMiddleware:
    """Append ``X-App-Version`` to every 200-499 HTTP response.

    Pure ASGI (no ``BaseHTTPMiddleware``) so streaming responses relay chunks
    without an extra task and memory-stream hop. 5xx responses and WebSocket
    scopes never carry the header, and a route-owned ``X-App-Version`` value is
    preserved (see ``openspec/specs/api-response-metadata/spec.md``).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_app_version(message: Message) -> None:
            if message["type"] == "http.response.start" and 200 <= message["status"] < 500:
                headers = MutableHeaders(raw=message.setdefault("headers", []))
                headers.setdefault("X-App-Version", __version__)
            await send(message)

        await self.app(scope, receive, send_with_app_version)


def add_app_version_middleware(app: FastAPI) -> None:
    app.add_middleware(AppVersionMiddleware)

from __future__ import annotations

import os
from typing import cast

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send
from uvicorn._types import ASGI3Application
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.core.socket_peer import _capture_raw_socket_peer


class TrustedProxyHeadersMiddleware:
    """Preserve the raw socket peer, then apply Uvicorn's proxy projection."""

    def __init__(self, app: ASGIApp) -> None:
        self._proxy_headers = cast(
            ASGIApp,
            ProxyHeadersMiddleware(
                cast(ASGI3Application, app),
                trusted_hosts=os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1"),
            ),
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in {"http", "websocket"}:
            _capture_raw_socket_peer(scope)
        await self._proxy_headers(scope, receive, send)


def add_trusted_proxy_headers_middleware(app: FastAPI) -> None:
    app.add_middleware(TrustedProxyHeadersMiddleware)

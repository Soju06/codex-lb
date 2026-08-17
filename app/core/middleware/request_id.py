from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.utils.request_id import (
    clear_request_id,
    clear_request_scope_id,
    reset_request_id,
    reset_request_scope_id,
    set_request_id,
    set_request_scope_id,
)


def _inbound_request_id(scope: Scope) -> str | None:
    """Return the first ``x-request-id`` value, else the first ``request-id`` value."""
    x_request_id: bytes | None = None
    request_id: bytes | None = None
    for name, value in scope.get("headers", []):
        lowered = name.lower()
        if x_request_id is None and lowered == b"x-request-id":
            x_request_id = value
        elif request_id is None and lowered == b"request-id":
            request_id = value
    inbound = x_request_id or request_id
    if not inbound:
        return None
    return inbound.decode("latin-1")


class RequestIdMiddleware:
    """Bind request-id contextvars around the request and echo ``x-request-id``.

    Pure ASGI: the contextvars are set and reset in the same task that runs the
    downstream app, so they stay visible for the whole response stream (with
    ``BaseHTTPMiddleware`` they were reset when the dispatch returned, before
    the body finished streaming).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _inbound_request_id(scope) or str(uuid4())
        request_id_token = set_request_id(request_id)
        request_scope_token = set_request_scope_id(str(uuid4()))

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message.setdefault("headers", []))
                headers.setdefault("x-request-id", request_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            reset_request_scope_id(request_scope_token)
            reset_request_id(request_id_token)
            clear_request_scope_id()
            clear_request_id()


def add_request_id_middleware(app: FastAPI) -> None:
    app.add_middleware(RequestIdMiddleware)

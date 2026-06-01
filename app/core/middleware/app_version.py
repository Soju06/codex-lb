from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import Response

from app import __version__


def add_app_version_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def app_version_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        if 200 <= response.status_code < 500:
            response.headers.setdefault("X-App-Version", __version__)
        return response

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.utils.request_id import reset_request_id, set_request_id


def add_request_id_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[JSONResponse]],
    ) -> JSONResponse:
        inbound_request_id = request.headers.get("x-request-id") or request.headers.get("request-id")
        request_id = inbound_request_id or str(uuid4())
        token = set_request_id(request_id)
        try:
            response = await call_next(request)
        except Exception:
            reset_request_id(token)
            raise
        response.headers.setdefault("x-request-id", request_id)
        return response

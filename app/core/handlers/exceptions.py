from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import dashboard_error


def add_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> Response:
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=422,
                content=dashboard_error("validation_error", "Invalid request payload"),
            )
        return await request_validation_exception_handler(request, exc)

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> Response:
        if request.url.path.startswith("/api/"):
            detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
            return JSONResponse(
                status_code=exc.status_code,
                content=dashboard_error(f"http_{exc.status_code}", detail),
            )
        return await http_exception_handler(request, exc)

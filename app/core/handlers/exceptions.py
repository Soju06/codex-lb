from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import dashboard_error, openai_error
from app.core.utils.json_guards import is_json_mapping


def _detail_text(detail: object) -> str:
    if isinstance(detail, str):
        stripped = detail.strip()
        return stripped or "Request failed"
    if is_json_mapping(detail):
        message = detail.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        error = detail.get("error")
        if isinstance(error, str) and error.strip():
            return error.strip()
    return "Request failed"


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
        if request.url.path.startswith("/v1/"):
            error = openai_error("invalid_request_error", "Invalid request payload", error_type="invalid_request_error")
            if exc.errors():
                first = exc.errors()[0]
                loc = first.get("loc", [])
                if isinstance(loc, (list, tuple)):
                    param = ".".join(str(part) for part in loc if part != "body")
                    if param:
                        error["error"]["param"] = param
            return JSONResponse(status_code=400, content=error)
        return await request_validation_exception_handler(request, exc)

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> Response:
        if request.url.path.startswith("/api/"):
            detail = _detail_text(exc.detail)
            return JSONResponse(
                status_code=exc.status_code,
                content=dashboard_error(f"http_{exc.status_code}", detail),
                headers=exc.headers,
            )
        if request.url.path.startswith("/v1/"):
            detail = _detail_text(exc.detail)
            error_type = "invalid_request_error"
            code = "invalid_request_error"
            if exc.status_code == 401:
                error_type = "authentication_error"
                code = "invalid_api_key"
            elif exc.status_code == 403:
                error_type = "permission_error"
                code = "insufficient_permissions"
            elif exc.status_code == 404:
                error_type = "invalid_request_error"
                code = "not_found"
            elif exc.status_code == 429:
                error_type = "rate_limit_error"
                code = "rate_limit_exceeded"
            elif exc.status_code >= 500:
                error_type = "server_error"
                code = "server_error"
            return JSONResponse(
                status_code=exc.status_code,
                content=openai_error(code, detail, error_type=error_type),
                headers=exc.headers,
            )
        return await http_exception_handler(request, exc)

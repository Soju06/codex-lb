from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import anthropic_error, dashboard_error, openai_error
from app.core.exceptions import (
    AppError,
    DashboardAuthError,
    DashboardBadRequestError,
    DashboardConflictError,
    DashboardNotFoundError,
    DashboardRateLimitError,
    DashboardValidationError,
    ProxyAuthError,
    ProxyModelNotAllowed,
    ProxyRateLimitError,
    ProxyUpstreamError,
)

logger = logging.getLogger(__name__)

_OPENAI_EXCEPTION_TYPES: tuple[type[AppError], ...] = (
    ProxyAuthError,
    ProxyModelNotAllowed,
    ProxyRateLimitError,
    ProxyUpstreamError,
)

_DASHBOARD_EXCEPTION_TYPES: tuple[type[AppError], ...] = (
    DashboardAuthError,
    DashboardNotFoundError,
    DashboardConflictError,
    DashboardBadRequestError,
    DashboardValidationError,
    DashboardRateLimitError,
)


def _error_format(request: Request) -> str | None:
    fmt = getattr(request.state, "error_format", None)
    if fmt is not None:
        return fmt
    # Fallback for unmatched routes (e.g. SPA fallback 404s)
    path = request.url.path
    if path.startswith("/api/"):
        return "dashboard"
    if path.startswith("/anthropic/"):
        return "anthropic"
    if path.startswith("/v1/") or path.startswith("/backend-api/"):
        return "openai"
    return None


def add_exception_handlers(app: FastAPI) -> None:
    # --- Domain exceptions: OpenAI envelope ---

    for exc_cls in _OPENAI_EXCEPTION_TYPES:

        @app.exception_handler(exc_cls)
        async def _openai_domain_handler(request: Request, exc: AppError) -> JSONResponse:
            error_type = getattr(exc, "error_type", "server_error")
            return JSONResponse(
                status_code=exc.status_code,
                content=openai_error(exc.code, exc.message, error_type=error_type),
            )

    # --- Domain exceptions: Dashboard envelope ---

    for exc_cls in _DASHBOARD_EXCEPTION_TYPES:

        @app.exception_handler(exc_cls)
        async def _dashboard_domain_handler(request: Request, exc: AppError) -> JSONResponse:
            headers: dict[str, str] | None = None
            if isinstance(exc, DashboardRateLimitError):
                headers = {"Retry-After": str(exc.retry_after)}
            return JSONResponse(
                status_code=exc.status_code,
                content=dashboard_error(exc.code, exc.message),
                headers=headers,
            )

    # --- Framework exceptions: format based on router marker ---

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> Response:
        fmt = _error_format(request)
        if fmt == "dashboard":
            return JSONResponse(
                status_code=422,
                content=dashboard_error("validation_error", "Invalid request payload"),
            )
        if fmt == "anthropic":
            return JSONResponse(
                status_code=400,
                content=anthropic_error("invalid_request_error", "Invalid request payload"),
            )
        if fmt == "openai":
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
        fmt = _error_format(request)
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
        if fmt == "dashboard":
            return JSONResponse(
                status_code=exc.status_code,
                content=dashboard_error(f"http_{exc.status_code}", detail),
            )
        if fmt == "anthropic":
            error_type = "invalid_request_error"
            if exc.status_code == 401:
                error_type = "authentication_error"
            elif exc.status_code == 403:
                error_type = "permission_error"
            elif exc.status_code == 429:
                error_type = "rate_limit_error"
            elif exc.status_code >= 500:
                error_type = "api_error"
            return JSONResponse(status_code=exc.status_code, content=anthropic_error(error_type, detail))
        if fmt == "openai":
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
            return JSONResponse(status_code=exc.status_code, content=openai_error(code, detail, error_type=error_type))
        return await http_exception_handler(request, exc)

    # --- Catch-all for unhandled exceptions ---

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        fmt = _error_format(request)
        if fmt == "dashboard":
            return JSONResponse(
                status_code=500,
                content=dashboard_error("internal_error", "Unexpected error"),
            )
        if fmt == "anthropic":
            return JSONResponse(
                status_code=500,
                content=anthropic_error("api_error", "Internal server error"),
            )
        if fmt == "openai":
            return JSONResponse(
                status_code=500,
                content=openai_error("server_error", "Internal server error", error_type="server_error"),
            )
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})

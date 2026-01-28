from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.core.errors import dashboard_error
from app.core.utils.request_id import get_request_id

logger = logging.getLogger(__name__)


def add_api_unhandled_error_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def api_unhandled_error_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        try:
            return await call_next(request)
        except Exception:
            if request.url.path.startswith("/api/"):
                logger.exception(
                    "Unhandled API error request_id=%s",
                    get_request_id(),
                )
                return JSONResponse(
                    status_code=500,
                    content=dashboard_error("internal_error", "Unexpected error"),
                )
            raise

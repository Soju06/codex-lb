from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.clients.http import close_http_client, init_http_client
from app.core.errors import dashboard_error
from app.core.utils.request_id import get_request_id, reset_request_id, set_request_id
from app.db.session import init_db
from app.modules.accounts import api as accounts_api
from app.modules.health import api as health_api
from app.modules.oauth import api as oauth_api
from app.modules.proxy import api as proxy_api
from app.modules.request_logs import api as request_logs_api
from app.modules.usage import api as usage_api

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    await init_http_client()

    try:
        yield
    finally:
        await close_http_client()


def create_app() -> FastAPI:
    app = FastAPI(title="codex-lb", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next) -> JSONResponse:
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

    @app.middleware("http")
    async def api_unhandled_error_middleware(request: Request, call_next) -> JSONResponse:
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

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=422,
                content=dashboard_error("validation_error", "Invalid request payload"),
            )
        return await request_validation_exception_handler(request, exc)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        if request.url.path.startswith("/api/"):
            detail = exc.detail if isinstance(exc.detail, str) else "Request failed"
            return JSONResponse(
                status_code=exc.status_code,
                content=dashboard_error(f"http_{exc.status_code}", detail),
            )
        return await http_exception_handler(request, exc)

    app.include_router(proxy_api.router)
    app.include_router(proxy_api.usage_router)
    app.include_router(accounts_api.router)
    app.include_router(usage_api.router)
    app.include_router(request_logs_api.router)
    app.include_router(oauth_api.router)
    app.include_router(health_api.router)

    static_dir = Path(__file__).parent / "static"
    index_html = static_dir / "index.html"

    @app.get("/", include_in_schema=False)
    async def root_redirect():
        return RedirectResponse(url="/dashboard", status_code=302)

    @app.get("/accounts", include_in_schema=False)
    async def spa_accounts():
        return FileResponse(index_html, media_type="text/html")

    app.mount("/dashboard", StaticFiles(directory=static_dir, html=True), name="dashboard")

    return app


app = create_app()

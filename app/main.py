from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.core.clients.http import close_http_client, init_http_client
from app.core.handlers import add_exception_handlers
from app.core.middleware import (
    add_api_unhandled_error_middleware,
    add_request_decompression_middleware,
    add_request_id_middleware,
)
from app.db.session import close_db, init_db
from app.modules.accounts import api as accounts_api
from app.modules.health import api as health_api
from app.modules.oauth import api as oauth_api
from app.modules.proxy import api as proxy_api
from app.modules.request_logs import api as request_logs_api
from app.modules.settings import api as settings_api
from app.modules.usage import api as usage_api


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    await init_http_client()

    try:
        yield
    finally:
        try:
            await close_http_client()
        finally:
            await close_db()


def create_app() -> FastAPI:
    app = FastAPI(title="codex-lb", version="0.1.0", lifespan=lifespan)

    add_request_decompression_middleware(app)
    add_request_id_middleware(app)
    add_api_unhandled_error_middleware(app)
    add_exception_handlers(app)

    app.include_router(proxy_api.router)
    app.include_router(proxy_api.v1_router)
    app.include_router(proxy_api.usage_router)
    app.include_router(accounts_api.router)
    app.include_router(usage_api.router)
    app.include_router(request_logs_api.router)
    app.include_router(oauth_api.router)
    app.include_router(settings_api.router)
    app.include_router(health_api.router)

    static_dir = Path(__file__).parent / "static"
    index_html = static_dir / "index.html"

    @app.get("/", include_in_schema=False)
    async def root_redirect():
        return RedirectResponse(url="/dashboard", status_code=302)

    @app.get("/accounts", include_in_schema=False)
    async def spa_accounts():
        return FileResponse(index_html, media_type="text/html")

    @app.get("/settings", include_in_schema=False)
    async def spa_settings():
        return FileResponse(index_html, media_type="text/html")

    app.mount("/dashboard", StaticFiles(directory=static_dir, html=True), name="dashboard")

    return app


app = create_app()

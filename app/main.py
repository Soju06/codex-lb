from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from app.core.clients.http import close_http_client, init_http_client
from app.core.config.settings_cache import get_settings_cache
from app.core.handlers import add_exception_handlers
from app.core.middleware import (
    add_request_decompression_middleware,
    add_request_id_middleware,
)
from app.core.openai.model_refresh_scheduler import build_model_refresh_scheduler
from app.core.usage.refresh_scheduler import build_usage_refresh_scheduler
from app.db.session import close_db, init_db
from app.modules.accounts import api as accounts_api
from app.modules.anthropic import api as anthropic_api
from app.modules.api_keys import api as api_keys_api
from app.modules.dashboard import api as dashboard_api
from app.modules.dashboard_auth import api as dashboard_auth_api
from app.modules.health import api as health_api
from app.modules.oauth import api as oauth_api
from app.modules.proxy import api as proxy_api
from app.modules.proxy.rate_limit_cache import get_rate_limit_headers_cache
from app.modules.request_logs import api as request_logs_api
from app.modules.settings import api as settings_api
from app.modules.usage import api as usage_api


_ROOT_LANDING_HTML = """<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"UTF-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
    <title>codex-lb</title>
    <style>
      :root { color-scheme: dark; }
      body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif; background: #0b1020; color: #dbe2f0; }
      main { max-width: 760px; margin: 8vh auto; padding: 0 24px; }
      h1 { margin: 0 0 12px; font-size: 30px; }
      p { margin: 8px 0; color: #9fb0cc; line-height: 1.5; }
      ul { margin-top: 16px; }
      a { color: #7fb4ff; }
      code { background: #131b2f; border: 1px solid #25304c; border-radius: 6px; padding: 1px 6px; }
    </style>
  </head>
  <body>
    <main>
      <h1>codex-lb endpoint online</h1>
      <p>This root page is intentionally minimal for desktop custom deployment clients.</p>
      <ul>
        <li>Dashboard: <a href=\"/dashboard\">/dashboard</a></li>
        <li>Health: <a href=\"/health\">/health</a></li>
        <li>Docs: <a href=\"/docs\">/docs</a></li>
      </ul>
    </main>
  </body>
</html>
"""


@asynccontextmanager
async def lifespan(_: FastAPI):
    await get_settings_cache().invalidate()
    await get_rate_limit_headers_cache().invalidate()
    await init_db()
    await init_http_client()
    usage_scheduler = build_usage_refresh_scheduler()
    model_scheduler = build_model_refresh_scheduler()
    await usage_scheduler.start()
    await model_scheduler.start()

    try:
        yield
    finally:
        await model_scheduler.stop()
        await usage_scheduler.stop()
        try:
            await close_http_client()
        finally:
            await close_db()


def create_app() -> FastAPI:
    app = FastAPI(
        title="codex-lb",
        version="0.1.0",
        lifespan=lifespan,
        swagger_ui_parameters={"persistAuthorization": True},
    )

    add_request_decompression_middleware(app)
    add_request_id_middleware(app)
    add_exception_handlers(app)

    app.include_router(proxy_api.router)
    app.include_router(proxy_api.v1_router)
    app.include_router(anthropic_api.router)
    app.include_router(anthropic_api.api_router)
    app.include_router(anthropic_api.desktop_router)
    app.include_router(proxy_api.usage_router)
    app.include_router(accounts_api.router)
    app.include_router(dashboard_api.router)
    app.include_router(usage_api.router)
    app.include_router(request_logs_api.router)
    app.include_router(oauth_api.router)
    app.include_router(dashboard_auth_api.router)
    app.include_router(settings_api.router)
    app.include_router(api_keys_api.router)
    app.include_router(health_api.router)

    static_dir = Path(__file__).parent / "static"
    index_html = static_dir / "index.html"
    static_root = static_dir.resolve()
    frontend_build_hint = "Frontend assets are missing. Run `cd frontend && bun run build`."
    excluded_prefixes = ("api/", "v1/", "backend-api/", "health")

    def _is_static_asset_path(path: str) -> bool:
        if path.startswith("assets/"):
            return True
        last_segment = path.rsplit("/", maxsplit=1)[-1]
        return "." in last_segment

    @app.get("/", include_in_schema=False)
    @app.get("/{path:path}", include_in_schema=False)
    async def spa_fallback(request: Request, path: str = ""):
        normalized = path.lstrip("/")
        if not normalized:
            return HTMLResponse(_ROOT_LANDING_HTML, media_type="text/html")

        if normalized and any(
            normalized == prefix.rstrip("/") or normalized.startswith(prefix) for prefix in excluded_prefixes
        ):
            raise HTTPException(status_code=404, detail="Not Found")

        if normalized:
            candidate = (static_dir / normalized).resolve()
            if candidate.is_relative_to(static_root) and candidate.is_file():
                return FileResponse(candidate)
            if _is_static_asset_path(normalized):
                raise HTTPException(status_code=404, detail="Not Found")

        if not index_html.is_file():
            raise HTTPException(status_code=503, detail=frontend_build_hint)

        return FileResponse(index_html, media_type="text/html")

    return app


app = create_app()

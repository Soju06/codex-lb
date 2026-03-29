from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from importlib import import_module
from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.responses import Response

from app.core.clients.http import close_http_client, init_http_client
from app.core.config.settings import get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.handlers import add_exception_handlers
from app.core.metrics.middleware import MetricsMiddleware
from app.core.metrics.prometheus import PROMETHEUS_AVAILABLE
from app.core.middleware import (
    add_api_firewall_middleware,
    add_request_decompression_middleware,
    add_request_id_middleware,
)
from app.core.openai.model_refresh_scheduler import build_model_refresh_scheduler
from app.core.resilience.backpressure import BackpressureMiddleware
from app.core.usage.refresh_scheduler import build_usage_refresh_scheduler
from app.db.session import close_db, init_db
from app.modules.accounts import api as accounts_api
from app.modules.api_keys import api as api_keys_api
from app.modules.audit import api as audit_api
from app.modules.dashboard import api as dashboard_api
from app.modules.dashboard_auth import api as dashboard_auth_api
from app.modules.firewall import api as firewall_api
from app.modules.health import api as health_api
from app.modules.oauth import api as oauth_api
from app.modules.proxy import api as proxy_api
from app.modules.proxy.rate_limit_cache import get_rate_limit_headers_cache
from app.modules.request_logs import api as request_logs_api
from app.modules.settings import api as settings_api
from app.modules.sticky_sessions import api as sticky_sessions_api
from app.modules.sticky_sessions.cleanup_scheduler import build_sticky_session_cleanup_scheduler
from app.modules.usage import api as usage_api
from app.modules.usage.additional_quota_keys import reload_additional_quota_registry

logger = logging.getLogger(__name__)


def add_in_flight_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def in_flight_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        shutdown_state = import_module("app.core.shutdown")

        shutdown_state.increment_in_flight()
        try:
            return await call_next(request)
        finally:
            shutdown_state.decrement_in_flight()


@asynccontextmanager
async def lifespan(_: FastAPI):
    import app.core.startup as startup_module

    shutdown_state = import_module("app.core.shutdown")
    metrics_server = None
    metrics_server_task: asyncio.Task[None] | None = None

    startup_module._startup_complete = False
    shutdown_state.set_draining(False)
    await get_settings_cache().invalidate()
    await get_rate_limit_headers_cache().invalidate()
    reload_additional_quota_registry()
    settings = get_settings()
    if settings.otel_enabled:
        from app.core.tracing.otel import init_tracing

        init_tracing(service_name="codex-lb", endpoint=settings.otel_exporter_endpoint)
    await init_db()
    await init_http_client()
    usage_scheduler = build_usage_refresh_scheduler()
    model_scheduler = build_model_refresh_scheduler()
    sticky_session_cleanup_scheduler = build_sticky_session_cleanup_scheduler()
    await usage_scheduler.start()
    await model_scheduler.start()
    await sticky_session_cleanup_scheduler.start()
    if settings.metrics_enabled and PROMETHEUS_AVAILABLE:
        import uvicorn

        from app.core.metrics.prometheus import REGISTRY

        prometheus_module = import_module("prometheus_client")
        make_asgi_app = getattr(prometheus_module, "make_asgi_app")
        metrics_app = make_asgi_app(registry=REGISTRY)
        config = uvicorn.Config(metrics_app, host="0.0.0.0", port=settings.metrics_port, log_level="warning")
        metrics_server = uvicorn.Server(config)
        metrics_server_task = asyncio.create_task(metrics_server.serve())
    elif settings.metrics_enabled:
        logger.warning("Metrics endpoint enabled but prometheus-client is not installed")
    startup_module._startup_complete = True

    try:
        yield
    finally:
        shutdown_state.set_draining(True)
        drained = await shutdown_state.wait_for_in_flight_drain(timeout_seconds=settings.shutdown_drain_timeout_seconds)
        if not drained:
            logger.warning("Drain timeout reached, proceeding with shutdown")

        if metrics_server is not None:
            metrics_server.should_exit = True

        await sticky_session_cleanup_scheduler.stop()
        await model_scheduler.stop()
        await usage_scheduler.stop()
        try:
            await close_http_client()
        finally:
            try:
                if metrics_server_task is not None:
                    await asyncio.wait_for(metrics_server_task, timeout=5)
            except TimeoutError:
                logger.warning("Timed out waiting for metrics server shutdown")
            except Exception:
                logger.exception("Metrics server stopped with an error")
            finally:
                await close_db()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="codex-lb",
        version="0.1.0",
        lifespan=lifespan,
        swagger_ui_parameters={"persistAuthorization": True},
    )

    add_in_flight_middleware(app)
    add_request_decompression_middleware(app)
    add_request_id_middleware(app)
    add_api_firewall_middleware(app)
    app.add_middleware(cast(Any, MetricsMiddleware), enabled=settings.metrics_enabled)
    if settings.backpressure_max_concurrent_requests > 0:
        app.add_middleware(
            cast(Any, BackpressureMiddleware),
            max_concurrent=settings.backpressure_max_concurrent_requests,
        )
    add_exception_handlers(app)

    app.include_router(proxy_api.router)
    app.include_router(proxy_api.ws_router)
    app.include_router(proxy_api.v1_router)
    app.include_router(proxy_api.v1_ws_router)
    app.include_router(proxy_api.transcribe_router)
    app.include_router(proxy_api.usage_router)
    app.include_router(audit_api.router)
    app.include_router(accounts_api.router)
    app.include_router(dashboard_api.router)
    app.include_router(usage_api.router)
    app.include_router(request_logs_api.router)
    app.include_router(oauth_api.router)
    app.include_router(dashboard_auth_api.router)
    app.include_router(settings_api.router)
    app.include_router(firewall_api.router)
    app.include_router(sticky_sessions_api.router)
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
    async def spa_fallback(path: str = ""):
        normalized = path.lstrip("/")
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

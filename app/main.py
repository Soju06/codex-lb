from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from collections.abc import Awaitable
from contextlib import asynccontextmanager
from importlib import import_module
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlparse

import aiohttp
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from app.core.bootstrap import ensure_auto_bootstrap_token, log_bootstrap_token
from app.core.clients.http import close_http_client, init_http_client
from app.core.config.settings import _bridge_advertise_hostname_is_replica_specific, get_settings
from app.core.config.settings_cache import get_settings_cache
from app.core.handlers import add_exception_handlers
from app.core.metrics.middleware import MetricsMiddleware
from app.core.metrics.prometheus import MULTIPROCESS_MODE, PROMETHEUS_AVAILABLE, make_scrape_registry, mark_process_dead
from app.core.middleware import (
    add_api_firewall_middleware,
    add_request_decompression_middleware,
    add_request_id_middleware,
)
from app.core.middleware.inflight import InFlightMiddleware
from app.core.openai.model_refresh_scheduler import build_model_refresh_scheduler
from app.core.resilience.backpressure import BackpressureMiddleware
from app.core.resilience.bulkhead import BulkheadMiddleware, get_bulkhead
from app.core.resilience.memory_monitor import configure as configure_memory_monitor
from app.core.usage.refresh_scheduler import build_usage_refresh_scheduler
from app.db.session import SessionLocal, close_db, init_background_db, init_db
from app.modules.accounts import api as accounts_api
from app.modules.api_keys import api as api_keys_api
from app.modules.audit import api as audit_api
from app.modules.dashboard import api as dashboard_api
from app.modules.dashboard_auth import api as dashboard_auth_api
from app.modules.firewall import api as firewall_api
from app.modules.health import api as health_api
from app.modules.oauth import api as oauth_api
from app.modules.proxy import api as proxy_api
from app.modules.proxy.durable_bridge_repository import missing_durable_bridge_tables
from app.modules.proxy.rate_limit_cache import get_rate_limit_headers_cache
from app.modules.proxy.ring_membership import (
    RING_HEARTBEAT_INTERVAL_SECONDS,
    RING_STALE_GRACE_SECONDS,
    RING_STALE_THRESHOLD_SECONDS,
    RingMembershipService,
)
from app.modules.request_logs import api as request_logs_api
from app.modules.settings import api as settings_api
from app.modules.sticky_sessions import api as sticky_sessions_api
from app.modules.sticky_sessions.cleanup_scheduler import build_sticky_session_cleanup_scheduler
from app.modules.usage import api as usage_api
from app.modules.usage.additional_quota_keys import reload_additional_quota_registry

logger = logging.getLogger(__name__)


class _MetricsServer(Protocol):
    should_exit: bool

    async def serve(self) -> None: ...


class _RingMembershipReader(Protocol):
    def list_active(self, stale_threshold_seconds: int = RING_STALE_THRESHOLD_SECONDS) -> Awaitable[list[str]]: ...


def _is_benign_metrics_bind_failure(exc: BaseException) -> bool:
    if not MULTIPROCESS_MODE:
        return False
    if isinstance(exc, SystemExit):
        return exc.code == 1
    if isinstance(exc, OSError):
        import errno as _errno

        return exc.errno in (_errno.EADDRINUSE, _errno.EADDRNOTAVAIL)
    return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    import app.core.startup as startup_module

    shutdown_state = import_module("app.core.shutdown")
    metrics_server = None
    metrics_server_task: asyncio.Task[None] | None = None
    ring_service = None
    heartbeat_task: asyncio.Task[None] | None = None
    instance_id = None

    startup_module._startup_complete = False
    startup_module.reset_bridge_registration()
    shutdown_state.reset()
    await get_settings_cache().invalidate()
    await get_rate_limit_headers_cache().invalidate()
    reload_additional_quota_registry()
    settings = get_settings()
    bridge_endpoint_base_url = settings.http_responses_session_bridge_advertise_base_url
    if settings.otel_enabled:
        from app.core.tracing.otel import init_tracing

        init_tracing(service_name="codex-lb", endpoint=settings.otel_exporter_endpoint, app=app)
    await init_db()
    init_background_db()
    _auto_bootstrap_token = await ensure_auto_bootstrap_token()
    if _auto_bootstrap_token:
        log_bootstrap_token(logger, _auto_bootstrap_token)
    await init_http_client()
    await _ensure_bridge_durable_schema_ready(settings)
    startup_module.mark_bridge_durable_schema_ready()
    usage_scheduler = build_usage_refresh_scheduler()
    model_scheduler = build_model_refresh_scheduler()
    sticky_session_cleanup_scheduler = build_sticky_session_cleanup_scheduler()
    await usage_scheduler.start()
    await model_scheduler.start()
    await sticky_session_cleanup_scheduler.start()
    if settings.metrics_enabled and PROMETHEUS_AVAILABLE:
        import uvicorn

        scrape_registry = make_scrape_registry()
        prometheus_module = import_module("prometheus_client")
        make_asgi_app = getattr(prometheus_module, "make_asgi_app")
        metrics_app = make_asgi_app(registry=scrape_registry)
        config = uvicorn.Config(metrics_app, host="0.0.0.0", port=settings.metrics_port, log_level="warning")
        metrics_server = uvicorn.Server(config)

        async def _serve_metrics(srv: _MetricsServer) -> None:
            try:
                await srv.serve()
            except SystemExit as exc:
                if _is_benign_metrics_bind_failure(exc):
                    logger.info(
                        "Metrics port %d unavailable (another worker likely serves metrics)",
                        settings.metrics_port,
                    )
                else:
                    raise
            except OSError as exc:
                if _is_benign_metrics_bind_failure(exc):
                    logger.info(
                        "Metrics port %d already bound (another worker serves metrics)",
                        settings.metrics_port,
                    )
                else:
                    raise

        metrics_server_task = asyncio.create_task(_serve_metrics(metrics_server))
    elif settings.metrics_enabled:
        logger.warning("Metrics endpoint enabled but prometheus-client is not installed")

    async def _complete_bridge_registration(svc: RingMembershipService, iid: str) -> None:
        if bridge_endpoint_base_url is not None:
            await _wait_for_bridge_advertise_endpoint(
                bridge_endpoint_base_url,
                connect_timeout_seconds=settings.upstream_connect_timeout_seconds,
            )
        await _activate_bridge_membership(svc, iid)
        startup_module.mark_bridge_registration_complete()

    async def _heartbeat_only(svc: RingMembershipService, iid: str) -> None:
        while True:
            await asyncio.sleep(RING_HEARTBEAT_INTERVAL_SECONDS)
            try:
                await svc.heartbeat(iid, endpoint_base_url=bridge_endpoint_base_url)
            except Exception:
                logger.warning("Ring heartbeat failed", exc_info=True)

    async def _register_and_heartbeat(svc: RingMembershipService, iid: str) -> None:
        attempt = 0
        while True:
            attempt += 1
            try:
                await _complete_bridge_registration(svc, iid)
                logger.info("Registered in bridge ring", extra={"instance_id": iid, "attempt": attempt})
                break
            except Exception:
                delay = min(5.0 * (2 ** min(attempt - 1, 5)), 60.0)
                logger.warning("Ring registration attempt %d failed, retrying in %.0fs", attempt, delay, exc_info=True)
                await asyncio.sleep(delay)
        await _heartbeat_only(svc, iid)

    async def _activate_bridge_membership(svc: RingMembershipService, iid: str) -> None:
        if bridge_endpoint_base_url is None:
            await svc.register(iid, endpoint_base_url=None)
            return
        await _validate_bridge_advertise_endpoint_for_multi_replica(
            svc=svc,
            settings=settings,
            instance_id=iid,
            endpoint_base_url=bridge_endpoint_base_url,
        )
        await svc.register(iid, endpoint_base_url=bridge_endpoint_base_url)

    from app.core.auth.api_key_cache import get_api_key_cache
    from app.core.cache.invalidation import (
        NAMESPACE_API_KEY,
        NAMESPACE_FIREWALL,
        CacheInvalidationPoller,
        set_cache_invalidation_poller,
    )
    from app.core.middleware.firewall_cache import get_firewall_ip_cache

    cache_poller = CacheInvalidationPoller(SessionLocal)
    cache_poller.on_invalidation(NAMESPACE_API_KEY, get_api_key_cache().clear)
    cache_poller.on_invalidation(NAMESPACE_FIREWALL, get_firewall_ip_cache().invalidate_all)
    set_cache_invalidation_poller(cache_poller)
    await cache_poller.start()

    ring_service: RingMembershipService | None = None
    instance_id: str | None = None
    heartbeat_task: asyncio.Task[None] | None = None
    ring_service = RingMembershipService(SessionLocal)
    instance_id = settings.http_responses_session_bridge_instance_id
    heartbeat_task = asyncio.create_task(_register_and_heartbeat(ring_service, instance_id))
    startup_module._startup_complete = True

    try:
        yield
    finally:
        shutdown_state.set_bridge_drain_active(True)
        shutdown_state.set_draining(True)
        drained = await shutdown_state.wait_for_in_flight_drain(timeout_seconds=settings.shutdown_drain_timeout_seconds)
        if not drained:
            logger.warning("Drain timeout reached, proceeding with shutdown")

        proxy_service = getattr(app.state, "proxy_service", None)
        if proxy_service is not None and hasattr(proxy_service, "mark_http_bridge_draining"):
            try:
                await proxy_service.mark_http_bridge_draining()
            except Exception:
                logger.warning("Failed to mark HTTP bridge durable sessions draining during shutdown", exc_info=True)
        if proxy_service is not None and hasattr(proxy_service, "close_all_http_bridge_sessions"):
            try:
                await proxy_service.close_all_http_bridge_sessions()
            except Exception:
                logger.warning("Failed to close HTTP bridge sessions during shutdown", exc_info=True)

        # Cancel heartbeat and age the shared ring row near expiry.
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await asyncio.wait_for(heartbeat_task, timeout=2)
            except (asyncio.CancelledError, TimeoutError):
                pass

        if ring_service is not None and instance_id is not None:
            try:
                await asyncio.wait_for(
                    ring_service.mark_stale(
                        instance_id,
                        stale_threshold_seconds=RING_STALE_THRESHOLD_SECONDS,
                        grace_seconds=RING_STALE_GRACE_SECONDS,
                    ),
                    timeout=3,
                )
                logger.info(
                    "Marked bridge ring membership stale for shutdown",
                    extra={"instance_id": instance_id},
                )
            except Exception:
                logger.warning("Failed to mark bridge ring membership stale during shutdown", exc_info=True)

        if metrics_server is not None:
            metrics_server.should_exit = True

        await cache_poller.stop()
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
                shutdown_state.reset()
                mark_process_dead()
                await close_db()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_memory_monitor(
        warning_threshold_mb=settings.memory_warning_threshold_mb,
        reject_threshold_mb=settings.memory_reject_threshold_mb,
    )
    app = FastAPI(
        title="codex-lb",
        version="0.1.0",
        lifespan=lifespan,
        swagger_ui_parameters={"persistAuthorization": True},
    )

    app.add_middleware(cast(Any, InFlightMiddleware))
    add_request_decompression_middleware(app)
    add_request_id_middleware(app)
    add_api_firewall_middleware(app)
    app.add_middleware(cast(Any, MetricsMiddleware), enabled=settings.metrics_enabled)
    if settings.backpressure_max_concurrent_requests > 0:
        app.add_middleware(
            cast(Any, BackpressureMiddleware),
            max_concurrent=settings.backpressure_max_concurrent_requests,
        )
    app.add_middleware(
        cast(Any, BulkheadMiddleware),
        bulkhead=get_bulkhead(
            proxy_limit=settings.bulkhead_proxy_limit,
            dashboard_limit=settings.bulkhead_dashboard_limit,
        ),
    )
    add_exception_handlers(app)

    app.include_router(proxy_api.router)
    app.include_router(proxy_api.internal_router)
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


async def _ensure_bridge_durable_schema_ready(settings) -> None:
    if not settings.http_responses_session_bridge_enabled:
        return
    session = SessionLocal()
    try:
        missing_tables = await missing_durable_bridge_tables(session)
    finally:
        await session.close()
    if not missing_tables:
        return
    missing = ", ".join(missing_tables)
    raise RuntimeError(f"HTTP bridge durable schema is missing required tables: {missing}")


async def _wait_for_bridge_advertise_endpoint(
    bridge_endpoint_base_url: str | None,
    *,
    connect_timeout_seconds: float,
) -> None:
    local_port = _local_api_port()
    if bridge_endpoint_base_url is None and local_port is None:
        raise RuntimeError(
            "Cannot determine local bridge listener port for registration probe; "
            "set PORT or configure http_responses_session_bridge_advertise_base_url"
        )
    probe_base_url = bridge_endpoint_base_url or f"http://127.0.0.1:{local_port}"
    probe_base_url = probe_base_url.rstrip("/")
    probe_url = f"{probe_base_url}/health/live"
    probe_scheme = urlparse(probe_url).scheme.lower()
    timeout = aiohttp.ClientTimeout(
        total=connect_timeout_seconds,
        sock_connect=connect_timeout_seconds,
        sock_read=connect_timeout_seconds,
    )
    max_probe_wait_seconds = max(connect_timeout_seconds, 5.0)
    deadline = time.monotonic() + max_probe_wait_seconds
    await asyncio.sleep(0)
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
                async with session.get(probe_url, ssl=False if probe_scheme == "https" else None) as response:
                    if response.status == 200:
                        return
        except Exception:
            logger.debug(
                "Bridge advertise endpoint not yet reachable",
                extra={"probe_url": probe_url, "attempt": attempt},
                exc_info=True,
            )
        delay = min(0.5 * (2 ** min(attempt - 1, 4)), 5.0)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        await asyncio.sleep(min(delay, remaining))
    raise RuntimeError(
        f"Bridge advertise endpoint did not become reachable before registration probe deadline: {probe_url}"
    )


def _local_api_port() -> int | None:
    raw = os.getenv("PORT")
    port = _parse_port_value(raw.strip()) if raw is not None else None
    if port is None:
        port = _port_from_argv()
    return port


def _parse_port_value(raw: str) -> int | None:
    try:
        port = int(raw)
    except ValueError:
        return None
    if port <= 0:
        return None
    return port


def _port_from_argv() -> int | None:
    args = tuple(sys.argv[1:])
    for index, value in enumerate(args):
        if value == "--port" and index + 1 < len(args):
            return _parse_port_value(args[index + 1])
        if value.startswith("--port="):
            return _parse_port_value(value.split("=", 1)[1])
    return None


async def _validate_bridge_advertise_endpoint_for_multi_replica(
    svc: _RingMembershipReader,
    *,
    settings,
    instance_id: str,
    endpoint_base_url: str | None,
) -> None:
    if endpoint_base_url is None:
        return
    hostname = urlparse(endpoint_base_url).hostname
    if hostname is None:
        raise RuntimeError("http_responses_session_bridge_advertise_base_url must include a valid hostname")
    try:
        parsed_ip = ip_address(hostname)
    except ValueError:
        parsed_ip = None
    if (parsed_ip is not None and parsed_ip.is_loopback) or hostname == "localhost":
        configured_multi_replica = len(settings.http_responses_session_bridge_instance_ring) > 1
        if configured_multi_replica:
            raise RuntimeError(
                "http_responses_session_bridge_advertise_base_url must be replica-specific for bridge routing"
            )
        try:
            active_instances = await svc.list_active(stale_threshold_seconds=RING_HEARTBEAT_INTERVAL_SECONDS)
        except Exception:
            active_instances = []
        if any(active_instance != instance_id for active_instance in active_instances):
            raise RuntimeError(
                "http_responses_session_bridge_advertise_base_url must be replica-specific for bridge routing"
            )
        return
    if not _bridge_advertise_hostname_is_replica_specific(hostname, instance_id=instance_id):
        raise RuntimeError(
            "http_responses_session_bridge_advertise_base_url must be replica-specific for bridge routing"
        )


app = create_app()

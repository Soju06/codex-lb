from __future__ import annotations

import asyncio
import logging
import os
import stat
import sys
import time
from collections.abc import Awaitable
from contextlib import asynccontextmanager
from datetime import timedelta
from functools import lru_cache
from importlib import import_module
from ipaddress import ip_address
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, cast
from urllib.parse import urlparse

import aiohttp
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles

from app.core.audit.service import drain_audit_log_tasks
from app.core.auth.guardian import build_auth_guardian_scheduler
from app.core.balancer import configure_replica_salt
from app.core.bootstrap import ensure_auto_bootstrap_token, log_bootstrap_token
from app.core.clients.http import close_http_client, init_http_client
from app.core.config.key_fingerprint import verify_encryption_key_fingerprint
from app.core.config.settings import (
    _bridge_advertise_hostname_is_replica_specific,
    get_settings,
    warn_removed_settings,
)
from app.core.config.settings_cache import get_settings_cache
from app.core.handlers import add_exception_handlers
from app.core.metrics.middleware import MetricsMiddleware
from app.core.metrics.prometheus import MULTIPROCESS_MODE, PROMETHEUS_AVAILABLE, make_scrape_registry, mark_process_dead
from app.core.middleware import (
    add_api_firewall_middleware,
    add_app_version_middleware,
    add_backend_api_codex_v1_alias_middleware,
    add_dashboard_auth_proxy_middleware,
    add_multipart_content_encoding_middleware,
    add_request_body_limit_middleware,
    add_request_decompression_middleware,
    add_request_id_middleware,
    add_trusted_proxy_headers_middleware,
)
from app.core.middleware.dashboard_gzip import add_dashboard_gzip_middleware
from app.core.middleware.inflight import InFlightMiddleware
from app.core.openai.model_refresh_scheduler import build_model_refresh_scheduler
from app.core.resilience.backpressure import BackpressureMiddleware
from app.core.resilience.bulkhead import BulkheadMiddleware, get_bulkhead
from app.core.resilience.memory_monitor import configure as configure_memory_monitor
from app.core.retention.scheduler import build_data_retention_scheduler
from app.core.scheduling.leader_election import get_leader_election
from app.core.shutdown import close_control_plane_task_admission
from app.core.usage.refresh_scheduler import build_usage_refresh_scheduler
from app.core.usage.reset_credits_refresh_scheduler import build_rate_limit_reset_credits_scheduler
from app.core.utils.time import utcnow
from app.db.session import SessionLocal, close_db, close_session, init_background_db, init_db
from app.modules.accounts import api as accounts_api
from app.modules.accounts.usage_rollup_scheduler import build_account_usage_rollup_scheduler
from app.modules.api_keys import api as api_keys_api
from app.modules.api_keys.reset_scheduler import build_api_key_limit_reset_scheduler
from app.modules.audit import api as audit_api
from app.modules.automations import api as automations_api
from app.modules.automations.scheduler import build_automations_scheduler
from app.modules.conversation_archive import api as conversation_archive_api
from app.modules.dashboard import api as dashboard_api
from app.modules.dashboard_auth import api as dashboard_auth_api
from app.modules.firewall import api as firewall_api
from app.modules.fleet import api as fleet_api
from app.modules.health import api as health_api
from app.modules.model_sources import api as model_sources_api
from app.modules.oauth import api as oauth_api
from app.modules.proxy import api as proxy_api
from app.modules.proxy.cap_partitioning import refresh_cap_partition
from app.modules.proxy.durable_bridge_coordinator import DurableBridgeSessionCoordinator
from app.modules.proxy.durable_bridge_repository import missing_durable_bridge_tables
from app.modules.proxy.rate_limit_cache import get_rate_limit_headers_cache
from app.modules.proxy.ring_membership import (
    RING_HEARTBEAT_INTERVAL_SECONDS,
    RING_STALE_GRACE_SECONDS,
    RING_STALE_THRESHOLD_SECONDS,
    RingMembershipService,
)
from app.modules.quota_planner import api as quota_planner_api
from app.modules.quota_planner.scheduler import build_quota_planner_scheduler
from app.modules.rate_limit_reset_credits import api as rate_limit_reset_credits_api
from app.modules.reports import api as reports_api
from app.modules.request_logs import api as request_logs_api
from app.modules.runtime import api as runtime_api
from app.modules.settings import api as settings_api
from app.modules.sticky_sessions import api as sticky_sessions_api
from app.modules.sticky_sessions.cleanup_scheduler import (
    _abandoned_bridge_retention_seconds,
    build_sticky_session_cleanup_scheduler,
)
from app.modules.usage import api as usage_api
from app.modules.usage.additional_quota_keys import reload_additional_quota_registry
from app.modules.usage.live_ingest import start_live_usage_ingestor, stop_live_usage_ingestor

logger = logging.getLogger(__name__)


def _log_abandoned_lease_release(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("Abandoned scheduler leader lease release finished with error", exc_info=exc)


async def _release_leader_lease_within(timeout: float) -> None:
    """Release the scheduler leader lease without ever pinning shutdown.

    ``release()`` uses a background DB session whose rollback/close shield and
    await their own cleanup, so wrapping it in ``asyncio.wait_for`` would only
    cancel the awaiting wrapper while a wedged database call keeps unwinding —
    shutdown could still hang past the deadline. Run the release as a task and,
    if it does not finish within ``timeout``, abandon it (logging its eventual
    outcome from a done callback) so shutdown always proceeds within the
    deadline; the lease then expires after its TTL, which is acceptable.
    """
    release_task: asyncio.Task[None] = asyncio.ensure_future(get_leader_election().release())
    done, _ = await asyncio.wait({release_task}, timeout=timeout)
    if release_task not in done:
        logger.warning(
            "Scheduler leader lease release did not finish within %.1fs; abandoning it so "
            "shutdown can proceed (the lease will expire after its TTL)",
            timeout,
        )
        release_task.add_done_callback(_log_abandoned_lease_release)
        return
    exc = release_task.exception()
    if exc is not None:
        logger.warning("Failed to release scheduler leader lease during shutdown", exc_info=exc)


async def _drain_detached_control_plane_tasks(timeout_seconds: float) -> None:
    # Closing admission is synchronous with producer checks on the event loop,
    # so no task can appear after the stable drain passes complete.
    close_control_plane_task_admission()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    clean_passes = 0

    while clean_passes < 2:
        remaining = max(0.0, deadline - loop.time())
        results = await asyncio.gather(
            drain_audit_log_tasks(remaining),
            fleet_api.drain_background_refresh_tasks(remaining),
            return_exceptions=True,
        )

        clean_pass = True
        for task_kind, result in zip(("audit log", "fleet refresh"), results, strict=True):
            if isinstance(result, BaseException):
                clean_pass = False
                logger.warning(
                    "Failed to drain %s tasks during shutdown",
                    task_kind,
                    exc_info=(type(result), result, result.__traceback__),
                )
            elif result is False:
                clean_pass = False

        if not clean_pass:
            return

        clean_passes += 1

        # A done callback may enqueue sibling audit/fleet work after both
        # registries looked empty. One extra stable pass catches that before
        # HTTP clients and DB engines are torn down.
        if clean_passes < 2:
            await asyncio.sleep(0)


class _MetricsServer(Protocol):
    should_exit: bool

    async def serve(self) -> None: ...


class _RingMembershipReader(Protocol):
    def list_active(
        self,
        stale_threshold_seconds: int = RING_STALE_THRESHOLD_SECONDS,
        *,
        require_endpoint: bool = False,
    ) -> Awaitable[list[str]]: ...


@lru_cache(maxsize=4)
def _static_files_for_root(static_root: Path) -> StaticFiles:
    return StaticFiles(directory=static_root, check_dir=False)


def _resolve_static_asset_path(static_root: Path, requested_path: str) -> Path | None:
    """Return a filesystem path for a SPA asset only when it stays under static_root."""
    normalized = PurePosixPath(requested_path)
    if normalized.is_absolute() or ".." in normalized.parts:
        return None
    full_path, stat_result = _static_files_for_root(static_root).lookup_path(normalized.as_posix())
    if stat_result is None or not stat.S_ISREG(stat_result.st_mode):
        return None
    return Path(full_path)


def _is_metrics_bind_conflict(exc: BaseException) -> bool:
    if isinstance(exc, SystemExit):
        return exc.code == 1
    if isinstance(exc, OSError):
        import errno as _errno

        return exc.errno in (_errno.EADDRINUSE, _errno.EADDRNOTAVAIL)
    return False


def _is_benign_metrics_bind_failure(exc: BaseException) -> bool:
    return MULTIPROCESS_MODE and _is_metrics_bind_conflict(exc)


def _log_non_multiproc_metrics_bind_conflict(port: int) -> None:
    logger.error(
        "Metrics port %d is already bound by another worker process but PROMETHEUS_MULTIPROC_DIR is not set: "
        "/metrics reflects only the winning worker's counters (1/N of traffic). "
        "Set PROMETHEUS_MULTIPROC_DIR to a writable directory shared by all workers to aggregate metrics "
        "across worker processes.",
        port,
    )


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
    await get_settings_cache().invalidate(propagate=False)
    await get_rate_limit_headers_cache().invalidate()
    reload_additional_quota_registry()
    settings = get_settings()
    warn_removed_settings()
    # Anchor round-robin tie-break decorrelation to this replica's stable bridge
    # instance identity so peer replicas spread exact ties across equally-good
    # accounts instead of all herding onto the lexicographically-first account.
    configure_replica_salt(settings.http_responses_session_bridge_instance_id)
    bridge_endpoint_base_url = settings.http_responses_session_bridge_advertise_base_url
    if settings.otel_enabled:
        from app.core.tracing.otel import init_tracing

        init_tracing(service_name="codex-lb", endpoint=settings.otel_exporter_endpoint, app=app)
    await init_db()
    init_background_db()
    await verify_encryption_key_fingerprint()
    _auto_bootstrap_token = await ensure_auto_bootstrap_token()
    if _auto_bootstrap_token:
        log_bootstrap_token(logger, _auto_bootstrap_token)
    await init_http_client()
    bridge_durable_schema_ready = await _ensure_bridge_durable_schema_ready(settings)
    if bridge_durable_schema_ready is True:
        startup_module.mark_bridge_durable_schema_ready()
        dashboard_settings = await get_settings_cache().get()
        ownerless_cutoff = utcnow() - timedelta(
            seconds=_abandoned_bridge_retention_seconds(dashboard_settings, settings)
        )
        deleted_bridge_rows = await DurableBridgeSessionCoordinator(SessionLocal).purge_owned_sessions_on_startup(
            instance_id=settings.http_responses_session_bridge_instance_id,
            ownerless_cutoff=ownerless_cutoff,
        )
        if deleted_bridge_rows > 0:
            logger.info(
                "Purged durable HTTP bridge rows from previous process instance",
                extra={
                    "instance_id": settings.http_responses_session_bridge_instance_id,
                    "deleted": deleted_bridge_rows,
                },
            )
    from app.core.auth.api_key_cache import get_api_key_cache
    from app.core.cache.invalidation import (
        NAMESPACE_ACCOUNT_ROUTING,
        NAMESPACE_ACCOUNT_SELECTION,
        NAMESPACE_API_KEY,
        NAMESPACE_FIREWALL,
        NAMESPACE_MODEL_REGISTRY,
        NAMESPACE_RESET_CREDITS,
        NAMESPACE_SETTINGS,
        NAMESPACE_UPSTREAM_ROUTE,
        CacheInvalidationPoller,
        get_cache_invalidation_poller,
        set_cache_invalidation_poller,
    )
    from app.core.middleware.firewall_cache import get_firewall_ip_cache
    from app.core.upstream_proxy.cache import get_upstream_route_cache
    from app.modules.proxy.account_cache import get_account_selection_cache, get_routing_availability_cache
    from app.modules.rate_limit_reset_credits.store import get_rate_limit_reset_credits_store

    # The poller MUST be installed before the model scheduler starts: a first
    # leader tick that persists a changed snapshot bumps the model_registry
    # namespace through the global poller, and a not-yet-installed poller would
    # silently drop that bump (followers would then wait for the refresh-tick
    # backstop instead of the cache poll bound).
    cache_poller = CacheInvalidationPoller(SessionLocal)
    cache_poller.on_invalidation(NAMESPACE_API_KEY, get_api_key_cache().clear)
    cache_poller.on_invalidation(NAMESPACE_FIREWALL, get_firewall_ip_cache().invalidate_all)
    routing_availability_cache = get_routing_availability_cache()
    # Remote-bump callbacks must be non-propagating variants: a propagating callback
    # would re-bump on every observed bump and feedback-loop across replicas.
    cache_poller.on_invalidation(NAMESPACE_ACCOUNT_ROUTING, routing_availability_cache.refresh_from_db)
    cache_poller.on_invalidation(
        NAMESPACE_ACCOUNT_SELECTION,
        lambda: get_account_selection_cache().invalidate(propagate=False),
    )
    cache_poller.on_invalidation(
        NAMESPACE_SETTINGS,
        lambda: get_settings_cache().invalidate(propagate=False),
    )
    cache_poller.on_invalidation(NAMESPACE_UPSTREAM_ROUTE, get_upstream_route_cache().clear)
    # The route resolver also reads the dashboard settings row (routing enabled
    # + default pool id), so settings bumps clear resolved routes as well.
    cache_poller.on_invalidation(NAMESPACE_SETTINGS, get_upstream_route_cache().clear)
    # The bus carries no payload, so a peer redeem clears this replica's whole
    # reset-credits store; the refresh scheduler repopulates it on its next tick.
    cache_poller.on_invalidation(NAMESPACE_RESET_CREDITS, get_rate_limit_reset_credits_store().invalidate)
    if settings.model_registry_enabled:
        from app.core.openai.model_registry_store import reconcile_model_registry_from_store

        # raise_on_error=True so a transient load failure leaves the
        # model_registry version unacknowledged and is retried on the next poll
        # cycle (matching the account_routing refresh callback) instead of being
        # swallowed, which would strand this replica on the stale catalog until
        # the non-leader scheduler backstop.
        cache_poller.on_invalidation(
            NAMESPACE_MODEL_REGISTRY,
            lambda: reconcile_model_registry_from_store(raise_on_error=True),
        )
    set_cache_invalidation_poller(cache_poller)

    # Seed the invalidation version baseline BEFORE loading the routing snapshot
    # and BEFORE the one-shot model-registry reconcile below. prime() records the
    # current version of every namespace (and flushes any pending bumps) without
    # firing callbacks, so a peer bump committed after this point is observed as a
    # change on the first background poll instead of being silently acknowledged
    # as pre-existing state. In particular a leader that persists-and-bumps the
    # model_registry namespace in the window between the reconcile's snapshot read
    # and the poller's first background tick would otherwise be absorbed as the
    # initial baseline, leaving this replica on the pre-refresh catalog until the
    # non-leader scheduler backstop (default 300s) instead of the sub-second cache
    # poll bound.
    try:
        await cache_poller.prime()
    except Exception:
        # prime() raises when the baseline version read fails; degrade to
        # first-poll-baselines (matching initialize()'s contract) rather than
        # continuing as if the seed succeeded. A peer bump landing before the
        # first background poll may then be absorbed as the initial baseline
        # and only converge on the fallback TTL / next bump, but the failure is
        # surfaced here instead of silently voiding the delivery guarantee.
        logger.warning("cache invalidation baseline prime failed", exc_info=True)
    try:
        await routing_availability_cache.refresh_from_db()
    except Exception:
        # Unseeded snapshot degrades to local-mark semantics; the next
        # account_routing bump retries the refresh via the poller callback.
        logger.warning("initial routing availability snapshot refresh failed", exc_info=True)

    if settings.model_registry_enabled:
        from app.core.openai.model_registry_store import reconcile_model_registry_from_store

        # Warm the in-memory registry from the persisted snapshot before any
        # scheduler starts so a restarted replica serves the refreshed catalog
        # instead of the bootstrap floor. Never fails startup.
        await reconcile_model_registry_from_store()

    await cache_poller.start()

    usage_scheduler = build_usage_refresh_scheduler()
    api_key_limit_reset_scheduler = build_api_key_limit_reset_scheduler()
    model_scheduler = build_model_refresh_scheduler()
    sticky_session_cleanup_scheduler = build_sticky_session_cleanup_scheduler()
    quota_planner_scheduler = build_quota_planner_scheduler()
    auth_guardian_scheduler = build_auth_guardian_scheduler()
    automations_scheduler = build_automations_scheduler()
    rate_limit_reset_credits_scheduler = build_rate_limit_reset_credits_scheduler()
    account_usage_rollup_scheduler = build_account_usage_rollup_scheduler()
    data_retention_scheduler = build_data_retention_scheduler()
    start_live_usage_ingestor()
    await usage_scheduler.start()
    await api_key_limit_reset_scheduler.start()
    await model_scheduler.start()
    await sticky_session_cleanup_scheduler.start()
    await quota_planner_scheduler.start()
    await auth_guardian_scheduler.start()
    await automations_scheduler.start()
    await rate_limit_reset_credits_scheduler.start()
    await account_usage_rollup_scheduler.start()
    await data_retention_scheduler.start()
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
                elif _is_metrics_bind_conflict(exc):
                    _log_non_multiproc_metrics_bind_conflict(settings.metrics_port)
                else:
                    raise
            except OSError as exc:
                if _is_benign_metrics_bind_failure(exc):
                    logger.info(
                        "Metrics port %d already bound (another worker serves metrics)",
                        settings.metrics_port,
                    )
                elif _is_metrics_bind_conflict(exc):
                    _log_non_multiproc_metrics_bind_conflict(settings.metrics_port)
                else:
                    raise

        metrics_server_task = asyncio.create_task(_serve_metrics(metrics_server))
    elif settings.metrics_enabled:
        logger.warning("Metrics endpoint enabled but prometheus-client is not installed")

    async def _complete_bridge_registration(svc: RingMembershipService, iid: str) -> None:
        if bridge_endpoint_base_url is None:
            await _activate_bridge_membership(svc, iid)
            startup_module.mark_bridge_registration_complete()
            return
        await _validate_bridge_advertise_endpoint_for_multi_replica(
            svc=svc,
            settings=settings,
            instance_id=iid,
            endpoint_base_url=bridge_endpoint_base_url,
        )
        await svc.register(iid, endpoint_base_url=None)
        await _wait_for_bridge_advertise_endpoint(
            bridge_endpoint_base_url,
            connect_timeout_seconds=settings.upstream_connect_timeout_seconds,
        )
        await svc.heartbeat(iid, endpoint_base_url=bridge_endpoint_base_url)
        startup_module.mark_bridge_registration_complete()

    async def _heartbeat_only(svc: RingMembershipService, iid: str) -> None:
        while True:
            await asyncio.sleep(RING_HEARTBEAT_INTERVAL_SECONDS)
            try:
                await svc.heartbeat(iid, endpoint_base_url=bridge_endpoint_base_url)
            except Exception:
                logger.warning("Ring heartbeat failed", exc_info=True)
            proxy_service = getattr(app.state, "proxy_service", None)
            if proxy_service is not None and hasattr(proxy_service, "reconcile_durable_http_bridge_ownership"):
                try:
                    await proxy_service.reconcile_durable_http_bridge_ownership()
                except Exception:
                    logger.warning("HTTP bridge durable ownership reconciliation failed", exc_info=True)
            await refresh_cap_partition(svc.list_active, iid)

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
        await refresh_cap_partition(svc.list_active, iid)
        await _heartbeat_only(svc, iid)

    async def _activate_bridge_membership(svc: RingMembershipService, iid: str) -> None:
        if bridge_endpoint_base_url is None:
            await svc.register(iid, endpoint_base_url=None)
            return
        await svc.register(iid, endpoint_base_url=bridge_endpoint_base_url)

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
        # No await separates the timeout result from this cutoff. A slow
        # request therefore either registered its task before the barrier or
        # is prevented from starting new audit/fleet database work afterward.
        shutdown_state.close_control_plane_task_admission()
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
        # Drain AFTER the bridge teardown: failing a bridge's pending
        # requests writes their request logs, which enqueues more
        # persistence tasks that this drain must cover.
        if proxy_service is not None and hasattr(proxy_service, "drain_persistence_tasks"):
            try:
                await proxy_service.drain_persistence_tasks(timeout_seconds=settings.shutdown_drain_timeout_seconds)
            except Exception:
                logger.warning("Failed to drain proxy persistence tasks during shutdown", exc_info=True)

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

        # Detached control-plane work must finish while usage singleflight,
        # shared HTTP clients, and both database engines are still available.
        # The replica heartbeat is already stopped/staled so this grace period
        # does not extend its active bridge-ring lifetime.
        await _drain_detached_control_plane_tasks(settings.shutdown_drain_timeout_seconds)

        # Start the single process-level lease-renewal keeper BEFORE stopping any
        # scheduler. Schedulers are stopped one at a time and only the final
        # release() renews the lease while draining detached bodies; an earlier
        # scheduler's stop() can detach a shielded leader-gated body (which stops
        # that scheduler's own heartbeat) while the remaining schedulers are still
        # stopping. If that stop sequence takes >= the (minimum 5s) TTL, the DB
        # lease would otherwise expire while the detached body still runs as
        # leader, letting a follower acquire it and run duplicate singleton work.
        # The keeper renews the lease continuously across the whole stop sequence
        # and is stopped by release(), which then owns renewal for its bounded
        # drain. It is a no-op when leader election is disabled.
        get_leader_election().start_release_keeper()
        await quota_planner_scheduler.stop()
        await auth_guardian_scheduler.stop()
        await automations_scheduler.stop()
        await sticky_session_cleanup_scheduler.stop()
        await model_scheduler.stop()
        # Stop the invalidation poller only after the model scheduler: a final
        # leader tick may still bump through the installed poller.
        await cache_poller.stop()
        if get_cache_invalidation_poller() is cache_poller:
            # A stopped poller must not keep receiving propagation requests.
            set_cache_invalidation_poller(None)
        await api_key_limit_reset_scheduler.stop()
        await usage_scheduler.stop()
        await stop_live_usage_ingestor()
        await rate_limit_reset_credits_scheduler.stop()
        await account_usage_rollup_scheduler.stop()
        await data_retention_scheduler.stop()
        # Release the scheduler leader lease only after every leader-gated
        # scheduler has stopped so no local tick re-acquires it; followers can
        # then take over immediately instead of waiting out the lease TTL.
        # release() itself first drains bodies that were detached still
        # draining shielded work, and skips the early release (letting the
        # lease expire by TTL) if any is still running, so a follower cannot
        # become leader while this process may still act as one. The deadline
        # covers that bounded drain plus the row delete, and — because the
        # release path shields and awaits its own session teardown — is
        # enforced by abandoning the release task rather than awaiting a
        # potentially wedged cancellation, so shutdown always proceeds.
        await _release_leader_lease_within(10)
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
                mark_process_dead()
                await close_db()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_memory_monitor(reject_threshold_mb=settings.memory_reject_threshold_mb)
    app = FastAPI(
        title="codex-lb",
        version="0.1.0",
        lifespan=lifespan,
        swagger_ui_parameters={"persistAuthorization": True},
    )

    app.add_middleware(cast(Any, InFlightMiddleware))
    add_dashboard_gzip_middleware(app)
    add_dashboard_auth_proxy_middleware(app)
    add_request_decompression_middleware(app)
    add_request_body_limit_middleware(app)
    add_multipart_content_encoding_middleware(app)
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
            proxy_http_limit=settings.bulkhead_proxy_limit,
            proxy_websocket_limit=settings.bulkhead_proxy_limit,
            # Compact limit is derived by BulkheadSemaphore: min(http, 16),
            # or 0 when the http class is unlimited-off (0).
            proxy_compact_limit=None,
            dashboard_limit=settings.bulkhead_dashboard_limit,
        ),
    )
    add_backend_api_codex_v1_alias_middleware(app)
    add_app_version_middleware(app)
    add_exception_handlers(app)
    add_trusted_proxy_headers_middleware(app)

    app.include_router(proxy_api.router)
    app.include_router(proxy_api.internal_router)
    app.include_router(proxy_api.ws_router)
    app.include_router(proxy_api.wham_router)
    app.include_router(proxy_api.v1_router)
    app.include_router(proxy_api.v1_ws_router)
    app.include_router(proxy_api.transcribe_router)
    app.include_router(proxy_api.files_router)
    app.include_router(proxy_api.usage_router)
    app.include_router(audit_api.router)
    app.include_router(accounts_api.router)
    app.include_router(rate_limit_reset_credits_api.router)
    app.include_router(dashboard_api.router)
    app.include_router(usage_api.router)
    app.include_router(request_logs_api.router)
    app.include_router(quota_planner_api.router)
    app.include_router(reports_api.router)
    app.include_router(conversation_archive_api.router)
    app.include_router(runtime_api.router)
    app.include_router(oauth_api.router)
    app.include_router(dashboard_auth_api.router)
    app.include_router(settings_api.router)
    app.include_router(firewall_api.router)
    app.include_router(fleet_api.router)
    app.include_router(sticky_sessions_api.router)
    app.include_router(automations_api.router)
    app.include_router(api_keys_api.router)
    app.include_router(model_sources_api.router)
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
            candidate = _resolve_static_asset_path(static_root, normalized)
            if candidate is not None:
                if normalized.startswith("assets/"):
                    # Vite content-hashes everything under assets/, so the
                    # response for a given URL can never change: immutable
                    # caching makes repeat dashboard loads skip ~1.7 MB of
                    # re-downloads/revalidations. index.html stays no-cache
                    # below, so deploys still pick up new hashes.
                    return FileResponse(candidate, headers={"Cache-Control": "public, max-age=31536000, immutable"})
                return FileResponse(candidate)
            if _is_static_asset_path(normalized):
                raise HTTPException(status_code=404, detail="Not Found")

        if not index_html.is_file():
            raise HTTPException(status_code=503, detail=frontend_build_hint)

        return FileResponse(index_html, media_type="text/html", headers={"Cache-Control": "no-cache"})

    return app


async def _ensure_bridge_durable_schema_ready(settings) -> bool:
    if not settings.http_responses_session_bridge_enabled:
        return False
    session = SessionLocal()
    try:
        missing_tables = await missing_durable_bridge_tables(session)
    finally:
        await close_session(session)
    if not missing_tables:
        return True
    missing = ", ".join(missing_tables)
    if settings.database_migrations_fail_fast:
        raise RuntimeError(f"HTTP bridge durable schema is missing required tables: {missing}")
    logger.warning(
        "HTTP bridge durable schema is missing required tables but startup fail-fast is disabled",
        extra={"missing_tables": missing_tables},
    )
    return False


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
                async with session.get(probe_url) as response:
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

from __future__ import annotations

import logging
import time

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config.dashboard_overrides import bind_dashboard_overrides, reset_dashboard_overrides
from app.core.config.settings_cache import get_settings_cache

logger = logging.getLogger(__name__)

_SNAPSHOT_FAILURE_LOG_INTERVAL_SECONDS = 60.0


class DashboardOverridesMiddleware:
    """Bind the dashboard-managed ``Settings`` overrides for the lifetime of one request or socket.

    Reads the ``SettingsCache`` snapshot (TTL 5 s, cross-replica invalidated) once
    per HTTP request or WebSocket connection and exposes the non-NULL dashboard
    columns through ``with_dashboard_overrides`` to every consumer downstream,
    including tasks the request spawns (they inherit the context). When the
    snapshot cannot be refreshed, the last row the cache loaded is used; only
    before the first successful load does the environment fallback apply.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._last_failure_logged_at = 0.0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        cache = get_settings_cache()
        try:
            row = await cache.get()
        except Exception:  # noqa: BLE001 - never fail a request over an unreadable snapshot
            row = cache.cached_row()
            self._log_snapshot_failure(stale_row_available=row is not None)
        if row is None:
            await self.app(scope, receive, send)
            return
        token = bind_dashboard_overrides(row)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_dashboard_overrides(token)

    def _log_snapshot_failure(self, *, stale_row_available: bool) -> None:
        now = time.monotonic()
        if now - self._last_failure_logged_at < _SNAPSHOT_FAILURE_LOG_INTERVAL_SECONDS:
            return
        self._last_failure_logged_at = now
        logger.warning(
            "dashboard settings snapshot unavailable; %s",
            "using the last loaded dashboard values" if stale_row_available else "using environment values",
            exc_info=True,
        )

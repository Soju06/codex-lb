from __future__ import annotations

import logging

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config.dashboard_overrides import bind_dashboard_overrides, reset_dashboard_overrides
from app.core.config.settings_cache import get_settings_cache

logger = logging.getLogger(__name__)


class DashboardOverridesMiddleware:
    """Bind the dashboard-managed ``Settings`` overrides for the lifetime of one request or socket.

    Reads the ``SettingsCache`` snapshot (TTL 5 s, cross-replica invalidated) once
    per HTTP request or WebSocket connection and exposes the non-NULL dashboard
    columns through ``with_dashboard_overrides`` to every consumer downstream,
    including tasks the request spawns (they inherit the context). A snapshot
    that cannot be read leaves the environment fallback in force for that request.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        try:
            row = await get_settings_cache().get()
        except Exception:  # noqa: BLE001 - never fail a request over an unreadable snapshot
            logger.debug("dashboard settings snapshot unavailable; using environment values", exc_info=True)
            await self.app(scope, receive, send)
            return
        token = bind_dashboard_overrides(row)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_dashboard_overrides(token)

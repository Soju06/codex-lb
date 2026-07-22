from __future__ import annotations

import asyncio
import logging

from app.modules.proxy._service.http_bridge.helpers import (
    _HTTP_BRIDGE_BACKGROUND_CLOSE_TIMEOUT_SECONDS,
    _http_bridge_pending_count_nowait,
    _http_bridge_request_counts_against_queue,
    http_bridge_activity_snapshot_nowait,
)
from app.modules.proxy._service.http_bridge.protocol import _HTTPBridgeServiceProtocol
from app.modules.proxy._service.support import _HTTPBridgeSession

logger = logging.getLogger("app.modules.proxy.service")


class _HTTPBridgeActivityMixin:
    async def _drain_http_bridge_background_cleanup_tasks(
        self: _HTTPBridgeServiceProtocol,
        *,
        reason: str,
    ) -> None:
        tasks = [
            task
            for task in self._background_cleanup_tasks
            if not task.done()
            and (
                task.get_name().startswith("proxy-http_bridge_session_close-")
                or task.get_name().startswith("http-bridge-close-")
            )
        ]
        if not tasks:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True),
                timeout=_HTTP_BRIDGE_BACKGROUND_CLOSE_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "http_bridge_background_cleanup_drain_timeout reason=%s count=%d timeout_seconds=%.1f",
                reason,
                len(tasks),
                _HTTP_BRIDGE_BACKGROUND_CLOSE_TIMEOUT_SECONDS,
            )

    async def _http_bridge_pending_count(
        self: _HTTPBridgeServiceProtocol,
        session: _HTTPBridgeSession,
    ) -> int:
        async with session.pending_lock:
            visible_pending_count = sum(
                1
                for request_state in session.pending_requests
                if _http_bridge_request_counts_against_queue(request_state)
            )
            return max(visible_pending_count, session.queued_request_count)

    def http_bridge_activity_snapshot_nowait(self: _HTTPBridgeServiceProtocol) -> dict[str, int | bool]:
        return http_bridge_activity_snapshot_nowait(self)

    def _http_bridge_pending_count_nowait(
        self: _HTTPBridgeServiceProtocol,
        session: _HTTPBridgeSession,
        *,
        context: str,
    ) -> int | None:
        return _http_bridge_pending_count_nowait(session, context=context)

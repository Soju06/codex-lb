from __future__ import annotations

from app.modules.proxy._service.http_bridge.helpers import (
    _http_bridge_pending_count_nowait,
    _http_bridge_request_counts_against_queue,
    http_bridge_activity_snapshot_nowait,
)
from app.modules.proxy._service.http_bridge.protocol import _HTTPBridgeServiceProtocol
from app.modules.proxy._service.support import _HTTPBridgeSession


class _HTTPBridgeActivityMixin:
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

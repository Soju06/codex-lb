from __future__ import annotations

import time

_UPSTREAM_WEBSOCKET_FAILURE_TTL_SECONDS = 60.0
_upstream_websocket_failure_at: float | None = None


def mark_upstream_websocket_transport_failure() -> None:
    """Remember a recent host-scoped upstream WebSocket transport failure."""

    global _upstream_websocket_failure_at
    _upstream_websocket_failure_at = time.monotonic()


def clear_upstream_websocket_transport_failure() -> None:
    """Clear the transport failure marker after a successful upstream connect."""

    global _upstream_websocket_failure_at
    _upstream_websocket_failure_at = None


def upstream_websocket_transport_recently_failed() -> bool:
    marked_at = _upstream_websocket_failure_at
    return marked_at is not None and time.monotonic() - marked_at < _UPSTREAM_WEBSOCKET_FAILURE_TTL_SECONDS

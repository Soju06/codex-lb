"""Per-instance health marker for the upstream Responses websocket transport.

Codex clients only switch to the HTTP transport when the websocket handshake
is rejected with HTTP 426 (codex-rs checks ``StatusCode::UPGRADE_REQUIRED``
on connect; in-band error events never trigger the transport fallback). This
marker remembers a recent connect-phase upstream websocket transport failure
so the websocket routes can deny the next handshake with 426 and the HTTP
paths can pin the upstream transport to HTTP until the websocket upstream
proves healthy again.
"""

from __future__ import annotations

import time

UPSTREAM_WS_TRANSPORT_FAILURE_TTL_SECONDS = 60.0
_upstream_ws_transport_failure_at: float | None = None


def mark_upstream_websocket_transport_failure() -> None:
    global _upstream_ws_transport_failure_at
    _upstream_ws_transport_failure_at = time.monotonic()


def clear_upstream_websocket_transport_failure() -> None:
    global _upstream_ws_transport_failure_at
    _upstream_ws_transport_failure_at = None


def upstream_websocket_transport_recently_failed() -> bool:
    marked_at = _upstream_ws_transport_failure_at
    return marked_at is not None and time.monotonic() - marked_at < UPSTREAM_WS_TRANSPORT_FAILURE_TTL_SECONDS

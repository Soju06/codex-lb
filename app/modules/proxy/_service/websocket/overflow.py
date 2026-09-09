"""WebSocket parity for subscription-exhaustion overflow (#2123 WP-D, folded into WP-C2).

Interface contract only -- every body raises ``NotImplementedError``. The
mixin will import nothing else new: two call sites (``<= 8`` lines total) call
these helpers, and all logic lives here.

Obligations once implemented (design §3, §6, §7.3):

* Settings fast path first: with both ``dashboard_settings`` columns off the
  helper returns ``False`` after two attribute reads (ship-dark, I9).
* Fresh exhaustion during a WebSocket session bounces in-band: a bounce row
  (60 s, drain-capped, thread-keyed; a latency optimisation whose write
  failure is logged and not fatal) plus the existing wrapped 503 connect
  failure event, which carries a top-level numeric ``status`` and the code
  ``model_source_requires_http_transport`` -- never ``server_is_overloaded``
  or ``slow_down`` (Codex treats those as non-retryable).
* Pinned or anchored conversations bounce toward HTTP on ``live``/``expired``
  thread-pin or live anchor evidence; ``PinLookupTimeout`` bounces (fail-closed
  toward HTTP). Non-native turns (no thread key) get the in-band 503 only.
* The decision is advisory: the HTTP route re-decides authoritatively after
  the client's session-scoped downgrade. Handshake 426 denial lives in
  ``api.py`` via ``app.modules.proxy.overflow.handshake_denial``.
* Zero timing allowance: bounded lookups take ``scheduler``/``clock`` from the
  proxy service; no owned tasks.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import anyio
    from fastapi import WebSocket

    from app.modules.api_keys.service import ApiKeyData
    from app.modules.proxy._service.support import _WebSocketRequestState
    from app.modules.proxy._service.websocket.protocol import _WebSocketServiceProtocol

__all__ = [
    "OUTCOME_BOUNCED_WS_EVENT",
    "bounce_exhausted_websocket_turn",
    "bounce_pinned_or_anchored_websocket_turn",
]

# ``outcome`` label recorded for every in-band bounce (``route="websocket"``).
OUTCOME_BOUNCED_WS_EVENT = "bounced_ws_event"


async def bounce_exhausted_websocket_turn(
    proxy: _WebSocketServiceProtocol,
    websocket: WebSocket,
    *,
    client_send_lock: anyio.Lock,
    api_key: ApiKeyData | None,
    request_state: _WebSocketRequestState,
    headers: Mapping[str, str],
) -> bool:
    """Called only when selection answered ``USAGE_LIMIT_REACHED`` (exhaustion established; no probe).

    ``True``: eligible -> bounce row written (when a thread key exists) and the
    in-band 503 bounce event emitted through ``proxy._emit_websocket_connect_failure``
    (reservation released, connect-failure row written). ``False``: not
    eligible -> today's 429 event is emitted by the caller unchanged.
    """

    raise NotImplementedError


async def bounce_pinned_or_anchored_websocket_turn(
    proxy: _WebSocketServiceProtocol,
    websocket: WebSocket,
    *,
    client_send_lock: anyio.Lock,
    api_key: ApiKeyData | None,
    request_state: _WebSocketRequestState,
    headers: Mapping[str, str],
) -> bool:
    """One bounded thread-key lookup (``live``/``expired`` -> bounce) and, for ``previous_response_id`` without a
    recorded subscription owner, one anchor lookup (live -> bounce); ``PinLookupTimeout`` -> bounce.

    Works for the first turn (no upstream yet) and a reused socket alike; ``True`` means the caller ``continue``s.
    """

    raise NotImplementedError

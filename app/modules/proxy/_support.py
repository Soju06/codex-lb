"""Compatibility shim for proxy service support internals.

New code should import from `app.modules.proxy._service.support`.
"""

from app.modules.proxy._service.support import (
    _HARD_HTTP_BRIDGE_AFFINITY_KINDS as _HARD_HTTP_BRIDGE_AFFINITY_KINDS,
)
from app.modules.proxy._service.support import (
    _REQUEST_TRANSPORT_WEBSOCKET as _REQUEST_TRANSPORT_WEBSOCKET,
)
from app.modules.proxy._service.support import (
    _WEBSOCKET_FULL_REPLAY_WAIT_MIN_ITEMS as _WEBSOCKET_FULL_REPLAY_WAIT_MIN_ITEMS,
)
from app.modules.proxy._service.support import (
    _WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS as _WEBSOCKET_FULL_REPLAY_WAIT_POLL_SECONDS,
)
from app.modules.proxy._service.support import (
    _ApiKeyReservationTouchState as _ApiKeyReservationTouchState,
)
from app.modules.proxy._service.support import (
    _clear_websocket_request_error_overrides as _clear_websocket_request_error_overrides,
)
from app.modules.proxy._service.support import (
    _consume_api_key_reservation_heartbeat_result as _consume_api_key_reservation_heartbeat_result,
)
from app.modules.proxy._service.support import (
    _copy_websocket_route_metadata_from_session as _copy_websocket_route_metadata_from_session,
)
from app.modules.proxy._service.support import (
    _copy_websocket_route_metadata_to_session as _copy_websocket_route_metadata_to_session,
)
from app.modules.proxy._service.support import (
    _DownstreamWebSocketActivity as _DownstreamWebSocketActivity,
)
from app.modules.proxy._service.support import (
    _event_type_from_payload as _event_type_from_payload,
)
from app.modules.proxy._service.support import (
    _FilePinEntry as _FilePinEntry,
)
from app.modules.proxy._service.support import (
    _HTTPBridgeOwnerForward as _HTTPBridgeOwnerForward,
)
from app.modules.proxy._service.support import (
    _HTTPBridgeSession as _HTTPBridgeSession,
)
from app.modules.proxy._service.support import (
    _HTTPBridgeSessionKey as _HTTPBridgeSessionKey,
)
from app.modules.proxy._service.support import (
    _PreparedWebSocketRequest as _PreparedWebSocketRequest,
)
from app.modules.proxy._service.support import (
    _record_response_event as _record_response_event,
)
from app.modules.proxy._service.support import (
    _record_websocket_route_metadata as _record_websocket_route_metadata,
)
from app.modules.proxy._service.support import (
    _RequestLogFailureMetadata as _RequestLogFailureMetadata,
)
from app.modules.proxy._service.support import (
    _RetryableStreamError as _RetryableStreamError,
)
from app.modules.proxy._service.support import (
    _stream_settlement_error_payload as _stream_settlement_error_payload,
)
from app.modules.proxy._service.support import (
    _StreamSettlement as _StreamSettlement,
)
from app.modules.proxy._service.support import (
    _TerminalStreamError as _TerminalStreamError,
)
from app.modules.proxy._service.support import (
    _TransientStreamError as _TransientStreamError,
)
from app.modules.proxy._service.support import (
    _wait_for_websocket_continuity_gap as _wait_for_websocket_continuity_gap,
)
from app.modules.proxy._service.support import (
    _websocket_full_replay_should_wait_for_continuity as _websocket_full_replay_should_wait_for_continuity,
)
from app.modules.proxy._service.support import (
    _websocket_request_can_replay_before_visible_output as _websocket_request_can_replay_before_visible_output,
)
from app.modules.proxy._service.support import (
    _websocket_route_log_kwargs as _websocket_route_log_kwargs,
)
from app.modules.proxy._service.support import (
    _WebSocketConnectFailureEmitted as _WebSocketConnectFailureEmitted,
)
from app.modules.proxy._service.support import (
    _WebSocketContinuityAnchor as _WebSocketContinuityAnchor,
)
from app.modules.proxy._service.support import (
    _WebSocketContinuityState as _WebSocketContinuityState,
)
from app.modules.proxy._service.support import (
    _WebSocketReceiveTimeout as _WebSocketReceiveTimeout,
)
from app.modules.proxy._service.support import (
    _WebSocketRequestState as _WebSocketRequestState,
)
from app.modules.proxy._service.support import (
    _WebSocketUpstreamControl as _WebSocketUpstreamControl,
)

"""Upstream websocket close-1009 classification (allow-bounded-inline-images-on-bridge).

Close code 1009 (message too big) is terminal payload evidence, not transport
evidence: the classifier must be close-code-exact (1006/None and 1000 keep
their existing semantics), and the terminal message must read as a payload
rejection rather than a generic disconnect.
"""

from __future__ import annotations

import pytest

from app.core.clients.proxy_websocket import (
    UPSTREAM_WEBSOCKET_MESSAGE_TOO_BIG_CLOSE_CODE,
    UpstreamWebSocketMessage,
    is_upstream_message_too_big_close_code,
)
from app.modules.proxy._service.streaming.helpers import (
    _classify_upstream_close,
    _is_upstream_payload_too_large_close,
)
from app.modules.proxy._service.websocket.helpers import (
    _upstream_websocket_disconnect_message,
    _upstream_websocket_payload_too_large_message,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("close_code", "expected"),
    [
        pytest.param(1009, True, id="message_too_big"),
        pytest.param(None, False, id="no_close_frame"),
        pytest.param(1006, False, id="abnormal_closure_synth"),
        pytest.param(1000, False, id="clean"),
        pytest.param(1011, False, id="server_error"),
        pytest.param(1008, False, id="policy_violation"),
    ],
)
def test_close_code_predicate_is_exact(close_code: int | None, expected: bool) -> None:
    assert is_upstream_message_too_big_close_code(close_code) is expected
    assert _is_upstream_payload_too_large_close(close_code) is expected


def test_close_code_constant_is_rfc_6455_message_too_big() -> None:
    assert UPSTREAM_WEBSOCKET_MESSAGE_TOO_BIG_CLOSE_CODE == 1009


@pytest.mark.parametrize(
    ("close_code", "events", "expected"),
    [
        pytest.param(1009, 0, "payload_too_large", id="too_big_before_events"),
        pytest.param(1009, 5, "payload_too_large", id="too_big_after_events"),
        pytest.param(1000, 0, "clean", id="clean_before_events_unchanged"),
        pytest.param(1000, 3, "transient", id="clean_after_events_unchanged"),
        pytest.param(1006, 0, "transient", id="generic_disconnect_unchanged"),
        pytest.param(None, 0, "transient", id="no_close_frame_unchanged"),
    ],
)
def test_classify_upstream_close(close_code: int | None, events: int, expected: str) -> None:
    assert _classify_upstream_close(close_code, response_events_seen=events) == expected


def test_payload_too_large_message_is_distinct_from_generic_disconnect() -> None:
    close_message = _upstream_websocket_disconnect_message(
        UpstreamWebSocketMessage(kind="close", close_code=1009, close_reason="message too big")
    )
    payload_message = _upstream_websocket_payload_too_large_message()
    assert "1009" in payload_message
    assert "message too big" in payload_message
    assert "reduce the request size" in payload_message
    # The generic disconnect wording stays available (and unchanged) for
    # every other close shape; the classified error never reuses it.
    assert "response.completed" in close_message
    assert "response.completed" not in payload_message

from __future__ import annotations

import pytest

from app.core.clients.proxy_websocket import is_upstream_message_too_big_close_code
from app.modules.proxy._service.streaming.helpers import _classify_upstream_close

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("code", [1009, 1000, 1006, 1008, 1011, None])
@pytest.mark.parametrize("events", [0, 3])
def test_only_close_1009_changes_classification(code: int | None, events: int) -> None:
    assert is_upstream_message_too_big_close_code(code) is (code == 1009)
    expected = "payload_too_large" if code == 1009 else "clean" if code == 1000 and events == 0 else "transient"
    assert _classify_upstream_close(code, response_events_seen=events) == expected

from __future__ import annotations

from dataclasses import replace

from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.http_bridge import streaming as http_bridge_streaming_module


def test_http_bridge_continuity_bound_without_safe_replay() -> None:
    unsafe_continuation = proxy_service._WebSocketRequestState(
        request_id="req-unsafe",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=1.0,
        previous_response_id="resp-prev",
    )
    safe_full_resend = replace(
        unsafe_continuation,
        fresh_upstream_request_text='{"type":"response.create","input":"full"}',
        fresh_upstream_request_is_retry_safe=True,
    )
    hard_turn_state = replace(
        unsafe_continuation,
        previous_response_id=None,
        hard_continuity_anchor=True,
    )
    ordinary_request = replace(unsafe_continuation, previous_response_id=None)

    assert http_bridge_streaming_module._http_bridge_continuity_bound_without_safe_replay(unsafe_continuation)
    assert not http_bridge_streaming_module._http_bridge_continuity_bound_without_safe_replay(safe_full_resend)
    assert http_bridge_streaming_module._http_bridge_continuity_bound_without_safe_replay(hard_turn_state)
    assert not http_bridge_streaming_module._http_bridge_continuity_bound_without_safe_replay(ordinary_request)

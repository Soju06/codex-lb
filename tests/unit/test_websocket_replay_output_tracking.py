from __future__ import annotations

import asyncio
import json

import pytest

from app.core.metrics import prometheus
from app.modules.proxy._service.http_bridge.accepted_replay import _stage_websocket_request_state_for_replay
from app.modules.proxy._service.support import _WebSocketRequestState
from app.modules.proxy._service.websocket.helpers import _install_verified_fresh_replay


def _request() -> _WebSocketRequestState:
    """Construct a request with output tracking state for replay lifecycle tests."""
    state = _WebSocketRequestState(
        request_id="tracking-replay",
        model="gpt-5.4",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        response_id="old-response",
        response_event_count=2,
    )
    state.response_output_items = [{"type": "message", "id": "old-item"}]
    state.response_output_items_by_index = {0: state.response_output_items[0]}
    state.response_output_item_added_indexes = {0}
    state.response_output_item_added_identities = {0: {"type": "message", "id": "old-item"}}
    state.added_tool_call_item_ids = {"old-tool"}
    state.tool_call_manifest_invalid = True
    state.response_output_items_event_invalid = True
    state.response_output_items_complete = True
    state.response_output_items_bytes = 1234
    return state


def _assert_empty(state: _WebSocketRequestState) -> None:
    """Assert every output-capture field has returned to its empty replay baseline."""
    assert state.response_output_items == []
    assert state.response_output_items_by_index == {}
    assert state.response_output_item_added_indexes == set()
    assert state.response_output_item_added_identities == {}
    assert state.added_tool_call_item_ids == set()
    assert state.tool_call_manifest_invalid is False
    assert state.response_output_items_event_invalid is False
    assert state.response_output_items_complete is False
    assert state.response_output_items_bytes == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("accepted", [False, True])
async def test_staged_replay_resets_output_tracking(accepted: bool) -> None:
    """Successfully staged replay clears output captured by the interrupted attempt."""
    state = _request()
    if not accepted:
        state.response_id = None
        state.awaiting_response_created = True
    assert await _stage_websocket_request_state_for_replay(
        state, create_gate=None, surface="websocket", trigger="capacity_error"
    )
    _assert_empty(state)
    assert state.replay_downstream_response_id == ("old-response" if accepted else None)
    assert state.suppress_next_created_downstream is accepted


@pytest.mark.asyncio
async def test_rejected_staging_preserves_output_tracking() -> None:
    """Failed replay staging leaves the current attempt's captured output untouched."""
    state = _request()
    before = dict(vars(state))
    assert not await _stage_websocket_request_state_for_replay(
        state, create_gate=asyncio.Semaphore(0), surface="websocket", trigger="capacity_error"
    )
    assert vars(state) == before


@pytest.mark.parametrize("authorized", [False, True])
def test_fresh_body_resets_output_tracking_only_when_installed(authorized: bool) -> None:
    """Preparing replacement input alone does not reset capture; installing it does."""
    state = _request()
    state.previous_response_id = "old-anchor"
    state.proxy_injected_previous_response_id = True
    state.fresh_upstream_request_is_retry_safe = authorized
    state.fresh_upstream_request_text = json.dumps({"type": "response.create", "model": "gpt-5.4", "input": []})
    before = dict(vars(state))
    result = _install_verified_fresh_replay(state)
    if authorized:
        assert result == state.request_text == state.fresh_upstream_request_text
        _assert_empty(state)
    else:
        assert result is None
        assert vars(state) == before


def test_recovery_counters_are_exported() -> None:
    """Recovery observability exposes the expected counter series."""
    assert "http_bridge_parked_recovery_total" in prometheus.__all__
    assert "http_bridge_stuck_watchdog_skip_total" in prometheus.__all__

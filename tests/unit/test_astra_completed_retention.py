from __future__ import annotations

import asyncio
import json
from collections import deque

import anyio
import pytest

from app.modules.api_keys.service import ApiKeyRequestUsageBudget
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.support import (
    _WebSocketRequestState,
    _WebSocketSteeringContinuation,
    _WebSocketSteerSubmission,
    _WebSocketUpstreamControl,
)
from app.modules.proxy._service.websocket.steering import steering_parent, steering_response_payload
from tests.unit.test_proxy_utils import _make_account, _repo_factory, _RequestLogsRecorder

pytestmark = pytest.mark.unit


def test_bounded_steering_queue_does_not_impose_an_extra_count_limit() -> None:
    parent = _WebSocketRequestState(
        request_id="parent",
        model="gpt-6-astra",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        response_id="r1",
    )
    child = _WebSocketRequestState(
        request_id="child",
        model="gpt-6-astra",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
    )
    continuation = _WebSocketSteeringContinuation(
        parent=parent,
        request_state=child,
        submissions=[
            _WebSocketSteerSubmission(
                input="x",
                wire_bytes=1,
                request_usage_budget=ApiKeyRequestUsageBudget(),
                request_service_tier=None,
            )
            for _ in range(33)
        ],
        queued_input_bytes=33,
    )
    control = _WebSocketUpstreamControl(steering_continuations={"r1": continuation})
    assert steering_parent("r1", pending_requests=deque([child]), control=control) is parent


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["gpt-5.1", "gpt-6-astra"])
@pytest.mark.parametrize("event_type", ["response.completed", "response.incomplete"])
@pytest.mark.parametrize("stored_configuration", [False, True], ids=["request-text-fallback", "configuration"])
async def test_completed_dispatch_retains_only_astra_steering_parent(
    model: str, event_type: str, stored_configuration: bool
) -> None:
    logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(logs))
    account = _make_account("acc_retention")
    request_text = json.dumps(
        {
            "model": model,
            "instructions": "Keep these instructions",
            "stream_id": "original-lane",
            "reasoning": {"effort": "high", "summary": "auto"},
            "input": [
                {"role": "user", "content": "large request " * 8192},
                {"type": "configuration_update", "reasoning": {"effort": "medium"}},
                {"type": "configuration_update", "reasoning": {"effort": "xhigh"}},
            ],
        }
    )
    state = _WebSocketRequestState(
        request_id="request-current",
        model=model,
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        response_id="response-current",
        awaiting_response_created=False,
        request_text=request_text,
        fresh_upstream_request_text=request_text,
        fresh_upstream_request_is_retry_safe=True,
        steering_configuration=json.loads(request_text) if stored_configuration else None,
    )
    previous = _WebSocketRequestState(
        request_id="request-previous",
        model="gpt-6-astra",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        response_id="response-previous",
    )
    pending = deque([state])
    control = _WebSocketUpstreamControl(last_completed_request=previous)
    await service._process_upstream_websocket_text(
        json.dumps(
            {
                "type": event_type,
                "response": {
                    "id": state.response_id,
                    "model": model,
                    "output": [],
                    "incomplete_details": {"reason": "steered"} if event_type == "response.incomplete" else None,
                    "usage": {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14},
                },
            }
        ),
        account=account,
        account_id_value=account.id,
        pending_requests=pending,
        pending_lock=anyio.Lock(),
        api_key=None,
        upstream_control=control,
        response_create_gate=asyncio.Semaphore(0),
    )

    assert not pending
    if model == "gpt-6-astra":
        assert control.last_completed_request is state
        assert steering_parent("response-current", pending_requests=pending, control=control) is state
        successor = steering_response_payload(
            state, parent_id="response-current", input_items=[{"role": "user", "content": "Correction"}]
        ).model_dump_for_forwarding()
        assert successor["reasoning"] == {"effort": "xhigh", "summary": "auto"}
        assert successor["instructions"] == "Keep these instructions"
        assert successor["stream_id"] == "original-lane"
        assert successor["input"] == [{"role": "user", "content": "Correction"}]
        assert state.request_text is None
        assert state.fresh_upstream_request_text is None
        assert not state.fresh_upstream_request_is_retry_safe
        assert state.steering_configuration is not None
        assert "input" not in state.steering_configuration
    else:
        assert control.last_completed_request is None

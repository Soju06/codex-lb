from __future__ import annotations

import asyncio
import json
from collections import deque

import anyio
import pytest

from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.support import _WebSocketRequestState, _WebSocketUpstreamControl
from app.modules.proxy._service.websocket.steering import steering_parent
from tests.unit.test_proxy_utils import _make_account, _repo_factory, _RequestLogsRecorder

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["gpt-5.1", "gpt-6-astra"])
@pytest.mark.parametrize("event_type", ["response.completed", "response.incomplete"])
async def test_completed_dispatch_retains_only_astra_steering_parent(model: str, event_type: str) -> None:
    logs = _RequestLogsRecorder()
    service = proxy_service.ProxyService(_repo_factory(logs))
    account = _make_account("acc_retention")
    state = _WebSocketRequestState(
        request_id="request-current",
        model=model,
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
        response_id="response-current",
        awaiting_response_created=False,
        request_text=json.dumps({"model": model, "input": "large request " * 8192}),
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
    else:
        assert control.last_completed_request is None

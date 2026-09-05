from __future__ import annotations

import json
from collections import deque
from types import SimpleNamespace

import pytest

from app.core.clients.proxy import ProxyResponseError
from app.core.types import JsonValue
from app.modules.proxy._service.support import (
    _WebSocketRequestState,
    _WebSocketSteeringContinuation,
    _WebSocketUpstreamControl,
)
from app.modules.proxy._service.websocket import steering as steering_module
from app.modules.proxy._service.websocket.steering import (
    assign_websocket_created_request_state,
    steering_parent,
    validate_steering_input,
)
from tests.unit.test_astra_steering_protocol import ScriptedSocket, ScriptedUpstream, create, response, run_socket, saw

pytestmark = pytest.mark.unit


def _request_state(request_id: str) -> _WebSocketRequestState:
    return _WebSocketRequestState(
        request_id=request_id,
        model="gpt-6-astra",
        service_tier=None,
        reasoning_effort=None,
        api_key_reservation=None,
        started_at=0.0,
    )


def test_empty_input_text_is_rejected_as_nonempty_steering_input() -> None:
    payload: dict[str, JsonValue] = {
        "type": "response.steer",
        "previous_response_id": "resp_parent",
        "input": [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": ""}],
            }
        ],
    }

    with pytest.raises(ProxyResponseError) as exc_info:
        validate_steering_input(payload)

    assert exc_info.value.payload["error"]["code"] == "invalid_input"


@pytest.mark.asyncio
async def test_failed_submission_releases_its_aggregate_steering_bytes(monkeypatch) -> None:
    first = {"type": "response.steer", "previous_response_id": "r1", "input": "First correction"}
    second = {**first, "input": "Other correction"}
    third = {**first, "input": "Third correction"}
    wire_bytes = len(json.dumps(first, ensure_ascii=True, separators=(",", ":")).encode("utf-8"))
    assert len(json.dumps(second, ensure_ascii=True, separators=(",", ":")).encode("utf-8")) == wire_bytes
    assert len(json.dumps(third, ensure_ascii=True, separators=(",", ":")).encode("utf-8")) == wire_bytes
    monkeypatch.setattr(
        steering_module,
        "get_settings",
        lambda: SimpleNamespace(upstream_response_create_max_bytes=wire_bytes * 2),
    )
    socket = ScriptedSocket(
        [
            (create(), lambda _: True),
            (first, saw("response.created", "r1")),
            (second, saw("response.steer.accepted")),
            (third, saw("response.steer.failed")),
        ]
    )
    socket.finish_when = lambda event: (
        event.get("type") == "response.completed"
        or (event.get("type") == "response.steer.failed" and event.get("error", {}).get("code") == "payload_too_large")
    )
    upstream = ScriptedUpstream(
        [
            [response("response.created", "r1")],
            [{"type": "response.steer.accepted", "steer": {"id": "s1", "previous_response_id": "r1"}}],
            [
                {"type": "response.steer.accepted", "steer": {"id": "s2", "previous_response_id": "r1"}},
                {
                    "type": "response.steer.failed",
                    "steer": {"id": "s1", "previous_response_id": "r1", "input": first["input"]},
                    "error": {"code": "successor_creation_failed", "message": "Rejected"},
                },
            ],
            [
                {"type": "response.steer.accepted", "steer": {"id": "s3", "previous_response_id": "r1"}},
                response("response.completed", "r1"),
            ],
        ]
    )

    await run_socket(monkeypatch, socket, upstream)

    assert [frame["type"] for frame in upstream.sent] == [
        "response.create",
        "response.steer",
        "response.steer",
        "response.steer",
    ]


def test_missing_steering_child_created_event_never_claims_unrelated_fifo_request() -> None:
    parent = _request_state("parent")
    parent.response_id = "r1"
    stale_child = _request_state("stale-child")
    stale_child.steering_parent_response_id = "r1"
    unrelated = _request_state("unrelated")
    continuation = _WebSocketSteeringContinuation(parent=parent, request_state=stale_child)
    control = _WebSocketUpstreamControl(steering_continuations={"r1": continuation})
    # Expiry and explicit-continuation replacement both leave this same
    # transient shape: the continuation is known but its old child is absent.
    pending_requests = deque([unrelated])

    assigned = assign_websocket_created_request_state(
        {"type": "response.created", "response": {"id": "r-late", "previous_response_id": "r1"}},
        response_id="r-late",
        control=control,
        pending_requests=pending_requests,
    )

    assert assigned is None
    assert unrelated.response_id is None
    assert control.suppressed_steering_response_ids == {"r-late"}
    with pytest.raises(ProxyResponseError) as exc_info:
        steering_parent("r1", pending_requests=pending_requests, control=control)
    assert exc_info.value.payload["error"]["code"] == "response_not_found"

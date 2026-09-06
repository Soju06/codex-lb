from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

import app.modules.proxy._service.websocket.mixin as ws_mixin
from app.core.exceptions import AppError, ProxyModelNotAllowed
from tests.unit.test_astra_steering_protocol import (
    ScriptedSocket,
    ScriptedUpstream,
    create,
    response,
    run_socket,
    saw,
)

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        ValueError("private-value"),
        AppError("private-app-message", code="private-app-code", param="private-app-param"),
    ],
)
async def test_steering_failure_does_not_expose_raw_exception_text(monkeypatch, error):
    steer = {"type": "response.steer", "previous_response_id": "r1", "input": "Correction"}
    socket = ScriptedSocket([(create(), lambda _: True), (steer, saw("response.created", "r1"))])
    upstream = ScriptedUpstream([[response("response.created", "r1")]])
    monkeypatch.setattr(ws_mixin, "submit_websocket_steering", AsyncMock(side_effect=error))

    await run_socket(monkeypatch, socket, upstream)

    failure = socket.sent[-1]
    assert failure["type"] == "response.steer.failed"
    assert failure["error"] == {
        "code": "invalid_input",
        "message": "Invalid steering request.",
        "type": "invalid_request_error",
    }
    serialized = json.dumps(failure)
    assert "private-value" not in serialized
    assert "private-app" not in serialized


@pytest.mark.asyncio
async def test_model_policy_failure_discards_private_error_overrides(monkeypatch):
    steer = {"type": "response.steer", "previous_response_id": "r1", "input": "Correction"}
    socket = ScriptedSocket([(create(), lambda _: True), (steer, saw("response.created", "r1"))])
    upstream = ScriptedUpstream([[response("response.created", "r1")]])
    monkeypatch.setattr(
        ws_mixin,
        "submit_websocket_steering",
        AsyncMock(
            side_effect=ProxyModelNotAllowed(
                "private-model-name",
                code="private-model-code",
                param="private-model-param",
            )
        ),
    )

    await run_socket(monkeypatch, socket, upstream)

    failure = socket.sent[-1]
    assert failure["type"] == "response.steer.failed"
    assert failure["error"] == {
        "code": "model_not_allowed",
        "message": "This API key does not have access to the requested model.",
        "type": "permission_error",
    }
    assert "private-model" not in json.dumps(failure)


@pytest.mark.asyncio
async def test_upstream_steer_failed_sanitizes_error_param(monkeypatch):
    steer = {"type": "response.steer", "previous_response_id": "r1", "input": "Correction"}
    socket = ScriptedSocket([(create(), lambda _: True), (steer, saw("response.created", "r1"))])
    upstream = ScriptedUpstream(
        [
            [response("response.created", "r1")],
            [
                {"type": "response.steer.accepted", "steer": {"id": "s1", "previous_response_id": "r1"}},
                {
                    "type": "response.steer.failed",
                    "steer": {"id": "s1", "previous_response_id": "r1", "input": "Correction"},
                    "error": {
                        "code": "successor_creation_failed",
                        "message": "Rejected",
                        "param": ["private-field"],
                    },
                },
                response("response.completed", "r1"),
            ],
        ]
    )
    socket.finish_when = lambda event: event.get("type") == "response.completed" and event["response"]["id"] == "r1"

    await run_socket(monkeypatch, socket, upstream)

    failure = next(event for event in socket.sent if event.get("type") == "response.steer.failed")
    error = failure["error"]
    assert isinstance(error, dict)
    assert "param" not in error
    assert "private-field" not in json.dumps(failure)

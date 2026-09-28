from __future__ import annotations

import json
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.core.clients.proxy import ProxyResponseError, _error_event_from_response, _error_payload_from_response
from app.core.exceptions import ProxyInvalidRequestError, ProxyReasoningEffortNotAllowed
from app.modules.proxy import api as proxy_api
from app.modules.proxy.api import _logged_error_json_response, _stream_response_error_events

pytestmark = pytest.mark.unit


def test_logged_error_json_response_preserves_upstream_diagnostic_markers():
    message = "Provider Exception: failed while reading /tmp/upstream-cache"
    request = Request({"type": "http", "method": "POST", "path": "/v1/responses", "headers": []})
    payload = {"error": {"code": "upstream_error", "message": message}}

    response = _logged_error_json_response(request, 502, payload)

    assert json.loads(bytes(response.body))["error"]["message"] == message


@pytest.mark.asyncio
async def test_stream_proxy_error_preserves_upstream_diagnostic_markers():
    message = "Provider Exception: failed while reading /tmp/upstream-cache"

    async def stream():
        if False:
            yield ""
        raise ProxyResponseError(
            502,
            {"error": {"code": "upstream_error", "message": message, "type": "server_error"}},
        )

    events = [
        event
        async for event in _stream_response_error_events(
            stream(),
            owns_reservation=False,
            reservation=None,
        )
    ]

    assert len(events) == 1
    assert message in events[0]


@pytest.mark.asyncio
async def test_stream_proxy_error_preserves_retry_after_as_sse_retry_hint():
    async def stream():
        if False:
            yield ""
        raise ProxyResponseError(
            503,
            {
                "error": {
                    "code": "upstream_request_timeout",
                    "message": "Retry shortly.",
                    "type": "server_error",
                }
            },
            retry_after_seconds=2,
        )

    events = [
        event
        async for event in _stream_response_error_events(
            stream(),
            owns_reservation=False,
            reservation=None,
        )
    ]

    assert len(events) == 1
    assert events[0].startswith("retry: 2000\n")


@pytest.mark.asyncio
@pytest.mark.parametrize("native_lifecycle", [False, True], ids=["openai", "native"])
@pytest.mark.parametrize(
    "policy_error",
    [
        ProxyInvalidRequestError("Automatic truncation conflicts with updates", param="truncation"),
        ProxyReasoningEffortNotAllowed("Effort is not allowed", param="input.0.reasoning.effort"),
        ProxyInvalidRequestError("Invalid continuation"),
    ],
    ids=["invalid-request", "reasoning-policy", "without-param"],
)
async def test_stream_policy_rejection_preserves_terminal_error(
    native_lifecycle: bool,
    policy_error: ProxyInvalidRequestError | ProxyReasoningEffortNotAllowed,
) -> None:
    cleanup = AsyncMock(spec=proxy_api._ResponsesReservationCleanup)
    created_event = 'data: {"type":"response.created","response":{"id":"resp_policy"}}\n\n'

    async def stream() -> AsyncIterator[str]:
        yield created_event
        raise policy_error

    events = [
        event
        async for event in _stream_response_error_events(
            stream(),
            owns_reservation=True,
            reservation=None,
            reservation_cleanup=cleanup,
            preserve_native_failure_lifecycle=native_lifecycle,
        )
    ]

    assert events[0] == created_event
    assert len(events) == 2
    failed = proxy_api._parse_sse_payload(events[1])
    assert failed is not None
    assert failed["type"] == "response.failed"
    assert proxy_api.SYNTHETIC_TRANSPORT_FAILURE_MARKER not in failed
    response = failed["response"]
    assert isinstance(response, dict)
    expected_error = {"code": policy_error.code, "type": policy_error.error_type, "message": policy_error.message}
    if policy_error.param is not None:
        expected_error["param"] = policy_error.param
    assert response["error"] == expected_error
    cleanup.release.assert_awaited_once_with(action="responses stream cleanup")


def _payload_error_code(payload) -> str | None:
    return payload["error"].get("code")


def _payload_error_message(payload) -> str | None:
    return payload["error"].get("message")


class MockResponse:
    def __init__(self, status, reason=None, json_data=None, text_data=""):
        self.status = status
        self.reason = reason
        self._json = json_data
        self._text = text_data

    async def json(self, *, content_type=None):
        if self._json is None:
            raise Exception("No JSON")
        return self._json

    async def text(self, *, encoding=None, errors="strict"):
        return self._text


@pytest.mark.asyncio
async def test_error_event_includes_reason_in_fallback():
    resp = MockResponse(402, reason="Payment Required", json_data=None, text_data="")
    event = await _error_event_from_response(resp)

    assert event["response"]["error"].get("code") == "upstream_error"
    message = event["response"]["error"].get("message")
    assert "Upstream error: HTTP 402 Payment Required" == message


@pytest.mark.asyncio
async def test_error_payload_includes_reason_in_fallback():
    resp = MockResponse(402, reason="Payment Required", json_data=None, text_data="")
    payload = await _error_payload_from_response(resp)

    assert _payload_error_code(payload) == "upstream_error"
    message = _payload_error_message(payload)
    assert "Upstream error: HTTP 402 Payment Required" == message


@pytest.mark.asyncio
async def test_error_event_uses_text_if_present():
    resp = MockResponse(502, reason="Bad Gateway", json_data=None, text_data="My Custom Error")
    event = await _error_event_from_response(resp)

    assert event["response"]["error"].get("message") == "My Custom Error"


@pytest.mark.asyncio
async def test_error_payload_uses_json_if_valid():
    json_data = {"error": {"message": "OpenAI says no", "type": "server_error", "code": "oops"}}
    resp = MockResponse(400, reason="Bad Request", json_data=json_data, text_data="")
    payload = await _error_payload_from_response(resp)

    assert _payload_error_message(payload) == "OpenAI says no"
    assert _payload_error_code(payload) == "oops"


@pytest.mark.asyncio
async def test_error_payload_uses_message_field():
    json_data = {"message": "Plain message"}
    resp = MockResponse(400, reason="Bad Request", json_data=json_data, text_data="")
    payload = await _error_payload_from_response(resp)

    assert _payload_error_message(payload) == "Plain message"


@pytest.mark.asyncio
async def test_error_payload_uses_detail_field():
    json_data = {"detail": "Bad request"}
    resp = MockResponse(400, reason="Bad Request", json_data=json_data, text_data="")
    payload = await _error_payload_from_response(resp)

    assert _payload_error_message(payload) == "Bad request"


@pytest.mark.asyncio
async def test_error_event_uses_detail_field():
    json_data = {"detail": "Bad request"}
    resp = MockResponse(400, reason="Bad Request", json_data=json_data, text_data="")
    event = await _error_event_from_response(resp)

    assert event["response"]["error"].get("message") == "Bad request"


@pytest.mark.asyncio
async def test_error_event_fallback_no_reason():
    resp = MockResponse(500, reason=None, json_data=None, text_data="")
    event = await _error_event_from_response(resp)

    assert event["response"]["error"].get("message") == "Upstream error: HTTP 500"

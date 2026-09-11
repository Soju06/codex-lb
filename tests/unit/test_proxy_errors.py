from __future__ import annotations

import json

import pytest
from starlette.requests import Request

from app.core.clients.proxy import (
    ProxyResponseError,
    _error_event_from_response,
    _error_payload_from_response,
    _infer_websocket_handshake_error_code,
)
from app.modules.proxy.api import _logged_error_json_response, _stream_response_error_events

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("status", [429, 403, None])
@pytest.mark.parametrize(
    "message",
    [
        # The wording the fixtures across this repository observe upstream send.
        "The usage limit has been reached",
        "You've hit your usage limit.",
        "Usage limit reached.",
        "You have exceeded your usage limit.",
    ],
)
def test_websocket_handshake_usage_limit_is_coded_from_the_message(status, message):
    # The handshake carries the rejection as free text, so the inferred code is
    # the only place the usage limit can still be read off it.
    assert _infer_websocket_handshake_error_code(status, message) == "usage_limit_reached"


@pytest.mark.parametrize(
    ("status", "message", "expected"),
    [
        (403, "This account has been deactivated", "account_deactivated"),
        (403, "Usage not included in your plan", "usage_not_included"),
        (429, "Insufficient quota for this request", "insufficient_quota"),
        (429, "Quota exceeded for this organization", "quota_exceeded"),
        (429, "Rate limit exceeded, try again shortly", "rate_limit_exceeded"),
        (401, "Unauthorized", "invalid_api_key"),
        (404, "Not found", "not_found"),
        (429, "Too many requests", "rate_limit_exceeded"),
        (503, "Upstream unavailable", "upstream_error"),
    ],
)
def test_websocket_handshake_error_codes_keep_their_hints(status, message, expected):
    assert _infer_websocket_handshake_error_code(status, message) == expected


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

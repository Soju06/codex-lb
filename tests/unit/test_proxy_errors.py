from __future__ import annotations

import json
from types import SimpleNamespace
from typing import cast

import aiohttp
import pytest
from aiohttp import RequestInfo
from starlette.requests import Request

from app.core.clients.proxy import (
    ProxyResponseError,
    _error_event_from_response,
    _error_payload_from_response,
    _error_payload_from_websocket_handshake_error,
    _infer_websocket_handshake_error_code,
)
from app.modules.proxy.api import _logged_error_json_response, _stream_response_error_events
from app.modules.proxy.helpers import is_upstream_usage_limit_rejection

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


# One sentence that reads both ways: the account-scoped usage limit, and the
# generic throttling substring that rides along with almost every 429 wording.
_HANDSHAKE_USAGE_LIMIT_WITH_THROTTLING_TEXT = "You've hit your usage limit. Your rate limit resets in 4 hours."


@pytest.mark.parametrize("status", [429, 403, None])
@pytest.mark.parametrize(
    "message",
    [
        _HANDSHAKE_USAGE_LIMIT_WITH_THROTTLING_TEXT,
        "Rate limit reached. The usage limit has been reached for this account.",
    ],
)
def test_websocket_handshake_usage_limit_outranks_the_generic_throttling_hint(status, message):
    # Throttling says "arrive slower"; the usage limit says "this account is
    # spent". When upstream says both, only the second is true of the account,
    # and the inferred code is where that survives: every consumer downstream
    # reads the code, not the sentence it was read from.
    code = _infer_websocket_handshake_error_code(status, message)

    assert code == "usage_limit_reached"
    assert is_upstream_usage_limit_rejection(error_code=code, message=None) is True


@pytest.mark.parametrize("status", [403, 503, None])
def test_websocket_handshake_throttling_hint_survives_a_non_429_status(status):
    # Nothing here asserts the usage limit, so the generic reading is the right
    # one -- and the status cannot stand in for it. A handshake rejected
    # without a 429 has only its message to be read from.
    code = _infer_websocket_handshake_error_code(status, "Rate limit exceeded. Please slow down.")

    assert code == "rate_limit_exceeded"
    assert is_upstream_usage_limit_rejection(error_code=code, message=None) is False


def test_websocket_handshake_payload_carries_the_inferred_reading_to_the_client():
    request_info = cast(RequestInfo, SimpleNamespace(real_url="wss://chatgpt.com/backend-api/codex/responses"))

    exhausted = _error_payload_from_websocket_handshake_error(
        aiohttp.WSServerHandshakeError(
            request_info, (), status=429, message=_HANDSHAKE_USAGE_LIMIT_WITH_THROTTLING_TEXT
        )
    )
    throttled = _error_payload_from_websocket_handshake_error(
        aiohttp.WSServerHandshakeError(request_info, (), status=403, message="Rate limit exceeded. Please slow down.")
    )

    assert exhausted["error"]["code"] == "usage_limit_reached"
    assert throttled["error"]["code"] == "rate_limit_exceeded"
    assert throttled["error"]["type"] == "rate_limit_error"


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

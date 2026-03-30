from __future__ import annotations

import pytest

from app.core.clients.proxy import _error_event_from_response, _error_payload_from_response

pytestmark = pytest.mark.unit


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

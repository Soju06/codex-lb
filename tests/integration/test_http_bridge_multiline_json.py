from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from app.core.clients.native_egress import NativeWebSocketRoutingMetadata
from app.core.clients.proxy_websocket import UpstreamWebSocketMessage
from app.core.utils.sse import parse_sse_data_json
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy import service as proxy_service
from app.modules.proxy._service.http_bridge import helpers
from tests.integration.test_http_responses_bridge import (
    _assert_created_text_delta_completed,
    _cleanup_http_bridge_sessions,  # noqa: F401
    _collect_sse_events,
    _PromotionUpstreamWebSocket,
)
from tests.integration.test_http_responses_bridge import (
    promotion_transport as promotion_transport,
)

pytestmark = pytest.mark.integration


class _FormattedUpstream(_PromotionUpstreamWebSocket):
    def __init__(self, style: str, native: bool, metadata_first: bool) -> None:
        super().__init__("resp_formatted")
        self.style, self.native, self.metadata_first = style, native, metadata_first

    async def send_text(self, text: str) -> None:
        if self.sent_text:
            await super().send_text(text)
            return
        self.sent_text.append(text)
        frames = [
            {
                "type": "error",
                "status": 400,
                "error": {
                    "code": "unsupported_value",
                    "type": "invalid_request_error",
                    "param": "parallel_tool_calls",
                    "message": "Unsupported parameter value.",
                },
            }
        ]
        if self.metadata_first:
            frames.insert(0, {"type": "codex.response.metadata", "headers": {}})
        for frame in frames:
            await self._messages.put(UpstreamWebSocketMessage("text", text=json.dumps(frame)))

    async def receive(self) -> UpstreamWebSocketMessage:
        message = await super().receive()
        assert message.text is not None
        payload = json.loads(message.text)
        text = json.dumps(payload, indent=2 if self.style != "compact" else None)
        if self.style == "crlf":
            text = " \r\n" + text.replace("\n", "\r\n") + "\r\n "
        response_id = (payload.get("response") or {}).get("id")
        return replace(
            message,
            text=text,
            responses_interpreted=self.native,
            payload=payload if self.native else None,
            event_type=payload["type"] if self.native else None,
            routing=NativeWebSocketRoutingMetadata(response_id, None) if self.native else None,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/responses", "/v1/responses/", "/backend-api/codex/responses"])
@pytest.mark.parametrize(
    "style,native,metadata_first",
    [
        ("compact", False, False),
        ("pretty", False, False),
        ("pretty", False, True),
        ("crlf", True, True),
    ],
)
async def test_formatted_error_settles_without_retry_and_next_turn_recovers(
    async_client,
    app_instance,
    promotion_transport,
    monkeypatch,
    path: str,
    style: str,
    native: bool,
    metadata_first: bool,
) -> None:
    upstreams, raw_calls, _ = promotion_transport
    upstream = _FormattedUpstream(style, native, metadata_first)
    connect = AsyncMock(return_value=upstream)
    monkeypatch.setattr(proxy_service, "connect_responses_websocket", connect)
    monkeypatch.setattr(helpers, "HTTP_BRIDGE_STUCK_GATE_RETIRE_AFTER_SECONDS", 0.1)
    service = get_proxy_service_for_app(app_instance)
    health = AsyncMock()
    monkeypatch.setattr(service, "_handle_stream_error", health)
    body = {
        "model": "gpt-5.4",
        "instructions": "test",
        "input": "hello",
        "stream": True,
        "prompt_cache_key": "formatted-error-test",
    }

    response = await async_client.post(path, json=body)

    if response.status_code == 400:
        error = response.json()["error"]
    else:
        assert response.status_code == 200
        events = [event for block in response.text.split("\n\n") if (event := parse_sse_data_json(block)) is not None]
        terminal = next(e for e in events if e["type"] in {"error", "response.failed"})
        detail = terminal["response"] if terminal["type"] == "response.failed" else terminal
        assert isinstance(detail, dict)
        error = detail["error"]
    assert isinstance(error, dict)
    assert error["code"] == "unsupported_value"
    assert error["type"] == "invalid_request_error"
    assert error["param"] == "parallel_tool_calls"
    assert len(upstream.sent_text) == 1
    connect.assert_awaited_once()
    health.assert_not_awaited()
    assert not upstreams and not raw_calls

    events = await _collect_sse_events(async_client, path, json_body={**body, "input": "next"})
    _assert_created_text_delta_completed(events)
    assert len(upstream.sent_text) == 2
    connect.assert_awaited_once()
    health.assert_not_awaited()


class _RejectedThenValidUpstream(_PromotionUpstreamWebSocket):
    def __init__(self, rejected: str, native: bool, validation_error: bool) -> None:
        super().__init__("resp_after_rejected_frame")
        self.rejected, self.native, self.validation_error = rejected, native, validation_error

    async def send_text(self, text: str) -> None:
        rejected = UpstreamWebSocketMessage(
            "text",
            text=self.rejected,
            responses_interpreted=self.native,
            payload=json.loads(self.rejected) if self.native else None,
            event_type="response.output_text.delta" if self.native else None,
            routing=NativeWebSocketRoutingMetadata(None, None) if self.native else None,
        )
        if self.validation_error:
            self.sent_text.append(text)
            await self._messages.put(rejected)
            await self._messages.put(
                UpstreamWebSocketMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "error",
                            "status": 400,
                            "error": {
                                "code": "unsupported_value",
                                "type": "invalid_request_error",
                                "param": "parallel_tool_calls",
                                "message": "Unsupported parameter value.",
                            },
                        },
                        indent=2,
                    ),
                )
            )
        else:
            await super().send_text(text)
            valid = [self._messages.get_nowait() for _ in range(self._messages.qsize())]
            await self._messages.put(rejected)
            for message in valid:
                await self._messages.put(message)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/v1/responses", "/backend-api/codex/responses"])
@pytest.mark.parametrize("native", [False, True], ids=["opaque", "native"])
@pytest.mark.parametrize("validation_error", [False, True], ids=["then-complete", "then-error"])
async def test_rejected_frame_is_not_forwarded_or_assigned_to_the_pending_turn(
    async_client,
    app_instance,
    promotion_transport,
    monkeypatch,
    path: str,
    native: bool,
    validation_error: bool,
) -> None:
    rejected = '{"type":"response.output_text.delta","delta":"rejected_marker","nested":{"n":1e400}}'
    upstream = _RejectedThenValidUpstream(rejected, native, validation_error)
    connect = AsyncMock(return_value=upstream)
    monkeypatch.setattr(proxy_service, "connect_responses_websocket", connect)
    service = get_proxy_service_for_app(app_instance)
    health = AsyncMock()
    monkeypatch.setattr(service, "_handle_stream_error", health)
    body = {
        "model": "gpt-5.4",
        "instructions": "test",
        "input": "hello",
        "stream": True,
        "prompt_cache_key": "rejected-frame-route",
    }

    response = await async_client.post(path, json=body)

    assert "rejected_marker" not in response.text
    assert "1e400" not in response.text and "Infinity" not in response.text
    if validation_error and response.status_code == 400:
        assert response.json()["error"]["code"] == "unsupported_value"
    else:
        assert response.status_code == 200
        events = [e for block in response.text.split("\n\n") if (e := parse_sse_data_json(block)) is not None]
        if validation_error:
            terminal = next(e for e in events if e["type"] in {"error", "response.failed"})
            detail = terminal["response"] if terminal["type"] == "response.failed" else terminal
            assert isinstance(detail, dict)
            error = detail["error"]
            assert isinstance(error, dict) and error["code"] == "unsupported_value"
        else:
            _assert_created_text_delta_completed([e for e in events if e["type"] != "codex.keepalive"])
    assert len(upstream.sent_text) == 1
    connect.assert_awaited_once()
    health.assert_not_awaited()
    for session in service._http_bridge_sessions.values():
        assert not session.pending_requests and session.queued_request_count == 0
        assert not session.response_create_gate.locked()

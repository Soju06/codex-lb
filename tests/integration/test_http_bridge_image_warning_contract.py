"""Offline HTTP route coverage for uncertain, unacknowledged image sends."""

# ruff: noqa: F811 - imported pytest fixtures are injected by name.

import json

import pytest

from tests.integration.test_http_bridge_peer_1009_send import (
    inline_bridge_transport,  # noqa: F401
)
from tests.integration.test_http_responses_bridge import (
    _cleanup_http_bridge_sessions,  # noqa: F401
    _FakeBridgeUpstreamWebSocket,
    _FakeUpstreamMessage,
    _PromotionUpstreamWebSocket,
)


class _UnacknowledgedFailure(_FakeBridgeUpstreamWebSocket):
    def __init__(self, failure: str) -> None:
        super().__init__()
        self.failure = failure

    async def send_text(self, text: str) -> None:
        self.sent_text.append(text)
        if self.failure == "send":
            raise ConnectionResetError("socket reset after handing response.create to the transport")
        code = {"generic": None, "clean": 1000, "peer1009": 1009}[self.failure]
        await self._messages.put(_FakeUpstreamMessage("close", close_code=code))


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["/v1/responses", "/v1/responses/", "/backend-api/codex/responses"])
@pytest.mark.parametrize("failure", ["generic", "clean", "send", "peer1009"])
async def test_unacknowledged_image_warning_contract(async_client, inline_bridge_transport, route, failure):
    state = inline_bridge_transport
    upstream = _UnacknowledgedFailure(failure)
    state.upstreams[:] = [upstream]

    response = await async_client.post(
        route,
        json={
            "model": "gpt-5.4",
            "instructions": "Return OK",
            "stream": True,
            "prompt_cache_key": "image-warning-contract",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_image",
                            "image_url": (
                                "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
                                "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
                            ),
                        },
                    ],
                }
            ],
        },
        follow_redirects=True,
    )

    sse_failure = route == "/backend-api/codex/responses" and failure in {"generic", "clean"}
    assert response.status_code == (200 if sse_failure else 400 if failure == "peer1009" else 502)
    if sse_failure:
        events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")]
        error = next(event["response"]["error"] for event in events if event["type"] == "response.failed")
    else:
        error = response.json()["error"]
    assert error["code"] == ("payload_too_large" if failure == "peer1009" else "stream_incomplete")
    assert state.connects == 1
    assert len(upstream.sent_text) == 1
    message = error["message"].lower()
    if failure == "peer1009":
        assert "may still be executing" not in message
        assert "duplicate" not in message
    else:
        assert "may still be executing" in message
        assert "retry" in message and "duplicate" in message


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["generic", "clean", "send"])
async def test_text_failure_retains_retry_contract(async_client, inline_bridge_transport, failure):
    state = inline_bridge_transport
    upstream = _UnacknowledgedFailure(failure)
    healthy = _PromotionUpstreamWebSocket()
    state.upstreams[:] = [upstream, healthy]
    response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.4",
            "instructions": "Return OK",
            "stream": True,
            "prompt_cache_key": "text-warning-control",
            "input": "hello",
        },
    )
    assert len(upstream.sent_text) == 1
    assert "may still be executing" not in response.text.lower()
    assert "duplicate upstream work" not in response.text.lower()
    if failure == "send":
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "stream_incomplete"
        assert state.connects == 1
        assert not healthy.sent_text
    else:
        assert response.status_code == 200
        assert "response.completed" in response.text
        assert state.connects == 2
        assert len(healthy.sent_text) == 1

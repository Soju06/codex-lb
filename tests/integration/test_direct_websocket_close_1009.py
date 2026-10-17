from __future__ import annotations

import json
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_proxy_service_for_app
from app.modules.proxy import api as proxy_api
from app.modules.proxy import service as proxy_service
from tests.integration.test_proxy_websocket_responses import (
    _FakeUpstreamMessage,
    _SequencedUpstreamWebSocket,
    _websocket_settings,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("path", ["/v1/responses", "/backend-api/codex/responses"])
@pytest.mark.parametrize("after_output", [False, True])
def test_direct_close_1009_is_terminal_without_replay_or_account_penalty(
    app_instance, monkeypatch, path: str, after_output: bool
) -> None:
    events = []
    if after_output:
        events.extend(
            [
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.created",
                            "response": {"id": "resp_size", "status": "in_progress"},
                        }
                    ),
                ),
                _FakeUpstreamMessage(
                    "text",
                    text=json.dumps(
                        {
                            "type": "response.output_text.delta",
                            "response_id": "resp_size",
                            "delta": "partial",
                            "output_index": 0,
                            "content_index": 0,
                        }
                    ),
                ),
            ]
        )
    events.append(_FakeUpstreamMessage("close", close_code=1009))
    upstream = _SequencedUpstreamWebSocket([], deferred_message_batches=[events])
    connect = AsyncMock(return_value=(SimpleNamespace(id="only-account"), upstream))
    health = AsyncMock()
    logs = AsyncMock()
    monkeypatch.setattr(proxy_api, "_websocket_firewall_denial_response", AsyncMock(return_value=None))
    monkeypatch.setattr(proxy_api, "validate_proxy_api_key_authorization", AsyncMock(return_value=None))
    monkeypatch.setattr(
        proxy_service, "get_settings_cache", lambda: SimpleNamespace(get=AsyncMock(return_value=_websocket_settings()))
    )
    monkeypatch.setattr(proxy_service.ProxyService, "_connect_proxy_websocket", connect)
    monkeypatch.setattr(proxy_service.ProxyService, "_handle_stream_error", health)
    monkeypatch.setattr(proxy_service.ProxyService, "_write_request_log", logs)
    body = {
        "type": "response.create",
        "model": "gpt-5.4",
        "instructions": "",
        "input": [{"role": "user", "content": "hello"}],
        "stream": True,
    }

    with TestClient(app_instance) as client:
        with client.websocket_connect(path) as websocket:
            websocket.send_text(json.dumps(body))
            received = [json.loads(websocket.receive_text()) for _ in range(3 if after_output else 1)]
        assert client.portal is not None
        client.portal.call(partial(get_proxy_service_for_app(app_instance).drain_persistence_tasks, timeout_seconds=5))

    terminal = received[-1]
    error = terminal["response"]["error"] if terminal["type"] == "response.failed" else terminal["error"]
    assert error["code"] == "payload_too_large"
    assert error["type"] == "invalid_request_error"
    assert error["param"] == "input"
    if after_output:
        assert [e["delta"] for e in received if e["type"] == "response.output_text.delta"] == ["partial"]
    assert len(upstream.sent_text) == 1
    connect.assert_awaited_once()
    health.assert_not_awaited()
    assert logs.await_count == 1
    assert logs.await_args is not None
    assert logs.await_args.kwargs["error_code"] == "payload_too_large"
    assert upstream.closed

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.dependencies import get_proxy_service_for_app
from app.modules.proxy import service as proxy_service
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.test_http_responses_bridge import (
    _cleanup_http_bridge_sessions as _cleanup_http_bridge_sessions,
)
from tests.integration.test_http_responses_bridge import (
    _collect_sse_events,
    _FakeBridgeUpstreamWebSocket,
    _get_account,
    _import_account,
    _install_bridge_settings,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True], ids=["json", "sse"])
@pytest.mark.parametrize("model", ["gpt-5.4", "gpt-6-astra"])
async def test_http_bridge_preserves_payload_without_steering_snapshot(
    async_client, app_instance, monkeypatch, model: str, stream: bool
) -> None:
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(async_client, "acc_http_snapshot", "snapshot@example.com")
    account = await _get_account(account_id)
    service = get_proxy_service_for_app(app_instance)
    prepared = []
    snapshot_present_at_dispatch = []
    original_prepare = service._prepare_http_bridge_request

    def prepare(*args, **kwargs):
        state, text = original_prepare(*args, **kwargs)
        prepared.append(state)
        return state, text

    class Upstream(_FakeBridgeUpstreamWebSocket):
        async def send_text(self, text: str) -> None:
            assert len(prepared) == 1
            assert prepared[0].transport == "http"
            snapshot_present_at_dispatch.append(getattr(prepared[0], "steering_configuration", None) is not None)
            await super().send_text(text)

    upstream = Upstream()
    monkeypatch.setattr(service, "_prepare_http_bridge_request", prepare)
    monkeypatch.setattr(
        service,
        "_select_account_with_budget",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None, error_code=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh_with_budget", AsyncMock(return_value=account))
    monkeypatch.setattr(proxy_service, "connect_responses_websocket", AsyncMock(return_value=upstream))
    input_items = [{"role": "user", "content": [{"type": "input_text", "text": "HTTP input " * 8192}]}]
    body = {"model": model, "instructions": "Return exactly OK.", "input": input_items, "stream": stream}
    if stream:
        events = await _collect_sse_events(async_client, "/v1/responses", json_body=body)
        assert events[0]["type"] == "response.created"
        assert events[-1]["type"] == "response.completed"
        result = events[-1]["response"]
    else:
        response = await async_client.post("/v1/responses", json=body)
        assert response.status_code == 200
        result = response.json()

    assert result["id"] == "resp_bridge_1"
    assert result["usage"]["total_tokens"] == 26
    assert await service.drain_persistence_tasks(timeout_seconds=2)
    assert len(upstream.sent_text) == 1
    forwarded = json.loads(upstream.sent_text[0])
    assert forwarded["model"] == model
    assert forwarded["instructions"] == body["instructions"]
    assert forwarded["input"] == input_items
    assert snapshot_present_at_dispatch == [False]

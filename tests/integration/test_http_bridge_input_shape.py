from __future__ import annotations

import json
import socket

import pytest

from app.dependencies import get_proxy_service_for_app
from app.modules.proxy._service.http_bridge import quarantine
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration import test_http_responses_bridge as bridge

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("input_shape", ["parallel_outputs", "short_raw_string", "large_single_output"])
async def test_quarantined_http_continuation_keeps_durable_anchor(async_client, app_instance, monkeypatch, input_shape):
    bridge._install_bridge_settings_with_limits(monkeypatch, enabled=True, instance_id=socket.gethostname())
    account_id = await bridge._import_account(async_client, "shape-account", "shape@example.com")
    account = await bridge._get_account(account_id)
    upstreams = [
        bridge._FakeBridgeUpstreamWebSocket("resp_shape_first"),
        bridge._FakeBridgeUpstreamWebSocket("resp_shape_next"),
    ]
    connected = []

    async def select_account(self, deadline, **kwargs):
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def ensure_fresh(self, target, *, force=False, timeout_seconds):
        return target

    async def connect(headers, access_token, account_id_header, *, base_url=None, session=None):
        upstream = upstreams[len(connected)]
        connected.append(upstream)
        return upstream

    monkeypatch.setattr(bridge.proxy_module.ProxyService, "_select_account_with_budget", select_account)
    monkeypatch.setattr(bridge.proxy_module.ProxyService, "_ensure_fresh_with_budget", ensure_fresh)
    monkeypatch.setattr(bridge.proxy_module, "connect_responses_websocket", connect)
    headers = {"x-codex-session-id": "shape-session", "x-codex-turn-state": "shape-turn"}
    service = get_proxy_service_for_app(app_instance)
    try:
        first = await async_client.post(
            "/v1/responses", json={"model": "gpt-5.1", "input": "first question"}, headers=headers
        )
        assert first.status_code == 200, first.text
        first_id = first.json()["id"]
        session = next(iter(service._http_bridge_sessions.values()))
        quarantine._quarantine_http_bridge_session(
            service, session, reason=quarantine._HTTP_BRIDGE_QUARANTINE_WEDGED_REATTACH_REASON
        )
        continuation = (
            [
                {"type": "function_call_output", "call_id": "call_a", "output": "a"},
                {"type": "function_call_output", "call_id": "call_b", "output": "b"},
            ]
            if input_shape == "parallel_outputs"
            else "x" * 4095
        )
        if input_shape == "large_single_output":
            continuation = [{"type": "function_call_output", "call_id": "call_a", "output": "x" * 4096}]
        response = await async_client.post(
            "/v1/responses", json={"model": "gpt-5.1", "input": continuation}, headers=headers
        )
        assert response.status_code == 200, response.text
        assert len(connected) == 2
        sent = json.loads(upstreams[1].sent_text[0])
        assert sent["previous_response_id"] == first_id
        if input_shape in {"parallel_outputs", "large_single_output"}:
            assert sent["input"] == continuation
        else:
            assert sent["input"] == [{"role": "user", "content": [{"type": "input_text", "text": continuation}]}]
    finally:
        for live in list(service._http_bridge_sessions.values()):
            await service._close_http_bridge_session(live)

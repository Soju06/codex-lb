from __future__ import annotations

import json
from unittest.mock import AsyncMock

import anyio
import pytest
from sqlalchemy import select

import app.modules.proxy.service as proxy_module
from app.core.openai.requests import ResponsesRequest
from app.db.models import HttpBridgeSessionRecord
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy._service.http_bridge import streaming as bridge_streaming
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.test_astra_inherited_policy import _reasoning_key
from tests.integration.test_http_responses_bridge import (
    _cleanup_http_bridge_sessions as _cleanup_http_bridge_sessions,
)
from tests.integration.test_http_responses_bridge import (
    _FakeBridgeUpstreamWebSocket,
    _get_account,
    _import_account,
    _install_bridge_settings,
)
from tests.integration.test_openai_compat_features import _completed_event

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("stream", [False, True], ids=["collect", "stream"])
async def test_astra_full_resend_preserves_bridge_prefix(async_client, monkeypatch, app_instance, stream: bool) -> None:
    # Given a real route, bridge and durable store, with only upstream I/O faked.
    account_id = await _import_account(async_client, "astra-history", "astra-history@example.com")
    account = await _get_account(account_id)
    key = await _reasoning_key(async_client, allowed=["high"])
    _install_bridge_settings(monkeypatch, enabled=True)
    upstream = _FakeBridgeUpstreamWebSocket()
    service = get_proxy_service_for_app(app_instance)
    monkeypatch.setattr(
        service,
        "_select_account_with_budget",
        AsyncMock(return_value=AccountSelection(account=account, error_message=None)),
    )
    monkeypatch.setattr(service, "_ensure_fresh_with_budget", AsyncMock(return_value=account))
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", AsyncMock(return_value=upstream))
    registered = {f"resp_bridge_{turn}": anyio.Event() for turn in (1, 2, 3)}
    original_register = service._register_http_bridge_previous_response_id

    async def register_response(session, response_id, **kwargs):
        result = await original_register(session, response_id, **kwargs)
        assert result
        registered[response_id].set()
        return result

    monkeypatch.setattr(service, "_register_http_bridge_previous_response_id", register_response)
    headers = {"Authorization": f"Bearer {key}", "thread-id": "astra-history-thread"}
    body = {
        "model": "gpt-6-astra",
        "instructions": "",
        "reasoning": {"effort": "high"},
        "stream": stream,
    }

    async def post_turn(input_items, response_id, *, previous_response_id=None):
        request_body = {**body, "input": input_items}
        if previous_response_id is not None:
            request_body["previous_response_id"] = previous_response_id
        with anyio.fail_after(5):
            response = await async_client.post("/v1/responses", json=request_body, headers=headers)
            assert response.status_code == 200, response.text
            if stream:
                events = [
                    json.loads(line[6:])
                    for line in response.text.splitlines()
                    if line.startswith("data: ") and line != "data: [DONE]"
                ]
                assert any(
                    event.get("type") == "response.completed" and event["response"]["id"] == response_id
                    for event in events
                )
            else:
                assert response.json()["id"] == response_id
            await registered[response_id].wait()

    await post_turn([{"role": "user", "content": "First"}], "resp_bridge_1")
    history = [
        {"type": "message", "role": "assistant", "id": "msg_1", "status": "completed", "content": "done"},
        {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "shell", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_1", "output": "ok"},
        {"role": "user", "content": "Continue"},
    ]
    normalized_history = ResponsesRequest.model_validate({**body, "input": history}).input
    assert isinstance(normalized_history, list)

    # When a marked full resend is validated, trimmed, reset and completed.
    await post_turn(history, "resp_bridge_2", previous_response_id="resp_bridge_1")

    # Then durable bookkeeping describes client history, not reset + delta.
    async with SessionLocal() as db:
        stored = (
            await db.execute(
                select(HttpBridgeSessionRecord).where(HttpBridgeSessionRecord.latest_response_id == "resp_bridge_2")
            )
        ).scalar_one()
        assert stored.latest_input_item_count == len(normalized_history)
        assert stored.latest_input_full_fingerprint == proxy_module._fingerprint_input_items(normalized_history)
    reset = {"type": "configuration_update", "reasoning": {"effort": "high"}}
    assert json.loads(upstream.sent_text[1])["input"] == [reset, *normalized_history[2:]]

    # A later unanchored full resend must match and reuse the completed anchor.
    await post_turn([*history, {"role": "user", "content": "Next"}], "resp_bridge_3")
    continuation = json.loads(upstream.sent_text[2])
    assert continuation["previous_response_id"] == "resp_bridge_2"
    assert continuation["input"] == [
        reset,
        {"role": "user", "content": "Next"},
    ]


@pytest.mark.parametrize("stream", [False, True], ids=["collect", "stream"])
@pytest.mark.parametrize("bridge_enabled", [False, True], ids=["disabled", "size-fallback"])
async def test_astra_full_resend_http_fallback_keeps_reset(
    async_client, monkeypatch, stream: bool, bridge_enabled: bool
) -> None:
    await _import_account(async_client, "astra-fallback", "astra-fallback@example.com")
    key = await _reasoning_key(async_client, allowed=["high"])
    _install_bridge_settings(monkeypatch, enabled=bridge_enabled)
    # Exercise the runtime size bypass after the route has chosen the bridge.
    monkeypatch.setattr(bridge_streaming, "_ws_transport_payload_budget_bytes", lambda settings: 1)
    forwarded = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        forwarded.append(payload.to_payload())
        yield _completed_event("resp_fallback")

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    tool_output = {"type": "function_call_output", "call_id": "call_1", "output": "ok"}
    with anyio.fail_after(5):
        response = await async_client.post(
            "/v1/responses",
            json={
                "model": "gpt-6-astra",
                "instructions": "",
                "reasoning": {"effort": "high"},
                "previous_response_id": "resp_stored",
                "stream": stream,
                "input": [
                    {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "shell", "arguments": "{}"},
                    tool_output,
                ],
            },
            headers={"Authorization": f"Bearer {key}"},
        )
    assert response.status_code == 200, response.text
    assert len(forwarded) == 1
    assert forwarded[0]["previous_response_id"] == "resp_stored"
    assert forwarded[0]["input"] == [
        {"type": "configuration_update", "reasoning": {"effort": "high"}},
        tool_output,
    ]

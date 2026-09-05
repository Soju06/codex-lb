from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.core.types import JsonValue
from app.dependencies import get_proxy_service_for_app
from app.modules.proxy import service as proxy_module
from app.modules.proxy.load_balancer import AccountSelection
from tests.integration.test_http_responses_bridge import (
    _get_account,
    _import_account,
    _install_bridge_settings,
)
from tests.unit.test_astra_async_tools import ScriptedUpstream, response

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("call_type", ["function_call", "custom_tool_call"])
async def test_http_bridge_async_result_spans_intervening_turn(async_client, app_instance, monkeypatch, call_type):
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(async_client, "acc_astra_http", "astra-http@example.com")
    account = await _get_account(account_id)
    call = {"type": call_type, "call_id": "async_1", "name": "slow", "async": True}
    call["arguments" if call_type == "function_call" else "input"] = "{}"
    sync = {"type": "function_call", "call_id": "sync_1", "name": "now", "arguments": "{}"}
    output_type = "function_call_output" if call_type == "function_call" else "custom_tool_call_output"
    actual = {"type": output_type, "call_id": "async_1", "output": "ready"}
    upstream = ScriptedUpstream(
        [
            [
                response("response.created", "r1"),
                {"type": "response.output_item.added", "response_id": "r1", "item": call},
                {"type": "response.output_item.done", "response_id": "r1", "item": call},
                {"type": "response.output_item.added", "response_id": "r1", "item": sync},
                {"type": "response.output_item.done", "response_id": "r1", "item": sync},
                response("response.completed", "r1", output=[call, sync]),
            ],
            [response("response.created", "r2", parent="r1"), response("response.completed", "r2", parent="r1")],
            [response("response.created", "r3", parent="r2"), response("response.completed", "r3", parent="r2")],
        ]
    )

    async def select_account(*args, **kwargs):
        return AccountSelection(account=account, error_message=None, error_code=None)

    async def fresh(self, target, **kwargs):
        return target

    async def connect(*args, **kwargs):
        return upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_select_account_with_budget", select_account)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fresh)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    base = {"model": "gpt-6-astra", "instructions": "", "prompt_cache_key": "astra-http"}
    first = await async_client.post("/v1/responses", json={**base, "input": "Begin"})
    assert first.status_code == 200, first.text
    second = await async_client.post("/v1/responses", json={**base, "previous_response_id": "r1", "input": "Continue"})
    assert second.status_code == 200, second.text
    third = await async_client.post("/v1/responses", json={**base, "previous_response_id": "r2", "input": [actual]})
    assert third.status_code == 200, third.text
    sent = upstream.sent
    synthetic = [item for item in sent[1]["input"] if item.get("type") == "function_call_output"]
    assert [item["call_id"] for item in synthetic] == ["sync_1"]
    assert sent[2]["input"] == [actual]
    service = get_proxy_service_for_app(app_instance)
    assert await service.drain_persistence_tasks(timeout_seconds=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("call_type", ["function_call", "custom_tool_call"])
@pytest.mark.parametrize(
    "path",
    ["/v1/responses", "/v1/responses/", "/backend-api/codex/responses", "/backend-api/codex/responses/"],
)
async def test_http_bridge_malformed_async_suffix_fails_closed(
    async_client, app_instance, monkeypatch, call_type: str, path: str
) -> None:
    _install_bridge_settings(monkeypatch, enabled=True)
    account_id = await _import_account(async_client, "acc_async_malformed", "async-malformed@example.com")
    paused = await async_client.post(f"/api/accounts/{account_id}/pause")
    assert paused.status_code == 200, paused.text
    service = get_proxy_service_for_app(app_instance)
    stored: list[JsonValue] = [{"role": "user", "content": "first"}]
    claimed = await service._durable_bridge.claim_live_session(
        session_key_kind="session_header",
        session_key_value="async-malformed",
        api_key_id=None,
        instance_id="instance-a",
        owner_process_epoch="previous-process",
        lease_ttl_seconds=60.0,
        account_id=account_id,
        model="gpt-6-astra",
        service_tier=None,
        latest_turn_state="http_turn_async_malformed",
        latest_response_id="resp_async_malformed",
        allow_takeover=True,
    )
    await service._durable_bridge.renew_live_session(
        session_id=claimed.session_id,
        api_key_id=None,
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
        lease_ttl_seconds=60.0,
        latest_response_id="resp_async_malformed",
        latest_input_item_count=1,
        latest_input_full_fingerprint=proxy_module._fingerprint_input_items(stored),
        latest_pending_tool_calls={"sync_1": "function_call"},
    )
    await service._durable_bridge.release_live_session(
        session_id=claimed.session_id,
        instance_id="instance-a",
        owner_epoch=claimed.owner_epoch,
        draining=False,
    )
    connect = AsyncMock(side_effect=AssertionError("malformed replay must not connect upstream"))
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    call: dict[str, JsonValue] = {"type": call_type, "name": "slow", "async": True}
    call["arguments" if call_type == "function_call" else "input"] = "{}"

    result = await asyncio.wait_for(
        async_client.post(
            path,
            headers={"session_id": "async-malformed"},
            json={
                "model": "gpt-6-astra",
                "instructions": "",
                "previous_response_id": "resp_async_malformed",
                "input": [
                    *stored,
                    call,
                    {"type": "function_call", "call_id": "sync_1", "name": "now", "arguments": "{}"},
                    {"type": "function_call_output", "call_id": "sync_1", "output": "ok"},
                ],
            },
        ),
        timeout=5,
    )

    assert result.status_code == 503, result.text
    error = result.json()["error"]
    assert error["code"] == "continuity_owner_policy_conflict"
    assert error["type"] == "server_error"
    connect.assert_not_awaited()

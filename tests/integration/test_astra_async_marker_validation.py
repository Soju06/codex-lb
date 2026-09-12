from __future__ import annotations

import asyncio
import json

import pytest

from app.dependencies import get_proxy_service_for_app
from app.modules.proxy import service as proxy_module
from tests.integration.test_http_responses_bridge import (
    _get_account,
    _import_account,
    _install_bridge_settings_with_limits,
)
from tests.unit.test_astra_async_tools import ScriptedUpstream, response

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("call_type", ["function_call", "custom_tool_call"])
@pytest.mark.parametrize("call_in_prefix", [False, True], ids=["suffix-call", "prefix-call"])
@pytest.mark.parametrize("proof", ["manifest", "retained-output"])
@pytest.mark.parametrize(
    "path", ["/v1/responses", "/v1/responses/", "/backend-api/codex/responses", "/backend-api/codex/responses/"]
)
@pytest.mark.parametrize(
    "fields,valid",
    [
        pytest.param({}, True, id="omitted"),
        pytest.param({"async": False}, True, id="false"),
        pytest.param({"async": True}, True, id="true"),
        pytest.param({"async": "true"}, False, id="string"),
        pytest.param({"async": []}, False, id="list"),
        pytest.param({"async": None}, False, id="null"),
        pytest.param({"async": 0}, False, id="zero"),
        pytest.param({"async": 1}, False, id="one"),
        pytest.param({"async": {}}, False, id="dict"),
    ],
)
async def test_http_owner_loss_validates_async_marker(
    async_client, app_instance, monkeypatch, call_type, call_in_prefix, proof, path, fields, valid
) -> None:
    _install_bridge_settings_with_limits(monkeypatch, enabled=True, instance_id="instance-a")
    owner_id = await _import_account(async_client, "acc_marker_owner", "marker-owner@example.com")
    owner = await _get_account(owner_id)
    call = {
        "type": call_type,
        "call_id": "marker_1",
        "name": "work",
        "arguments" if call_type == "function_call" else "input": "{}",
        **fields,
    }
    output = {"type": f"{call_type}_output", "call_id": "marker_1", "output": "done"}
    sync = {"type": "function_call", "call_id": "sync_1", "name": "now", "arguments": "{}"}
    answer = {
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": "finished"}],
    }
    stored = [{"role": "user", "content": "first"}]
    if call_in_prefix:
        stored.extend([call, output])
    first_output = [] if call_in_prefix else [call]
    first_output.append(sync if proof == "manifest" else answer)
    events = [response("response.created", "r1")]
    if proof == "manifest":
        # Exercise the real added/done/terminal lifecycle and manifest persistence.
        # A terminal-only tool response intentionally leaves that manifest unknown.
        for item in first_output:
            events.extend(
                [
                    {"type": "response.output_item.added", "response_id": "r1", "item": item},
                    {"type": "response.output_item.done", "response_id": "r1", "item": item},
                ]
            )
    events.append(response("response.completed", "r1", output=first_output))
    owner_upstream = ScriptedUpstream([events])
    recovered_upstream = ScriptedUpstream(
        [[response("response.created", "recovered"), response("response.completed", "recovered", output=[answer])]]
    )
    connected_accounts = []

    async def fresh(self, account, **kwargs):
        return account

    async def connect(headers, access_token, account_id_header, **kwargs):
        connected_accounts.append(account_id_header)
        return owner_upstream if len(connected_accounts) == 1 else recovered_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fresh)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    service = get_proxy_service_for_app(app_instance)
    registered = asyncio.Event()
    register = service._register_http_bridge_previous_response_id

    async def register_and_signal(session, response_id, **kwargs):
        result = await register(session, response_id, **kwargs)
        if response_id == "r1":
            registered.set()
        return result

    monkeypatch.setattr(service, "_register_http_bridge_previous_response_id", register_and_signal)
    base = {"model": "gpt-6-astra", "instructions": ""}
    headers = {"session_id": "async-marker"}
    first = await async_client.post(path, headers=headers, json={**base, "input": stored})
    assert first.status_code == 200, first.text
    await asyncio.wait_for(registered.wait(), timeout=5)
    lookup = await service._durable_bridge.lookup_request_targets(
        session_key_kind="session_header",
        session_key_value="async-marker",
        api_key_id=None,
        turn_state=None,
        session_header="async-marker",
        previous_response_id=None,
    )
    assert lookup is not None
    assert lookup.account_id == owner_id
    assert lookup.latest_response_id == "r1"
    assert lookup.latest_input_item_count == len(stored)
    assert lookup.latest_input_full_fingerprint == proxy_module._fingerprint_input_items(stored)
    if proof == "manifest":
        expected_calls = {"sync_1": "function_call"}
        if not call_in_prefix and fields.get("async") is not True:
            expected_calls["marker_1"] = call_type
        assert lookup.latest_pending_tool_calls == expected_calls
    else:
        assert not lookup.latest_pending_tool_calls
    assert await service.drain_persistence_tasks(timeout_seconds=5)
    assert await service.close_all_http_bridge_sessions()

    alternate_id = await _import_account(async_client, "acc_marker_alternate", "marker-alternate@example.com")
    alternate = await _get_account(alternate_id)
    paused = await async_client.post(f"/api/accounts/{owner_id}/pause")
    assert paused.status_code == 200, paused.text
    _install_bridge_settings_with_limits(monkeypatch, enabled=True, instance_id="instance-b")
    del app_instance.state.proxy_service
    recovering_service = get_proxy_service_for_app(app_instance)
    assert recovering_service is not service
    replay = list(stored) if call_in_prefix else [*stored, call, output]
    if proof == "manifest":
        replay.extend([sync, {"type": "function_call_output", "call_id": "sync_1", "output": "ok"}])
    else:
        replay.extend([answer, {"role": "user", "content": "continue"}])

    result = await asyncio.wait_for(
        async_client.post(path, headers=headers, json={**base, "previous_response_id": "r1", "input": replay}),
        timeout=5,
    )

    if valid:
        assert result.status_code == 200, result.text
        if path.startswith("/backend-api/"):
            events = [json.loads(line[6:]) for line in result.text.splitlines() if line.startswith("data: {")]
            assert events[-1]["type"] == "response.completed"
            assert events[-1]["response"]["id"] == "recovered"
        else:
            assert result.json()["id"] == "recovered"
        assert connected_accounts == [owner.chatgpt_account_id, alternate.chatgpt_account_id]
        assert len(recovered_upstream.sent) == 1
        assert recovered_upstream.sent[0]["input"] == replay
        assert "previous_response_id" not in recovered_upstream.sent[0]
    else:
        assert result.status_code == 502, result.text
        assert result.json()["error"]["code"] == "previous_response_owner_unavailable"
        assert result.json()["error"]["type"] == "server_error"
        assert connected_accounts == [owner.chatgpt_account_id]
        assert recovered_upstream.sent == []
    assert await recovering_service.drain_persistence_tasks(timeout_seconds=5)

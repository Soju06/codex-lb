from __future__ import annotations

import asyncio
import json

import pytest

from app.core.types import JsonValue
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
@pytest.mark.parametrize(
    "path", ["/v1/responses", "/v1/responses/", "/backend-api/codex/responses", "/backend-api/codex/responses/"]
)
@pytest.mark.parametrize(
    "continuation", ["unresolved", "delayed", "same-owner", "malformed", "mismatched", "no-boundary"]
)
async def test_http_bridge_no_manifest_async_recovery(
    async_client, app_instance, monkeypatch, call_type, call_in_prefix, path, continuation
) -> None:
    _install_bridge_settings_with_limits(monkeypatch, enabled=True, instance_id="instance-a")
    owner_id = await _import_account(async_client, "acc_async_owner", "async-owner@example.com")
    owner = await _get_account(owner_id)
    call = {"type": call_type, "call_id": "async_1", "name": "slow", "async": True}
    call["arguments" if call_type == "function_call" else "input"] = "{}"
    answer: dict[str, JsonValue] = {
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": "started"}],
    }
    first_output = [call] if call_in_prefix or continuation == "no-boundary" else [call, answer]
    owner_upstream = ScriptedUpstream(
        [
            [
                response("response.created", "r1"),
                {"type": "response.output_item.added", "response_id": "r1", "item": call},
                {"type": "response.output_item.done", "response_id": "r1", "item": call},
                response("response.completed", "r1", output=first_output),
            ],
            [
                response("response.created", "r2", parent="r1"),
                response("response.completed", "r2", parent="r1", output=[answer]),
            ],
        ]
    )
    recovered_upstream = ScriptedUpstream(
        [[response("response.created", "recovered"), response("response.completed", "recovered", output=[answer])]]
    )
    connected_accounts = []
    connected_headers = []

    async def fresh(self, account, **kwargs):
        return account

    async def connect(headers, access_token, account_id_header, **kwargs):
        connected_accounts.append(account_id_header)
        connected_headers.append(dict(headers))
        return owner_upstream if len(connected_accounts) == 1 else recovered_upstream

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fresh)
    monkeypatch.setattr(proxy_module, "connect_responses_websocket", connect)
    service = get_proxy_service_for_app(app_instance)
    registered = {"r1": asyncio.Event(), "r2": asyncio.Event()}
    register = service._register_http_bridge_previous_response_id

    async def register_and_signal(session, response_id, **kwargs):
        result = await register(session, response_id, **kwargs)
        if response_id in registered:
            registered[response_id].set()
        return result

    monkeypatch.setattr(service, "_register_http_bridge_previous_response_id", register_and_signal)
    base = {"model": "gpt-6-astra", "instructions": ""}
    headers = {"session_id": "async-durable"}
    stored = [{"role": "user", "content": "first"}]
    first = await async_client.post(path, headers=headers, json={**base, "input": stored})
    assert first.status_code == 200, first.text
    await asyncio.wait_for(registered["r1"].wait(), timeout=5)
    latest_id = "r1"
    if call_in_prefix:
        stored = [*stored, call, {"role": "user", "content": "intervening"}]
        second = await async_client.post(
            path, headers=headers, json={**base, "previous_response_id": "r1", "input": stored}
        )
        assert second.status_code == 200, second.text
        await asyncio.wait_for(registered["r2"].wait(), timeout=5)
        latest_id = "r2"
    lookup = await service._durable_bridge.lookup_request_targets(
        session_key_kind="session_header",
        session_key_value="async-durable",
        api_key_id=None,
        turn_state=None,
        session_header="async-durable",
        previous_response_id=None,
    )
    assert lookup is not None
    assert lookup.account_id == owner_id
    assert lookup.latest_response_id == latest_id
    assert not lookup.latest_pending_tool_calls
    if not call_in_prefix:
        assert lookup.latest_pending_tool_calls is None
    assert lookup.latest_input_item_count == len(stored)
    assert lookup.latest_input_full_fingerprint == proxy_module._fingerprint_input_items(stored)
    assert await service.drain_persistence_tasks(timeout_seconds=5)
    assert await service.close_all_http_bridge_sessions()

    alternate_id = await _import_account(async_client, "acc_async_alternate", "async-alternate@example.com")
    alternate = await _get_account(alternate_id)
    if continuation != "same-owner":
        pause = await async_client.post(f"/api/accounts/{owner_id}/pause")
        assert pause.status_code == 200, pause.text
    _install_bridge_settings_with_limits(monkeypatch, enabled=True, instance_id="instance-b")
    del app_instance.state.proxy_service
    recovering_service = get_proxy_service_for_app(app_instance)
    assert recovering_service is not service
    replay: list[JsonValue] = [*stored] if call_in_prefix else [*stored, call]
    if continuation != "no-boundary":
        replay.append(answer)
    if continuation in {"delayed", "same-owner", "malformed", "mismatched"}:
        output = {"type": f"{call_type}_output", "call_id": "async_1", "output": "done"}
        if continuation == "malformed":
            output.pop("output")
        elif continuation == "mismatched":
            output["type"] = "custom_tool_call_output" if call_type == "function_call" else "function_call_output"
        replay.append(output)
    else:
        replay.append({"role": "user", "content": "continue"})

    result = await asyncio.wait_for(
        async_client.post(path, headers=headers, json={**base, "previous_response_id": latest_id, "input": replay}),
        timeout=5,
    )

    if continuation in {"unresolved", "delayed", "same-owner"}:
        assert result.status_code == 200, result.text
        if path.startswith("/backend-api/"):
            events = [
                json.loads(line[6:])
                for line in result.text.splitlines()
                if line.startswith("data: ") and line[6:] != "[DONE]"
            ]
            assert events[-1]["type"] == "response.completed"
            assert events[-1]["response"]["id"] == "recovered"
        else:
            assert result.json()["id"] == "recovered"
        expected_account = owner if continuation == "same-owner" else alternate
        assert connected_accounts == [owner.chatgpt_account_id, expected_account.chatgpt_account_id]
        assert len(recovered_upstream.sent) == 1
        assert recovered_upstream.sent[0]["input"] == replay
        if continuation == "same-owner":
            assert recovered_upstream.sent[0]["previous_response_id"] == latest_id
        else:
            assert "previous_response_id" not in recovered_upstream.sent[0]
            assert "session_id" not in connected_headers[-1]
    else:
        assert result.status_code == 502, result.text
        assert result.json()["error"]["code"] == "previous_response_owner_unavailable"
        assert result.json()["error"]["type"] == "server_error"
        assert connected_accounts == [owner.chatgpt_account_id]
        assert recovered_upstream.sent == []
    assert await recovering_service.drain_persistence_tasks(timeout_seconds=5)

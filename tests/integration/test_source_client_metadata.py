"""Fresh Codex metadata must not create unknown source references."""

from __future__ import annotations

import copy

import pytest

from tests.integration import test_model_source_pool as pool_fixtures
from tests.integration import test_source_pool_client_tool_results as tool_fixtures
from tests.integration.test_model_source_pool import ROUTES, body, reservations
from tests.integration.test_source_reference_scope_replica import another_replica

make_pool = pool_fixtures.make_pool
pool_clock = pool_fixtures.pool_clock
tool_pool = tool_fixtures.tool_pool
pytestmark = pytest.mark.integration
META = {"turn_id": "turn_client", "create_time": 1790503200.25, "content_item_kinds": ["text"]}


def decorate(item):
    return {**item, "internal_chat_message_metadata_passthrough": copy.deepcopy(META)}


def client_items():
    return [
        decorate(
            {"type": "message", "id": f"msg_{role}", "role": role, "content": [{"type": "input_text", "text": "hello"}]}
        )
        for role in ("developer", "user")
    ]


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("stream", [False, True])
async def test_fresh_metadata_survives_pool_and_replica(async_client, make_pool, monkeypatch, route, stream):
    pool = await make_pool(5)
    items = client_items()
    # Existing request normalization moves the leading developer text into
    # instructions. Forwarded user metadata must remain byte-for-byte equal.
    original = copy.deepcopy(items[1:])
    first = await async_client.post(route, headers=pool.headers, json=body(stream=stream, input=items))
    assert first.status_code == 200, first.text
    async with another_replica(monkeypatch) as replica:
        second = await replica.post(route, headers=pool.headers, json=body(stream=stream, input=items))
        assert second.status_code == 200, second.text
        denied = await replica.post(
            route, headers=pool.headers, json=body(input=[{"type": "item_reference", "id": "msg_user"}])
        )
        assert denied.status_code == 409
    for _, sent in pool.calls:
        assert sent["input"] == original
        assert "hello" in sent["instructions"]
    assert len(pool.calls) == 2
    assert all(row.status == "finalized" for row in await reservations(pool))


@pytest.mark.parametrize("route", ["/v1/responses", "/backend-api/codex/responses/"])
async def test_metadata_supported_tool_and_agent_envelopes(async_client, make_pool, route):
    pool = await make_pool(5)
    items = client_items() + [
        decorate(item)
        for item in [
            {"type": "function_call", "id": "fc_local", "call_id": "call_local", "name": "run", "arguments": "{}"},
            {"type": "function_call_output", "id": "fco_local", "call_id": "call_local", "output": "done"},
            {"type": "custom_tool_call", "id": "ct_local", "call_id": "custom_local", "name": "patch", "input": "text"},
            {"type": "custom_tool_call_output", "id": "cto_local", "call_id": "custom_local", "output": "done"},
            {
                "type": "apply_patch_call",
                "id": "ap_local",
                "call_id": "patch_local",
                "operation": {"type": "delete_file", "path": "tmp.txt"},
            },
            {"type": "apply_patch_call_output", "id": "apo_local", "call_id": "patch_local", "output": "done"},
            {
                "type": "function_call_output",
                "id": "notice",
                "name": "send_message_to_thread",
                "output": "task complete",
            },
            {
                "type": "agent_message",
                "id": "agent_local",
                "author": "/root",
                "recipient": "/root/child",
                "content": [{"type": "encrypted_content", "encrypted_content": "inline_task"}],
            },
        ]
    ]
    response = await async_client.post(route, headers=pool.headers, json=body(input=items))
    assert response.status_code == 200, response.text
    assert pool.calls[-1][1]["input"] == items[1:]


@pytest.mark.parametrize("route", ["/v1/responses/", "/backend-api/codex/responses"])
@pytest.mark.parametrize("guard", ["same", "unknown-call", "replaced", "disabled", "mixed-owner", "disallowed"])
async def test_metadata_preserves_call_owner_across_replicas(async_client, tool_pool, monkeypatch, route, guard):
    pool, calls = tool_pool
    first = await async_client.post(route, headers=pool.headers, json=body())
    assert first.status_code == 200
    result = decorate(tool_fixtures.tool_result())
    items = client_items() + [result]
    payload = body(input=items, previous_response_id=first.json()["id"])
    if guard == "unknown-call":
        result["call_id"] = "unknown"
    elif guard == "replaced":
        assert (
            await async_client.patch(f"/api/model-sources/{pool.ids[0]}", json={"apiKey": "replacement"})
        ).status_code == 200
    elif guard == "disabled":
        assert (
            await async_client.patch(f"/api/model-sources/{pool.ids[0]}", json={"isEnabled": False})
        ).status_code == 200
    elif guard == "disallowed":
        assert (
            await async_client.patch(f"/api/api-keys/{pool.key_id}", json={"assignedSourceIds": pool.ids[1:]})
        ).status_code == 200
    elif guard == "mixed-owner":
        second = await async_client.post(route, headers=pool.headers, json=body())
        assert second.status_code == 200
        result["call_id"] = second.json()["output"][1]["call_id"]
    before = len(calls)
    async with another_replica(monkeypatch) as replica:
        response = await replica.post(route, headers=pool.headers, json=payload)
    assert response.status_code == (200 if guard == "same" else 409), response.text
    assert len(calls) == before + (guard == "same")
    if guard == "same":
        assert calls[-1][0] == "pool-0"
        assert calls[-1][1]["input"] == items[1:]


@pytest.mark.parametrize("route", ["/v1/responses", "/backend-api/codex/responses/"])
@pytest.mark.parametrize("guard", ["unknown-field", "bad-time", "bad-kinds", "reasoning", "assistant"])
async def test_metadata_cannot_make_unproven_input_portable(async_client, make_pool, route, guard):
    pool = await make_pool(5)
    items = client_items()
    metadata = items[-1]["internal_chat_message_metadata_passthrough"]
    if guard == "unknown-field":
        metadata["opaque_owner"] = "unknown"
    elif guard == "bad-time":
        metadata["create_time"] = True
    elif guard == "bad-kinds":
        metadata["content_item_kinds"] = [{"id": "owned"}]
    elif guard == "reasoning":
        items.append(decorate({"type": "reasoning", "encrypted_content": "unknown_cipher", "summary": []}))
    else:
        items.append(decorate({"type": "message", "role": "assistant", "id": "owned", "content": "old"}))
    response = await async_client.post(route, headers=pool.headers, json=body(input=items))
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "model_source_owner_unavailable"
    assert not pool.calls
    assert not await reservations(pool)

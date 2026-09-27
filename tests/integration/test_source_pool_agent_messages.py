"""Inline Codex agent messages reach pooled sources without losing ownership guards."""

from __future__ import annotations

import copy

import pytest

from tests.integration import test_model_source_pool as pool_fixtures
from tests.integration import test_source_pool_client_tool_results as tool_fixtures
from tests.integration.test_model_source_pool import MODEL, ROUTES, body, reservations
from tests.integration.test_source_reference_scope_replica import another_replica

make_pool = pool_fixtures.make_pool
pool_clock = pool_fixtures.pool_clock
tool_pool = tool_fixtures.tool_pool
pytestmark = pytest.mark.integration


def subtask_items(**extra):
    return [
        {
            "type": "message",
            "id": "msg_context",
            "role": "user",
            "content": [{"type": "input_text", "text": "Subtask context."}],
        },
        {
            "type": "agent_message",
            "id": "local-agent-task",
            "author": "/root",
            "recipient": "/root/probe",
            "content": [
                {"type": "input_text", "text": "Message Type: NEW_TASK\nPayload:\n"},
                {"type": "encrypted_content", "encrypted_content": "encrypted-agent-task"},
            ],
            **extra,
        },
        {
            "type": "message",
            "id": "msg_user",
            "role": "user",
            "content": [{"type": "input_text", "text": "Complete the subtask."}],
        },
    ]


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("encrypted", [False, True])
async def test_subtask_bootstrap_survives_pool_expansion_and_replica(
    async_client, make_pool, monkeypatch, route, stream, encrypted
):
    pool = await make_pool(5)
    assert (
        await async_client.patch(f"/api/api-keys/{pool.key_id}", json={"assignedSourceIds": pool.ids[:1]})
    ).status_code == 200
    payload = body(stream=stream, input=subtask_items())
    if not encrypted:
        payload["input"][1]["content"] = [{"type": "input_text", "text": "Do the task."}]
    original = copy.deepcopy(payload)
    first = await async_client.post(route, headers={**pool.headers, "thread-id": "parent"}, json=payload)
    assert first.status_code == 200, first.text
    assert (
        await async_client.patch(f"/api/api-keys/{pool.key_id}", json={"assignedSourceIds": pool.ids})
    ).status_code == 200
    async with another_replica(monkeypatch) as replica:
        child = await replica.post(route, headers={**pool.headers, "thread-id": "child"}, json=payload)
        assert child.status_code == 200, child.text
        assert "response.completed" in child.text if stream else child.json()["status"] == "completed"
        denied = await replica.post(
            route, headers=pool.headers, json=body(input=[{"type": "item_reference", "id": "local-agent-task"}])
        )
        assert denied.status_code == 409
    assert [token for token, _ in pool.calls] == ["pool-0", "pool-4"]
    assert all(sent["input"] == original["input"] for _, sent in pool.calls)
    assert len(await reservations(pool)) == 2
    assert all(row.status == "finalized" for row in await reservations(pool))


@pytest.mark.parametrize("route", ["/v1/responses/", "/backend-api/codex/responses/"])
@pytest.mark.parametrize(
    "guard",
    ["same", "unknown-call", "unknown-response", "disabled", "replaced", "disallowed", "different-key", "mixed-owner"],
)
async def test_agent_message_retains_ownership_boundaries(async_client, tool_pool, monkeypatch, route, guard):
    pool, calls = tool_pool
    first = await async_client.post(route, headers=pool.headers, json=body())
    assert first.status_code == 200
    payload = body(stream=True, input=subtask_items(), previous_response_id=first.json()["id"])
    headers = pool.headers
    if guard == "unknown-call":
        payload["input"].append({"type": "function_call_output", "call_id": "call_unknown", "output": "done"})
    elif guard == "unknown-response":
        payload["previous_response_id"] = "resp_unknown"
    elif guard == "disabled":
        assert (
            await async_client.patch(f"/api/model-sources/{pool.ids[0]}", json={"isEnabled": False})
        ).status_code == 200
    elif guard == "replaced":
        assert (
            await async_client.patch(f"/api/model-sources/{pool.ids[0]}", json={"apiKey": "changed"})
        ).status_code == 200
    elif guard == "disallowed":
        assert (
            await async_client.patch(f"/api/api-keys/{pool.key_id}", json={"assignedSourceIds": pool.ids[1:]})
        ).status_code == 200
    elif guard == "different-key":
        key = await async_client.post(
            "/api/api-keys/", json={"name": "other-client", "assignedSourceIds": pool.ids, "allowedModels": [MODEL]}
        )
        assert key.status_code == 200
        headers = {"Authorization": "Bearer " + key.json()["key"]}
    elif guard == "mixed-owner":
        second = await async_client.post(route, headers=pool.headers, json=body())
        assert second.status_code == 200
        payload["input"].extend(second.json()["output"])
    before_calls = len(calls)
    before_reservations = len(await reservations(pool))
    async with another_replica(monkeypatch) as replica:
        response = await replica.post(route, headers=headers, json=payload)
    assert response.status_code == (200 if guard == "same" else 409), response.text
    assert len(calls) == before_calls + (guard == "same")
    assert len(await reservations(pool)) == before_reservations + (guard == "same")
    if guard == "same":
        assert calls[-1][0] == "pool-0"
        assert calls[-1][1]["input"] == payload["input"]


@pytest.mark.parametrize("route", ["/v1/responses", "/backend-api/codex/responses"])
@pytest.mark.parametrize("retained", ["reasoning", "call"])
async def test_agent_message_keeps_owner_without_response_anchor(async_client, tool_pool, monkeypatch, route, retained):
    pool, calls = tool_pool
    first = await async_client.post(route, headers=pool.headers, json=body())
    assert first.status_code == 200
    items = subtask_items()
    items.append(
        first.json()["output"][0]
        if retained == "reasoning"
        else {"type": "function_call_output", "call_id": "call_pool-0", "output": "done"}
    )
    async with another_replica(monkeypatch) as replica:
        response = await replica.post(route, headers=pool.headers, json=body(input=items))
    assert response.status_code == 200, response.text
    assert calls[-1][0] == "pool-0"
    assert calls[-1][1]["input"] == items
    assert len(calls) == 2


@pytest.mark.parametrize("route", ["/v1/responses", "/backend-api/codex/responses"])
@pytest.mark.parametrize(
    "extra",
    [
        {"call_id": "unknown"},
        {"encrypted_content": "reasoning-state"},
        {"opaque_state": "unknown"},
        {"content": [{"type": "encrypted_content", "encrypted_content": "cipher", "file_id": "owned"}]},
        {"content": [{"type": "input_file", "file_id": "owned"}]},
        {"content": [{"type": "reasoning", "encrypted_content": "cipher"}]},
        {"content": []},
        {"author": None},
    ],
)
async def test_invalid_agent_message_rejects_before_dispatch(async_client, make_pool, route, extra):
    pool = await make_pool(5)
    response = await async_client.post(route, headers=pool.headers, json=body(input=subtask_items(**extra)))
    # File inputs follow the existing subscription file-owner path before source
    # selection; this fixture deliberately has no subscription accounts.
    has_file = any(part.get("type") == "input_file" for part in extra.get("content", []))
    assert response.status_code == (503 if has_file else 409), response.text
    if has_file:
        assert response.json()["error"]["code"] == "no_accounts"
    assert not pool.calls
    rows = await reservations(pool)
    if has_file:
        assert len(rows) == 1 and rows[0].status == "released"
    else:
        assert not rows

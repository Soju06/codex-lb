"""Client result IDs must not veto a durably owned Codex tool continuation."""

from __future__ import annotations

import copy
import json

import pytest
from aiohttp import web

from tests.integration import test_model_source_pool as pool_fixtures
from tests.integration.test_model_source_pool import MODEL, ROUTES, body
from tests.integration.test_source_reference_scope_replica import another_replica

make_pool = pool_fixtures.make_pool
pool_clock = pool_fixtures.pool_clock
pytestmark = pytest.mark.integration


@pytest.fixture
async def tool_pool(make_pool):
    calls = []

    async def upstream(request):
        sent = await request.json()
        token = request.headers["Authorization"].removeprefix("Bearer token-")
        calls.append((token, sent))
        result = {
            "id": f"resp_tool_{token}_{len(calls)}",
            "object": "response",
            "status": "completed",
            "model": sent["model"],
            "output": [
                {"type": "reasoning", "id": f"rs_{token}", "summary": [], "encrypted_content": f"cipher_{token}"},
                {
                    "type": "function_call",
                    "id": f"fc_{token}",
                    "call_id": f"call_{token}",
                    "namespace": "functions",
                    "name": "exec",
                    "arguments": "{}",
                },
            ],
            "usage": {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
        }
        if sent.get("stream"):
            return web.Response(
                text="data: " + json.dumps({"type": "response.completed", "response": result}) + "\n\n",
                content_type="text/event-stream",
            )
        return web.json_response(result)

    pool = await make_pool(5, upstream)
    return pool, calls


def tool_result(call_id="call_pool-0", **extra):
    return {
        "type": "function_call_output",
        "id": "client_result_local",
        "call_id": call_id,
        "output": [{"type": "input_text", "text": "done"}, {"type": "input_text", "text": "exit code 0"}],
        **extra,
    }


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("retain_call", [False, True], ids=["output-only", "namespaced-history"])
async def test_client_result_id_resumes_same_owner_across_pool_expansion_and_replica(
    async_client, tool_pool, monkeypatch, route, stream, retain_call
):
    pool, calls = tool_pool
    assert (
        await async_client.patch(f"/api/api-keys/{pool.key_id}", json={"assignedSourceIds": pool.ids[:1]})
    ).status_code == 200
    first = await async_client.post(route, headers=pool.headers, json=body())
    assert first.status_code == 200, first.text
    assert (
        await async_client.patch(f"/api/api-keys/{pool.key_id}", json={"assignedSourceIds": pool.ids})
    ).status_code == 200
    items = copy.deepcopy(first.json()["output"]) if retain_call else []
    items.append(tool_result())
    async with another_replica(monkeypatch) as replica:
        resumed = await replica.post(route, headers=pool.headers, json=body(stream=stream, input=items))
        assert resumed.status_code == 200, resumed.text
        assert "response.completed" in resumed.text if stream else resumed.json()["status"] == "completed"
        assert calls[-1][0] == "pool-0"
        assert calls[-1][1]["input"][-1] == items[-1]
        # The client result ID never becomes an upstream item reference.
        denied = await replica.post(
            route, headers=pool.headers, json=body(input=[{"type": "item_reference", "id": "client_result_local"}])
        )
        assert denied.status_code == 409
    assert len(calls) == 2


@pytest.mark.parametrize("route", ["/v1/responses/", "/backend-api/codex/responses/"])
@pytest.mark.parametrize(
    "guard",
    [
        "unknown-call",
        "mixed-owner",
        "disabled",
        "replaced",
        "disallowed",
        "different-key",
        "encrypted",
        "unknown-field",
    ],
)
async def test_client_result_bookkeeping_does_not_bypass_ownership(async_client, tool_pool, route, guard):
    pool, calls = tool_pool
    first = await async_client.post(route, headers=pool.headers, json=body())
    assert first.status_code == 200
    headers = pool.headers
    result = tool_result()
    payload = body(previous_response_id=first.json()["id"], input=[result])
    if guard == "unknown-call":
        result["call_id"] = "call_unknown"
    elif guard == "mixed-owner":
        second = await async_client.post(route, headers=headers, json=body())
        assert second.status_code == 200 and calls[-1][0] != "pool-0"
        result["call_id"] = second.json()["output"][1]["call_id"]
    elif guard == "disabled":
        assert (
            await async_client.patch(f"/api/model-sources/{pool.ids[0]}", json={"isEnabled": False})
        ).status_code == 200
    elif guard == "replaced":
        assert (
            await async_client.patch(f"/api/model-sources/{pool.ids[0]}", json={"apiKey": "different-token"})
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
    elif guard == "encrypted":
        result["encrypted_content"] = "cipher_unknown"
    else:
        result["opaque_owner"] = "unknown"
    before = len(calls)
    denied = await async_client.post(route, headers=headers, json=payload)
    assert denied.status_code == 409, denied.text
    assert len(calls) == before

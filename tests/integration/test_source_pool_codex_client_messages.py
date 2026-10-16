"""Client-authored message IDs remain usable when an operator expands a pool."""

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


def client_message(text: str = "Continue", *, message_id: str = "msg_client_local") -> dict:
    return {
        "type": "message",
        "id": message_id,
        "role": "user",
        "content": [{"type": "input_text", "text": text}],
    }


SEARCH = {"type": "web_search", "external_web_access": False, "search_content_types": ["text", "image"]}


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("control", ["message-id", "search", "both"])
async def test_client_controls_survive_expansion_from_one_source_to_five(async_client, make_pool, route, control):
    pool = await make_pool(5)
    for index, source_id in enumerate(pool.ids):
        update = await async_client.patch(
            f"/api/model-sources/{source_id}",
            json={
                "isEnabled": index == 0,
                "models": [
                    {
                        "model": MODEL,
                        "supportsStreaming": True,
                        "supportsTools": True,
                        "rawMetadataJson": json.dumps(
                            {"upstream_model": f"upstream-{index}", "supports_search_tool": True}
                        ),
                    }
                ],
            },
        )
        assert update.status_code == 200, update.text
    payload = body(stream=True)
    if control != "search":
        payload["input"] = [client_message()]
    else:
        payload["input"] = [{key: value for key, value in client_message().items() if key != "id"}]
    if control != "message-id":
        payload["tools"] = [SEARCH]
    first = await async_client.post(route, headers=pool.headers, json=payload)
    assert first.status_code == 200, first.text
    for source_id in pool.ids[1:]:
        assert (
            await async_client.patch(f"/api/model-sources/{source_id}", json={"isEnabled": True})
        ).status_code == 200
    for turn in range(5):
        request_body = copy.deepcopy(payload)
        if control != "search":
            request_body["input"].append(client_message(message_id=f"msg_fresh_{turn}"))
        response = await async_client.post(route, headers=pool.headers, json=request_body)
        assert response.status_code == 200, response.text
        sent = pool.calls[-1][1]
        assert sent["input"] == request_body["input"]
        if control != "message-id":
            assert sent["tools"] == [SEARCH]
    assert {token for token, _ in pool.calls} == {f"pool-{index}" for index in range(5)}


@pytest.mark.parametrize("route", ["/v1/responses", "/backend-api/codex/responses"])
@pytest.mark.parametrize("state", ["response", "encrypted"])
async def test_fresh_client_message_keeps_known_owner_after_expansion_on_another_replica(
    async_client, make_pool, monkeypatch, route, state
):
    calls = []

    async def upstream(request):
        calls.append((request.headers["Authorization"], await request.json()))
        return web.json_response(
            {
                "id": f"resp_known_{len(calls)}",
                "object": "response",
                "status": "completed",
                "model": MODEL,
                "output": [{"type": "reasoning", "id": "rs_known", "encrypted_content": "cipher-known", "summary": []}],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            }
        )

    pool = await make_pool(5, upstream)
    assert (
        await async_client.patch(f"/api/api-keys/{pool.key_id}", json={"assignedSourceIds": pool.ids[:1]})
    ).status_code == 200
    first = await async_client.post(route, headers=pool.headers, json=body(input=[client_message()]))
    assert first.status_code == 200, first.text
    assert (
        await async_client.patch(f"/api/api-keys/{pool.key_id}", json={"assignedSourceIds": pool.ids})
    ).status_code == 200
    payload = body(input=[client_message(message_id="msg_next_turn")])
    if state == "response":
        payload["previous_response_id"] = first.json()["id"]
    else:
        payload["input"].insert(0, first.json()["output"][0])
    async with another_replica(monkeypatch) as replica:
        result = await replica.post(route, headers=pool.headers, json=payload)
        assert result.status_code == 200, result.text
        assert calls[-1][0] == "Bearer token-pool-0"
        assert calls[-1][1]["input"] == payload["input"]
        assert (await replica.patch(f"/api/model-sources/{pool.ids[0]}", json={"isEnabled": False})).status_code == 200
        denied = await replica.post(route, headers=pool.headers, json=payload)
        assert denied.status_code == 409, denied.text
    assert len(calls) == 2


@pytest.mark.parametrize("route", ["/v1/responses/", "/backend-api/codex/responses/"])
@pytest.mark.parametrize(
    "state",
    [
        {
            "type": "message",
            "role": "assistant",
            "id": "msg_unknown",
            "content": [{"type": "output_text", "text": "old"}],
        },
        {"type": "item_reference", "id": "msg_unknown"},
        {"type": "reasoning", "encrypted_content": "cipher-unknown", "summary": []},
        {"type": "message", "role": "user", "id": "msg_unknown", "content": "hello", "opaque_owner": "unknown"},
        {
            "type": "additional_tools",
            "role": "developer",
            "id": "bundle_unknown",
            "tools": [{"type": "function", "name": "ping", "parameters": {"type": "object"}}],
        },
    ],
)
async def test_client_ids_do_not_erase_other_unknown_state(async_client, make_pool, route, state):
    pool = await make_pool(2)
    response = await async_client.post(
        route, headers=pool.headers, json=body(input=[state, client_message(message_id="msg_new")])
    )
    assert response.status_code == 409, response.text
    assert not pool.calls

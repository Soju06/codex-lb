"""Responses source-reference regressions through public routes and local upstreams."""

from __future__ import annotations

import json

import pytest
from aiohttp import web

from tests.integration import test_model_source_pool as pool_fixtures
from tests.integration.test_model_source_pool import MODEL, body

make_pool = pool_fixtures.make_pool
pool_clock = pool_fixtures.pool_clock
pytestmark = pytest.mark.integration

ROUTES = ["/v1/responses", "/backend-api/codex/responses"]


async def _set_model(async_client, source_id: str, model: str, upstream_model: str, **metadata) -> None:
    result = await async_client.patch(
        f"/api/model-sources/{source_id}",
        json={
            "models": [
                {
                    "model": model,
                    "supportsStreaming": True,
                    "supportsTools": True,
                    "rawMetadataJson": json.dumps({"upstream_model": upstream_model, **metadata}),
                }
            ]
        },
    )
    assert result.status_code == 200, result.text


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("owner_change", ["deleted", "disabled", "no-streaming"])
async def test_original_scope_owner_cannot_be_sent_to_normalized_fallback(async_client, make_pool, route, owner_change):
    pool = await make_pool(2)
    await _set_model(async_client, pool.ids[0], "gpt-5-high", "upstream-0")
    await _set_model(async_client, pool.ids[1], "gpt-5", "upstream-1")
    allowed = await async_client.patch(f"/api/api-keys/{pool.key_id}", json={"allowedModels": ["gpt-5-high", "gpt-5"]})
    assert allowed.status_code == 200, allowed.text
    first = await async_client.post(route, headers=pool.headers, json=body(model="gpt-5-high", stream=True))
    assert first.status_code == 200, first.text
    assert pool.calls[0][0] == "pool-0"
    if owner_change == "deleted":
        assert (await async_client.delete(f"/api/model-sources/{pool.ids[0]}")).status_code == 204
    elif owner_change == "disabled":
        assert (
            await async_client.patch(f"/api/model-sources/{pool.ids[0]}", json={"isEnabled": False})
        ).status_code == 200
    else:
        assert (
            await async_client.patch(
                f"/api/model-sources/{pool.ids[0]}",
                json={"models": [{"model": "gpt-5-high", "supportsStreaming": False, "supportsTools": True}]},
            )
        ).status_code == 200
    resumed = await async_client.post(
        route, headers=pool.headers, json=body(model="gpt-5-high", stream=True, previous_response_id="resp_pool_1")
    )
    assert resumed.status_code == 409, resumed.text
    assert len(pool.calls) == 1


@pytest.mark.parametrize("route", ROUTES)
async def test_fallback_owned_continuation_remains_valid(async_client, make_pool, route):
    pool = await make_pool(1)
    await _set_model(async_client, pool.ids[0], "gpt-5", "upstream-0")
    assert (
        await async_client.patch(f"/api/api-keys/{pool.key_id}", json={"allowedModels": ["gpt-5-high", "gpt-5"]})
    ).status_code == 200
    first = await async_client.post(route, headers=pool.headers, json=body(model="gpt-5-high"))
    assert first.status_code == 200, first.text
    resumed = await async_client.post(
        route, headers=pool.headers, json=body(model="gpt-5-high", previous_response_id=first.json()["id"])
    )
    assert resumed.status_code == 200, resumed.text
    assert [token for token, _ in pool.calls] == ["pool-0", "pool-0"]


async def test_original_scope_owner_is_isolated_by_client_key_and_model(async_client, make_pool):
    pool = await make_pool(2)
    await _set_model(async_client, pool.ids[0], "gpt-5-high", "upstream-0")
    await _set_model(async_client, pool.ids[1], "gpt-5", "upstream-1")
    assert (
        await async_client.patch(f"/api/api-keys/{pool.key_id}", json={"allowedModels": ["gpt-5-high", "gpt-5"]})
    ).status_code == 200
    first = await async_client.post("/v1/responses", headers=pool.headers, json=body(model="gpt-5-high"))
    assert first.status_code == 200, first.text
    assert (await async_client.delete(f"/api/model-sources/{pool.ids[0]}")).status_code == 204
    other_key = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "separate-client-key",
            "assignedSourceIds": [pool.ids[1]],
            "allowedModels": ["gpt-5-high", "gpt-5"],
        },
    )
    assert other_key.status_code == 200, other_key.text
    anchor = first.json()["id"]
    other_headers = {"Authorization": "Bearer " + other_key.json()["key"]}
    other_scope = await async_client.post(
        "/v1/responses", headers=other_headers, json=body(model="gpt-5-high", previous_response_id=anchor)
    )
    direct_model = await async_client.post(
        "/v1/responses", headers=pool.headers, json=body(model="gpt-5", previous_response_id=anchor)
    )
    assert other_scope.status_code == direct_model.status_code == 200
    assert [token for token, _ in pool.calls] == ["pool-0", "pool-1", "pool-1"]


@pytest.mark.parametrize("approval_owner", ["other", "same", "unknown"])
async def test_mcp_approval_reference_resolves_item_owner(async_client, make_pool, approval_owner):
    calls = []

    async def upstream(request):
        sent = await request.json()
        calls.append((request.headers["Authorization"], sent))
        number = len(calls)
        return web.json_response(
            {
                "id": f"resp_mcp_{number}",
                "object": "response",
                "status": "completed",
                "model": sent["model"],
                "output": [
                    {
                        "type": "mcp_approval_request",
                        "id": f"mcpr_{number}",
                        "server_label": "server",
                        "name": "tool",
                        "arguments": "{}",
                    }
                ],
                "usage": {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
            }
        )

    pool = await make_pool(2, upstream)
    first = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert first.status_code == 200, first.text
    second = None
    if approval_owner == "other":
        second = await async_client.post("/v1/responses", headers=pool.headers, json=body())
        assert second.status_code == 200, second.text
    approval_id = (
        second.json()["output"][0]["id"]
        if second is not None
        else first.json()["output"][0]["id"]
        if approval_owner == "same"
        else "mcpr_unknown"
    )
    prior_calls = len(calls)
    resumed = await async_client.post(
        "/v1/responses",
        headers=pool.headers,
        json=body(
            previous_response_id=first.json()["id"],
            input=[
                {
                    "type": "mcp_approval_response",
                    "approval_request_id": approval_id,
                    "approve": True,
                }
            ],
        ),
    )
    assert resumed.status_code == (200 if approval_owner == "same" else 409), resumed.text
    assert len(calls) == prior_calls + (1 if approval_owner == "same" else 0)
    if approval_owner == "same":
        assert calls[-1][0] == calls[0][0]


async def test_unknown_object_conversation_override_rejected_with_one_source(async_client, make_pool):
    pool = await make_pool(1)
    await _set_model(
        async_client,
        pool.ids[0],
        MODEL,
        "upstream-0",
        source_request_overrides={"conversation": {"id": "conv_unknown"}},
    )
    result = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert result.status_code == 409, result.text
    assert pool.calls == []


@pytest.mark.parametrize("override_owner", ["same", "other"])
async def test_object_conversation_override_respects_recorded_owner(async_client, make_pool, override_owner):
    calls = []

    async def upstream(request):
        sent = await request.json()
        token = request.headers["Authorization"].removeprefix("Bearer token-")
        calls.append((token, sent))
        return web.json_response(
            {
                "id": f"resp_conv_{len(calls)}",
                "object": "response",
                "status": "completed",
                "model": sent["model"],
                "conversation": {"id": f"conv_{token}"},
                "output": [],
                "usage": {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
            }
        )

    pool = await make_pool(2, upstream)
    first = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    second = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert first.status_code == second.status_code == 200
    assert first.json()["conversation"]["id"] != second.json()["conversation"]["id"]
    override_id = (
        first.json()["conversation"]["id"] if override_owner == "same" else second.json()["conversation"]["id"]
    )
    await _set_model(
        async_client,
        pool.ids[0],
        MODEL,
        "upstream-0",
        source_request_overrides={"conversation": {"id": override_id}},
    )
    prior_calls = len(calls)
    resumed = await async_client.post(
        "/v1/responses", headers=pool.headers, json=body(previous_response_id=first.json()["id"])
    )
    assert resumed.status_code == (200 if override_owner == "same" else 409), resumed.text
    assert len(calls) == prior_calls + (1 if override_owner == "same" else 0)
    if override_owner == "same":
        assert calls[-1][0] == "pool-0"
        assert calls[-1][1]["conversation"] == {"id": override_id}


async def test_client_external_conversation_keeps_single_source_compatibility(async_client, make_pool):
    pool = await make_pool(1)
    result = await async_client.post("/v1/responses", headers=pool.headers, json=body(conversation="conv_external"))
    assert result.status_code == 200, result.text
    assert pool.calls[0][1]["conversation"] == "conv_external"


@pytest.mark.parametrize("conversation", [{}, {"id": 42}, {"id": ""}, ["conv_bad"]])
async def test_malformed_conversation_override_is_rejected_before_dispatch(async_client, make_pool, conversation):
    pool = await make_pool(1)
    await _set_model(
        async_client,
        pool.ids[0],
        MODEL,
        "upstream-0",
        source_request_overrides={"conversation": conversation},
    )
    result = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert result.status_code == 409, result.text
    assert result.json()["error"]["code"] == "model_source_override_invalid"
    assert pool.calls == []


async def test_malformed_override_on_another_source_does_not_block_valid_source(async_client, make_pool):
    pool = await make_pool(2)
    await _set_model(
        async_client,
        pool.ids[0],
        MODEL,
        "upstream-0",
        source_request_overrides={"conversation": {"id": 42}},
    )
    result = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert result.status_code == 200, result.text
    assert [token for token, _ in pool.calls] == ["pool-1"]

"""Explicit compact routing through the public API and a loopback source."""

from __future__ import annotations

from dataclasses import replace

import pytest
from aiohttp import web

from app.core.openai.model_registry import get_model_registry
from tests.integration.model_source_helpers import _create_model_source, _enable_api_key_auth, stub_source_upstreams

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["/v1/responses/compact", "/backend-api/codex/responses/compact"])
async def test_compact_routes_to_registered_source_without_subscription_accounts(async_client, route):
    received = []

    async def compact(request):
        received.append((request.path, request.headers.get("Authorization"), await request.json()))
        return web.json_response(
            {
                "id": "cmp_source",
                "object": "response.compaction",
                "output": [{"type": "compaction", "encrypted_content": "source-opaque-state"}],
                "usage": {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
            }
        )

    async with stub_source_upstreams() as start:
        base = await start(compact)
        await _create_model_source(
            async_client,
            name="compact-fixture",
            model="external-compact-model",
            base_url=base,
            supports_responses=True,
            supports_streaming=False,
        )
        response = await async_client.post(
            route,
            json={
                "model": "external-compact-model",
                "instructions": "Compact this conversation",
                "input": [{"role": "user", "content": "Remember the number 42"}],
            },
        )

    assert response.status_code == 200, response.text
    assert len(received) == 1
    assert received[0][0] == "/v1/responses/compact"
    assert received[0][1] == "Bearer token-compact-fixture"
    assert received[0][2]["model"] == "external-compact-model"
    assert "Remember the number 42" in str(received[0][2]["input"])
    assert response.json()["output"][0]["encrypted_content"] == "source-opaque-state"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["/v1/responses/compact", "/backend-api/codex/responses/compact"])
async def test_compact_rejects_non_compact_source_response(async_client, route):
    async def invalid_compact(_request):
        return web.json_response({"id": "resp_wrong", "object": "response", "output": []})

    async with stub_source_upstreams() as start:
        base = await start(invalid_compact)
        await _create_model_source(
            async_client,
            name="invalid-compact-fixture",
            model="external-compact-model",
            base_url=base,
            supports_responses=True,
        )
        response = await async_client.post(
            route,
            json={"model": "external-compact-model", "instructions": "Compact", "input": []},
        )

    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "invalid_upstream_response"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["/v1/responses/compact", "/backend-api/codex/responses/compact"])
async def test_compact_preserves_source_history_and_public_output_contract(async_client, route):
    received = []
    history = [
        {"type": "compaction", "id": "cmp_prior", "encrypted_content": "prior-source-state"},
        {"type": "function_call", "call_id": "call_1", "name": "lookup", "namespace": "tools", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "call_1", "output": "42"},
    ]
    definitions = [{"type": "function", "name": "lookup", "parameters": {"type": "object"}}]
    source_output = [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Retained input"}]},
        {"type": "compaction", "id": "cmp_next", "encrypted_content": "next-source-state"},
    ]

    async def compact(request):
        received.append(await request.json())
        return web.json_response({"id": "cmp_result", "object": "response.compaction", "output": source_output})

    async with stub_source_upstreams() as start:
        base = await start(compact)
        await _create_model_source(
            async_client, name="history", model="external-compact-model", base_url=base, supports_responses=True
        )
        response = await async_client.post(
            route,
            json={
                "model": "external-compact-model",
                "instructions": "Compact",
                "input": history,
                "tools": definitions,
                "client_metadata": {"private": "client-telemetry"},
            },
        )

    assert response.status_code == 200, response.text
    assert received[0]["input"] == history
    assert received[0]["tools"] == definitions
    assert "client_metadata" not in received[0]
    assert "store" not in received[0]
    assert "stream" not in received[0]
    expected_output = source_output if route.startswith("/v1/") else [source_output[1]]
    assert response.json()["output"] == expected_output


@pytest.mark.asyncio
@pytest.mark.parametrize("disabled", ["source", "model"])
@pytest.mark.parametrize("route", ["/v1/responses/compact", "/backend-api/codex/responses/compact"])
async def test_compact_refuses_disabled_source_ownership(async_client, route, disabled):
    received = []

    async def unexpected_source(request):
        received.append(request.path)
        return web.json_response({"object": "response.compaction", "output": []})

    async with stub_source_upstreams() as start:
        base = await start(unexpected_source)
        source_id = await _create_model_source(
            async_client, name="disabled", model="external-compact-model", base_url=base, supports_responses=True
        )
        change = (
            {"isEnabled": False}
            if disabled == "source"
            else {"models": [{"model": "external-compact-model", "isEnabled": False}]}
        )
        updated = await async_client.patch(f"/api/model-sources/{source_id}", json=change)
        assert updated.status_code == 200, updated.text
        response = await async_client.post(
            route, json={"model": "external-compact-model", "instructions": "Compact", "input": []}
        )

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "model_source_disabled"
    assert received == []


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["/v1/responses/compact", "/backend-api/codex/responses/compact"])
async def test_compact_uses_enforced_model_and_assigned_source(async_client, route):
    received = []

    async def compact(request):
        received.append((request.headers["Authorization"], await request.json()))
        return web.json_response(
            {"object": "response.compaction", "output": [{"type": "compaction", "encrypted_content": "state"}]}
        )

    async with stub_source_upstreams() as start:
        base = await start(compact)
        await _create_model_source(
            async_client, name="unassigned", model="external-compact-model", base_url=base, supports_responses=True
        )
        assigned_id = await _create_model_source(
            async_client, name="assigned", model="external-compact-model", base_url=base, supports_responses=True
        )
        await _enable_api_key_auth(async_client)
        created = await async_client.post(
            "/api/api-keys/",
            json={"name": "scoped", "assignedSourceIds": [assigned_id], "enforcedModel": "external-compact-model"},
        )
        assert created.status_code == 200, created.text
        response = await async_client.post(
            route,
            headers={"Authorization": f"Bearer {created.json()['key']}"},
            json={"model": "client-model", "instructions": "Compact", "input": []},
        )

    assert response.status_code == 200, response.text
    assert received[0][0] == "Bearer token-assigned"
    assert received[0][1]["model"] == "external-compact-model"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["/v1/responses/compact", "/backend-api/codex/responses/compact"])
async def test_compact_retains_enforced_tier_in_source_request_log(async_client, route):
    registry = get_model_registry()
    model = replace(registry.get_models_for_metadata()["gpt-5.6-sol"], raw={"service_tiers": [{"slug": "default"}]})
    await registry.update(
        {"pro": [model]},
        per_account_results={"fixture-account": ("pro", [model])},
        active_account_plans={"fixture-account": "pro"},
    )
    received = []

    async def compact(request):
        received.append(await request.json())
        return web.json_response(
            {
                "id": "cmp_tier",
                "object": "response.compaction",
                "output": [{"type": "compaction", "encrypted_content": "state"}],
                "usage": {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
            }
        )

    async with stub_source_upstreams() as start:
        base = await start(compact)
        source_id = await _create_model_source(
            async_client, name="tier", model="gpt-5.6-sol", base_url=base, supports_responses=True
        )
        await _enable_api_key_auth(async_client)
        created = await async_client.post(
            "/api/api-keys/",
            json={"name": "tier", "assignedSourceIds": [source_id], "enforcedServiceTier": "priority"},
        )
        assert created.status_code == 200, created.text
        response = await async_client.post(
            route,
            headers={"Authorization": f"Bearer {created.json()['key']}"},
            json={"model": "gpt-5.6-sol", "instructions": "Compact", "input": []},
        )

    assert response.status_code == 200, response.text
    logs = await async_client.get("/api/request-logs")
    assert logs.status_code == 200, logs.text
    row = next(row for row in logs.json()["requests"] if row["requestId"] == "cmp_tier")
    assert row["requestedServiceTier"] == "priority"
    assert row["serviceTier"] is None

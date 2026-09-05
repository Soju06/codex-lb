from __future__ import annotations

# ruff: noqa: F811 -- imported pytest fixtures are injected by parameter name
import pytest
from aiohttp import web

from tests.integration.test_model_source_routing import (
    _create_model_source,
    _enable_api_key_auth,
    source_upstream,  # noqa: F401 -- imported pytest fixture
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("endpoint", ["/v1/responses", "/backend-api/codex/responses", "/v1/chat/completions"])
async def test_source_named_astra_keeps_its_own_model_contract(async_client, source_upstream, endpoint):
    captured = []

    async def capture(request: web.Request) -> web.Response:
        payload = await request.json()
        captured.append(payload)
        if payload.get("stream"):
            return web.Response(
                body=(
                    b'data: {"type":"response.completed","response":'
                    b'{"id":"resp_source","status":"completed","output":[]}}\n\n'
                ),
                content_type="text/event-stream",
            )
        if endpoint == "/v1/chat/completions":
            return web.json_response(
                {
                    "id": "chat_source",
                    "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
                    ],
                }
            )
        return web.json_response({"id": "resp_source", "status": "completed", "output": []})

    source_id = await _create_model_source(
        async_client,
        name="astra-source-contract",
        model="gpt-6-astra",
        base_url=await source_upstream(capture),
        supports_responses=True,
        raw_metadata_json='{"supports_reasoning": true, "supported_reasoning_levels": ["none", "minimal", "low"]}',
    )
    await _enable_api_key_auth(async_client)
    created = await async_client.post(
        "/api/api-keys/", json={"name": "source-contract", "assignedSourceIds": [source_id]}
    )
    assert created.status_code == 200
    payload = {"model": "gpt-6-astra", "instructions": "", "reasoning": {"effort": "none"}, "top_logprobs": 2}
    if endpoint == "/v1/chat/completions":
        payload["messages"] = [{"role": "user", "content": "Hello"}]
    else:
        payload["input"] = [
            {"type": "configuration_update", "reasoning": {"effort": "none"}, "vendor_setting": True},
            {"type": "configuration_update", "reasoning": {"effort": "minimal"}},
            {"role": "user", "content": "Hello"},
        ]
    response = await async_client.post(
        endpoint, json=payload, headers={"Authorization": f"Bearer {created.json()['key']}"}
    )
    assert response.status_code == 200, response.text
    assert len(captured) == 1
    assert captured[0]["reasoning"]["effort"] == "none"
    assert captured[0]["top_logprobs"] == 2
    if "input" in payload:
        assert captured[0]["input"] == payload["input"]


@pytest.mark.parametrize("policy", [{"allowedReasoningEfforts": ["low"]}, {"enforcedReasoningEffort": "low"}])
async def test_source_configuration_update_cannot_evade_key_policy(async_client, source_upstream, policy):
    captured = []

    async def capture(request: web.Request) -> web.Response:
        captured.append(await request.json())
        return web.json_response({"id": "resp_source", "status": "completed", "output": []})

    source_id = await _create_model_source(
        async_client,
        name="astra-source-policy",
        model="gpt-6-astra",
        base_url=await source_upstream(capture),
        supports_responses=True,
    )
    await _enable_api_key_auth(async_client)
    created = await async_client.post(
        "/api/api-keys/", json={"name": "source-policy", "assignedSourceIds": [source_id], **policy}
    )
    assert created.status_code == 200
    response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-6-astra",
            "reasoning": {"effort": "low"},
            "input": [{"type": "configuration_update", "reasoning": {"effort": "high"}}],
        },
        headers={"Authorization": f"Bearer {created.json()['key']}"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "reasoning_effort_not_allowed"
    assert captured == []

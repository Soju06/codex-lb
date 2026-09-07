from __future__ import annotations

# ruff: noqa: F811 -- imported pytest fixtures are injected by parameter name
import asyncio
import json

import pytest
from aiohttp import web
from sqlalchemy import select

from app.db.models import ApiKeyLimit, ApiKeyUsageReservation
from app.db.session import SessionLocal
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
@pytest.mark.parametrize("endpoint", ["/v1/responses", "/backend-api/codex/responses"])
@pytest.mark.parametrize(
    ("update", "expected_status"),
    [
        ({"type": "configuration_update", "vendor_setting": True}, 200),
        ({"type": "configuration_update", "reasoning": {"vendor_setting": True}}, 200),
        ({"type": "configuration_update", "reasoning": {"effort": "low"}}, 200),
        ({"type": "configuration_update", "reasoning": {"effort": "high"}}, 403),
        ({"type": "configuration_update", "reasoning": {"effort": None}}, 400),
        ({"type": "configuration_update", "reasoning": {"effort": 7}}, 400),
    ],
    ids=["no-reasoning", "no-effort", "allowed", "denied", "null", "non-string"],
)
async def test_source_configuration_update_enforces_only_explicit_effort(
    async_client, source_upstream, policy, endpoint, update, expected_status
):
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
        return web.json_response({"id": "resp_source", "status": "completed", "output": []})

    source_id = await _create_model_source(
        async_client,
        name="astra-source-policy",
        model="gpt-6-astra",
        base_url=await source_upstream(capture),
        supports_responses=True,
        raw_metadata_json='{"supports_reasoning": true, "supported_reasoning_levels": ["low", "high"]}',
    )
    await _enable_api_key_auth(async_client)
    created = await async_client.post(
        "/api/api-keys/", json={"name": "source-policy", "assignedSourceIds": [source_id], **policy}
    )
    assert created.status_code == 200
    response = await async_client.post(
        endpoint,
        json={
            "model": "gpt-6-astra",
            "reasoning": {"effort": "low"},
            "input": [update],
        },
        headers={"Authorization": f"Bearer {created.json()['key']}"},
    )
    assert response.status_code == expected_status, response.text
    if expected_status == 200:
        assert len(captured) == 1
        assert captured[0]["input"] == [update]
        assert captured[0]["reasoning"]["effort"] == "low"
    else:
        assert captured == []
        assert response.json()["error"]["param"] == "input.0.reasoning.effort"
        if expected_status == 403:
            assert response.json()["error"]["code"] == "reasoning_effort_not_allowed"


@pytest.mark.parametrize("endpoint", ["/v1/responses", "/backend-api/codex/responses"])
@pytest.mark.parametrize("effort", ["low", "ultra"])
@pytest.mark.parametrize("vendor_size", [32, 10_000], ids=["small-control", "budget-cap"])
async def test_source_reasoning_fields_count_toward_overlapping_request_budget(
    async_client, source_upstream, endpoint, effort, vendor_size
):
    captured = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def capture(request: web.Request) -> web.Response:
        payload = await request.json()
        captured.append(payload)
        response_id = f"resp_source_budget_{len(captured)}"
        if len(captured) == 1:
            started.set()
            await release.wait()
        response = {
            "id": response_id,
            "status": "completed",
            "output": [],
            "usage": {"input_tokens": 1, "output_tokens": 0},
        }
        if payload.get("stream"):
            event = {"type": "response.completed", "response": response}
            return web.Response(body=f"data: {json.dumps(event)}\n\n".encode(), content_type="text/event-stream")
        return web.json_response(response)

    source_id = await _create_model_source(
        async_client,
        name="astra-source-budget",
        model="gpt-6-astra",
        base_url=await source_upstream(capture),
        supports_responses=True,
        raw_metadata_json='{"supports_reasoning": true, "supported_reasoning_levels": ["low", "ultra"]}',
    )
    await _enable_api_key_auth(async_client)
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "source-budget",
            "assignedSourceIds": [source_id],
            "allowedReasoningEfforts": ["low", "ultra"],
            "limits": [{"limitType": "input_tokens", "limitWindow": "weekly", "maxValue": 1000}],
        },
    )
    assert created.status_code == 200
    key = created.json()
    headers = {"Authorization": f"Bearer {key['key']}"}
    update = {"type": "configuration_update", "reasoning": {"effort": effort, "vendor_payload": "x" * vendor_size}}
    payload = {"model": "gpt-6-astra", "reasoning": {"effort": "low"}, "input": [update]}
    first = asyncio.create_task(async_client.post(endpoint, json=payload, headers=headers))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        async with SessionLocal() as session:
            reserved = await session.scalar(
                select(ApiKeyLimit.current_value).where(ApiKeyLimit.api_key_id == key["id"])
            )
        assert reserved is not None
        second = await async_client.post(
            endpoint,
            json={"model": "gpt-6-astra", "reasoning": {"effort": "low"}, "input": "next"},
            headers=headers,
        )
        if vendor_size >= 1000:
            assert second.status_code == 429, second.text
            assert second.json()["error"]["code"] == "rate_limit_exceeded"
            assert reserved == 1000
        else:
            assert second.status_code == 200, second.text
            assert 0 < reserved < 1000
    finally:
        release.set()
        first_response = await first
    assert first_response.status_code == 200, first_response.text
    expected_requests = 1 if vendor_size >= 1000 else 2
    assert len(captured) == expected_requests
    assert captured[0]["input"] == [update]
    async with SessionLocal() as session:
        settled = await session.scalar(select(ApiKeyLimit.current_value).where(ApiKeyLimit.api_key_id == key["id"]))
        statuses = (
            await session.scalars(
                select(ApiKeyUsageReservation.status).where(ApiKeyUsageReservation.api_key_id == key["id"])
            )
        ).all()
    assert settled == expected_requests
    assert statuses == ["finalized"] * expected_requests

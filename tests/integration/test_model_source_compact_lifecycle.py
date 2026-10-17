"""Source compact accounting and disconnect behavior at the HTTP boundary."""

from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp import web

from tests.integration.model_source_helpers import (
    _AsgiStream,
    _create_model_source,
    _enable_api_key_auth,
    stub_source_upstreams,
)

pytestmark = pytest.mark.integration

_BODY = {"model": "source-compact", "instructions": "Compact", "input": []}
_RESULT = {
    "id": "cmp_source",
    "object": "response.compaction",
    "output": [{"type": "compaction", "encrypted_content": "source-state"}],
    "usage": {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
}


async def _limited_key(async_client, source_id, *, max_value=100_000):
    await _enable_api_key_auth(async_client)
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "compact-limit",
            "assignedSourceIds": [source_id],
            "limits": [{"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": max_value}],
        },
    )
    assert created.status_code == 200, created.text
    return created.json()["id"], {"Authorization": f"Bearer {created.json()['key']}"}


async def _limit_value(async_client, key_id):
    response = await async_client.get("/api/api-keys/")
    assert response.status_code == 200, response.text
    key = next(item for item in response.json() if item["id"] == key_id)
    return key["limits"][0]["currentValue"]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["/v1/responses/compact", "/backend-api/codex/responses/compact"])
async def test_compact_settles_reported_usage_and_releases_source_capacity(async_client, route):
    async def compact(_request):
        return web.json_response(_RESULT)

    async with stub_source_upstreams() as start:
        base = await start(compact)
        source_id = await _create_model_source(
            async_client, name="usage", model="source-compact", base_url=base, supports_responses=True
        )
        updated = await async_client.patch(f"/api/model-sources/{source_id}", json={"maxConcurrency": 1})
        assert updated.status_code == 200
        key_id, headers = await _limited_key(async_client, source_id)
        first = await async_client.post(route, headers=headers, json=_BODY)
        assert first.status_code == 200, first.text
        assert await _limit_value(async_client, key_id) == 15
        second = await async_client.post(route, headers=headers, json=_BODY)
        assert second.status_code == 200, second.text
        assert await _limit_value(async_client, key_id) == 30


@pytest.mark.asyncio
async def test_compact_admission_counts_preserved_source_tool_definitions(async_client):
    received = []
    entered = asyncio.Event()
    hold = asyncio.Event()

    async def compact(request):
        received.append(await request.json())
        entered.set()
        await hold.wait()
        return web.json_response(_RESULT)

    async with stub_source_upstreams() as start:
        base = await start(compact)
        source_id = await _create_model_source(
            async_client, name="budget", model="source-compact", base_url=base, supports_responses=True
        )
        key_id, headers = await _limited_key(async_client, source_id, max_value=3_000)
        first = asyncio.create_task(
            async_client.post(
                "/v1/responses/compact",
                headers=headers,
                json={
                    **_BODY,
                    "tools": [{"type": "function", "name": "lookup", "description": "x" * 10_000}],
                },
            )
        )
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            assert await _limit_value(async_client, key_id) == 3_000
            second = await async_client.post("/v1/responses/compact", headers=headers, json=_BODY)
            assert second.status_code == 429, second.text
            assert len(received) == 1
        finally:
            hold.set()
            response = await asyncio.wait_for(first, timeout=5)
        assert response.status_code == 200, response.text
        assert await _limit_value(async_client, key_id) == 15


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 429, 500])
async def test_compact_source_error_is_explicit_redacted_and_releases_usage(async_client, status):
    requests = 0

    async def compact(_request):
        nonlocal requests
        requests += 1
        if requests == 1:
            return web.json_response(
                {"error": {"code": "source_error", "message": "Rejected token-errors", "type": "server_error"}},
                status=status,
                headers={"Retry-After": "9"},
            )
        return web.json_response(_RESULT)

    async with stub_source_upstreams() as start:
        base = await start(compact)
        source_id = await _create_model_source(
            async_client, name="errors", model="source-compact", base_url=base, supports_responses=True
        )
        updated = await async_client.patch(f"/api/model-sources/{source_id}", json={"maxConcurrency": 1})
        assert updated.status_code == 200
        key_id, headers = await _limited_key(async_client, source_id)
        first = await async_client.post("/v1/responses/compact", headers=headers, json=_BODY)
        assert first.status_code == status, first.text
        assert first.json()["error"]["code"] == "source_error"
        assert "token-errors" not in first.text
        assert first.headers["Retry-After"] == "9"
        assert await _limit_value(async_client, key_id) == 0
        second = await async_client.post("/v1/responses/compact", headers=headers, json=_BODY)
        assert second.status_code == 200, second.text
        assert requests == 2
        assert await _limit_value(async_client, key_id) == 15


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True], ids=["disconnect", "cancel"])
async def test_compact_departure_releases_limited_key_and_source_capacity(async_client, app_instance, cancel):
    entered = asyncio.Event()
    hold = asyncio.Event()
    requests = 0

    async def compact(_request):
        nonlocal requests
        requests += 1
        if requests == 1:
            entered.set()
            await hold.wait()
        return web.json_response(_RESULT)

    async with stub_source_upstreams() as start:
        base = await start(compact, handler_cancellation=True, shutdown_timeout=0.1)
        source_id = await _create_model_source(
            async_client, name="departure", model="source-compact", base_url=base, supports_responses=True
        )
        updated = await async_client.patch(f"/api/model-sources/{source_id}", json={"maxConcurrency": 1})
        assert updated.status_code == 200
        key_id, headers = await _limited_key(async_client, source_id)
        stream = _AsgiStream(
            app=app_instance,
            path="/v1/responses/compact",
            headers={"authorization": headers["Authorization"]},
            body=json.dumps(_BODY).encode(),
        )
        runner = asyncio.create_task(stream.run())
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            if cancel:
                runner.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await runner
            else:
                stream.disconnect()
                await asyncio.wait_for(runner, timeout=5)
            assert await _limit_value(async_client, key_id) == 0
            second = await async_client.post("/v1/responses/compact", headers=headers, json=_BODY)
            assert second.status_code == 200, second.text
            assert await _limit_value(async_client, key_id) == 15
        finally:
            hold.set()
            if not runner.done():
                runner.cancel()
                await asyncio.gather(runner, return_exceptions=True)

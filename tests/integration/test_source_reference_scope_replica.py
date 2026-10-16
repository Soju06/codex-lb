"""Continuity validation with fresh replica-local state and shared persisted owners."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from aiohttp import web
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.modules.proxy import source_admission, source_pool
from tests.integration import test_model_source_pool as pool_fixtures
from tests.integration.test_model_source_pool import MODEL, body
from tests.integration.test_source_reference_scope_gaps import _set_model

make_pool = pool_fixtures.make_pool
pool_clock = pool_fixtures.pool_clock
pytestmark = pytest.mark.integration
ROUTES = ["/v1/responses/", "/backend-api/codex/responses/"]


@asynccontextmanager
async def another_replica(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with app.router.lifespan_context(app):
        monkeypatch.setattr(source_pool, "_SOURCE_POOL", source_pool.SourcePool())
        monkeypatch.setattr(source_admission, "_BULKHEAD", source_admission.SourceBulkhead())
        monkeypatch.setattr(source_pool.random, "choice", lambda sources: sources[-1])
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
                yield client
        finally:
            await app.state.proxy_service.drain_persistence_tasks(timeout_seconds=5)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("original_owner", [True, False], ids=["removed-original", "valid-fallback"])
async def test_normalized_continuation_on_another_replica(
    async_client, make_pool, monkeypatch, route, original_owner
) -> None:
    pool = await make_pool(2 if original_owner else 1)
    for index, source_id in enumerate(pool.ids):
        model = "gpt-5-high" if original_owner and index == 0 else "gpt-5"
        await _set_model(async_client, source_id, model, f"upstream-{index}")
    allowed = await async_client.patch(f"/api/api-keys/{pool.key_id}", json={"allowedModels": ["gpt-5-high", "gpt-5"]})
    assert allowed.status_code == 200, allowed.text
    first = await async_client.post(route, headers=pool.headers, json=body(model="gpt-5-high"))
    assert first.status_code == 200, first.text
    if original_owner:
        assert (await async_client.delete(f"/api/model-sources/{pool.ids[0]}")).status_code == 204
    async with another_replica(monkeypatch) as replica:
        response = await replica.post(
            route, headers=pool.headers, json=body(model="gpt-5-high", previous_response_id=first.json()["id"])
        )
    assert response.status_code == (409 if original_owner else 200), response.text
    assert [token for token, _ in pool.calls] == (["pool-0"] if original_owner else ["pool-0", "pool-0"])


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("reference_kind", ["approval", "conversation", "container"])
@pytest.mark.parametrize("owner", ["same", "other", "unknown"])
async def test_reference_ownership_survives_fresh_replica_state(
    async_client, make_pool, monkeypatch, route, reference_kind, owner
) -> None:
    calls = []

    async def upstream(request: web.Request) -> web.Response:
        sent = await request.json()
        token = request.headers["Authorization"].removeprefix("Bearer token-")
        calls.append((token, sent))
        response = {
            "id": f"resp_replica_{len(calls)}",
            "object": "response",
            "status": "completed",
            "model": sent["model"],
            "conversation": {"id": f"conv_{token}"},
            "output": [
                {
                    "type": "mcp_approval_request",
                    "id": f"approval_{token}",
                    "server_label": "test-server",
                    "name": "test-tool",
                    "arguments": "{}",
                },
                {
                    "type": "code_interpreter_call",
                    "id": f"ci_{token}",
                    "container_id": f"cntr_{token}",
                    "code": "print(1)",
                    "status": "completed",
                    "outputs": [],
                },
            ],
            "usage": {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
        }
        if sent.get("stream"):
            return web.Response(
                text="data: " + json.dumps({"type": "response.completed", "response": response}) + "\n\n",
                content_type="text/event-stream",
            )
        return web.json_response(response)

    pool = await make_pool(2, upstream)
    if reference_kind == "container":
        for index, source_id in enumerate(pool.ids):
            await _set_model(
                async_client,
                source_id,
                MODEL,
                f"upstream-{index}",
                experimental_supported_tools=["code_interpreter"],
            )
    for _ in range(2):
        response = await async_client.post(route, headers=pool.headers, json=body(stream=True))
        assert response.status_code == 200, response.text
    assert [token for token, _ in calls] == ["pool-0", "pool-1"]
    target = {"same": "pool-0", "other": "pool-1", "unknown": "missing"}[owner]
    payload = body(previous_response_id="resp_replica_1", stream=True)
    if reference_kind == "approval":
        payload["input"] = [
            {"type": "mcp_approval_response", "approval_request_id": f"approval_{target}", "approve": True}
        ]
    elif reference_kind == "conversation":
        await _set_model(
            async_client,
            pool.ids[0],
            MODEL,
            "upstream-0",
            source_request_overrides={"conversation": {"id": f"conv_{target}"}},
        )
    else:
        payload["tools"] = [{"type": "code_interpreter", "container": f"cntr_{target}"}]
    async with another_replica(monkeypatch) as replica:
        response = await replica.post(route, headers=pool.headers, json=payload)
    assert response.status_code == (200 if owner == "same" else 409), response.text
    assert [token for token, _ in calls] == (
        ["pool-0", "pool-1", "pool-0"] if owner == "same" else ["pool-0", "pool-1"]
    )
    if owner == "same" and reference_kind == "approval":
        assert calls[-1][1]["input"] == payload["input"]
    if owner == "same" and reference_kind == "conversation":
        assert calls[-1][1]["conversation"] == {"id": "conv_pool-0"}
    if owner == "same" and reference_kind == "container":
        assert calls[-1][1]["tools"] == payload["tools"]

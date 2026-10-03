from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.modules.proxy import source_admission, source_pool
from tests.integration import test_model_source_pool as pool_fixtures
from tests.integration.test_model_source_pool import MODEL, ROUTES, body

make_pool = pool_fixtures.make_pool
pool_clock = pool_fixtures.pool_clock
pytestmark = pytest.mark.integration


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("stream", [False, True])
async def test_namespace_controls_and_complete_tool_history_reach_source_on_all_routes(
    async_client, make_pool, route, stream
) -> None:
    pool = await make_pool(2)
    for index, source_id in enumerate(pool.ids):
        response = await async_client.patch(
            f"/api/model-sources/{source_id}",
            json={
                "models": [
                    {
                        "model": MODEL,
                        "supportsStreaming": True,
                        "supportsTools": True,
                        "rawMetadataJson": json.dumps(
                            {
                                "upstream_model": f"upstream-{index}",
                                "multi_agent_version": "v2",
                                "experimental_supported_tools": ["namespace"],
                            }
                        ),
                    }
                ]
            },
        )
        assert response.status_code == 200, response.text
    tools = [
        {
            "type": "namespace",
            "name": "collaboration",
            "tools": [{"type": "function", "name": "spawn_agent", "parameters": {"type": "object"}, "strict": False}],
        },
        {"type": "function", "name": "lookup", "parameters": {"type": "object"}},
    ]
    payload = body(
        stream=stream,
        tools=tools,
        background=False,
        max_tool_calls=4,
        stream_options={"include_obfuscation": False},
    )
    for source_ids in [pool.ids[:1], pool.ids]:
        update = await async_client.patch(f"/api/api-keys/{pool.key_id}", json={"assignedSourceIds": source_ids})
        assert update.status_code == 200, update.text
        response = await async_client.post(route, headers=pool.headers, json=payload)
        assert response.status_code == 200, response.text
        assert "resp_pool_" in response.text
        sent = pool.calls[-1][1]
        assert sent["tools"] == tools
        assert sent["background"] is False
        assert sent["max_tool_calls"] == 4
        assert sent["stream_options"] == {"include_obfuscation": False}

    payload["input"] = [
        {"type": "function_call", "call_id": "self-contained", "name": "lookup", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "self-contained", "output": "complete result"},
        {"role": "user", "content": "Continue"},
    ]
    response = await async_client.post(route, headers=pool.headers, json=payload)
    assert response.status_code == 200, response.text
    assert len(pool.calls) == 3


@pytest.mark.parametrize("replacement", [False, True], ids=["same-token", "changed-token"])
async def test_token_update_continuity_on_another_application_instance(
    async_client, make_pool, monkeypatch, replacement
) -> None:
    pool = await make_pool(2)
    first = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert first.status_code == 200, first.text
    token = "token-replacement" if replacement else "token-pool-0"
    update = await async_client.patch(f"/api/model-sources/{pool.ids[0]}", json={"apiKey": token})
    assert update.status_code == 200, update.text
    second_app = create_app()
    async with second_app.router.lifespan_context(second_app):
        monkeypatch.setattr(source_pool, "_SOURCE_POOL", source_pool.SourcePool())
        monkeypatch.setattr(source_admission, "_BULKHEAD", source_admission.SourceBulkhead())
        monkeypatch.setattr(source_pool.random, "choice", lambda values: values[-1])
        async with AsyncClient(transport=ASGITransport(app=second_app), base_url="http://testserver") as second_client:
            response = await second_client.post(
                "/v1/responses", headers=pool.headers, json=body(previous_response_id=first.json()["id"])
            )
        await second_app.state.proxy_service.drain_persistence_tasks(timeout_seconds=5)
    assert response.status_code == (409 if replacement else 200), response.text
    assert [token for token, _ in pool.calls] == (["pool-0"] if replacement else ["pool-0", "pool-0"])

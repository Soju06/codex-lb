"""Overall-review source-pool regressions through public routes and local upstreams."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from aiohttp import web
from fastapi.responses import JSONResponse
from sqlalchemy import select

from app.core.utils.time import utcnow
from app.db.models import Account, FileAccountPin, RequestLog
from app.db.session import SessionLocal
from app.modules.proxy import api as proxy_api
from tests.integration import test_model_source_pool as pool_fixtures
from tests.integration.test_model_source_pool import MODEL, body, reservations

make_pool = pool_fixtures.make_pool
pool_clock = pool_fixtures.pool_clock
pytestmark = pytest.mark.integration
ROUTES = ["/v1/responses", "/backend-api/codex/responses"]


async def _set_model_metadata(async_client, source_id: str, upstream_model: str, **metadata) -> None:
    updated = await async_client.patch(
        f"/api/model-sources/{source_id}",
        json={
            "models": [
                {
                    "model": MODEL,
                    "supportsStreaming": True,
                    "supportsTools": True,
                    "rawMetadataJson": json.dumps({"upstream_model": upstream_model, **metadata}),
                }
            ]
        },
    )
    assert updated.status_code == 200, updated.text


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("source_count", [1, 2])
async def test_file_override_cannot_bypass_subscription_pin(async_client, make_pool, route, source_count):
    pool = await make_pool(source_count)
    first = await async_client.post(route, headers=pool.headers, json=body())
    assert first.status_code == 200, first.text
    async with SessionLocal() as session:
        session.add(
            Account(
                id="subscription-file-owner",
                email="file-owner@example.test",
                plan_type="plus",
                access_token_encrypted=b"unused",
                refresh_token_encrypted=b"unused",
                id_token_encrypted=b"unused",
                last_refresh=utcnow(),
            )
        )
        await session.flush()
        session.add(
            FileAccountPin(
                file_id="file-subscription-owned",
                account_id="subscription-file-owner",
                expires_at=utcnow() + timedelta(days=1),
            )
        )
        await session.commit()
    await _set_model_metadata(
        async_client,
        pool.ids[0],
        "upstream-0",
        source_request_overrides={
            "input": [{"role": "user", "content": [{"type": "input_file", "file_id": "file-subscription-owned"}]}]
        },
    )
    result = await async_client.post(route, headers=pool.headers, json=body(previous_response_id=first.json()["id"]))
    assert result.status_code == 409, result.text
    assert len(pool.calls) == 1
    assert len(await reservations(pool)) == 1


@pytest.mark.parametrize("route", ROUTES)
async def test_original_client_file_keeps_subscription_route(async_client, make_pool, monkeypatch, route):
    pool = await make_pool(2)
    account_calls = []

    async def account_path(_request, parsed, *_args, **_kwargs):
        account_calls.append(parsed.input)
        return JSONResponse({"id": "resp_account", "object": "response", "status": "completed", "output": []})

    monkeypatch.setattr(proxy_api, "_collect_responses", account_path)
    result = await async_client.post(
        route,
        headers=pool.headers,
        json=body(input=[{"role": "user", "content": [{"type": "input_file", "file_id": "file-owned"}]}]),
    )
    assert result.status_code == 200, result.text
    assert len(account_calls) == 1
    assert pool.calls == []
    assert await reservations(pool) == []


@pytest.mark.parametrize("route", ROUTES)
async def test_file_override_on_unused_source_does_not_block_valid_owner(async_client, make_pool, route):
    pool = await make_pool(2)
    first = await async_client.post(route, headers=pool.headers, json=body())
    assert first.status_code == 200, first.text
    await _set_model_metadata(
        async_client,
        pool.ids[1],
        "upstream-1",
        source_request_overrides={"input": [{"type": "input_file", "file_id": "file-other-owner"}]},
    )
    resumed = await async_client.post(route, headers=pool.headers, json=body(previous_response_id=first.json()["id"]))
    assert resumed.status_code == 200, resumed.text
    assert [token for token, _ in pool.calls] == ["pool-0", "pool-0"]


@pytest.mark.parametrize("route", ROUTES)
async def test_hosted_tool_file_ids_cannot_bypass_subscription_pin(async_client, make_pool, route):
    pool = await make_pool(2)
    for index, source_id in enumerate(pool.ids):
        await _set_model_metadata(
            async_client, source_id, f"upstream-{index}", experimental_supported_tools=["code_interpreter"]
        )
    first = await async_client.post(route, headers=pool.headers, json=body())
    assert first.status_code == 200, first.text
    async with SessionLocal() as session:
        session.add(
            Account(
                id="hosted-file-owner",
                email="hosted-file@example.test",
                plan_type="plus",
                access_token_encrypted=b"unused",
                refresh_token_encrypted=b"unused",
                id_token_encrypted=b"unused",
                last_refresh=utcnow(),
            )
        )
        await session.flush()
        session.add(
            FileAccountPin(
                file_id="file-hosted-owned",
                account_id="hosted-file-owner",
                expires_at=utcnow() + timedelta(days=1),
            )
        )
        await session.commit()
    tools = [{"type": "code_interpreter", "container": {"type": "auto", "file_ids": ["file-hosted-owned"]}}]
    result = await async_client.post(
        route, headers=pool.headers, json=body(previous_response_id=first.json()["id"], tools=tools)
    )
    assert result.status_code == 409, result.text
    assert len(pool.calls) == 1
    assert len(await reservations(pool)) == 1


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("malformed_part", ["item", "content", "output"])
@pytest.mark.parametrize("all_malformed", [False, True])
async def test_malformed_override_type_does_not_break_valid_candidate(
    async_client, make_pool, route, malformed_part, all_malformed
):
    pool = await make_pool(2)
    malformed_input = {
        "item": [{"type": ["message"], "content": "bad"}],
        "content": [{"type": "message", "role": "user", "content": [{"type": ["input_text"], "text": "bad"}]}],
        "output": [{"type": "function_call_output", "call_id": "call_ignored", "output": [{"type": ["input_text"]}]}],
    }[malformed_part]
    malformed_sources = pool.ids if all_malformed else [pool.ids[1]]
    for index, source_id in enumerate(pool.ids):
        if source_id in malformed_sources:
            await _set_model_metadata(
                async_client,
                source_id,
                f"upstream-{index}",
                source_request_overrides={"input": malformed_input},
            )
    result = await async_client.post(route, headers=pool.headers, json=body())
    assert result.status_code == (409 if all_malformed else 200), result.text
    assert [token for token, _ in pool.calls] == ([] if all_malformed else ["pool-0"])


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("reference_surface", ["tool", "input"])
@pytest.mark.parametrize("container_owner", ["same", "other", "unknown"])
async def test_code_interpreter_container_owner_is_resolved(
    async_client, make_pool, route, reference_surface, container_owner
):
    calls = []

    async def upstream(request):
        sent = await request.json()
        calls.append((request.headers["Authorization"], sent))
        number = len(calls)
        return web.json_response(
            {
                "id": f"resp_cont_{number}",
                "object": "response",
                "status": "completed",
                "model": sent["model"],
                "output": [
                    {
                        "type": "code_interpreter_call",
                        "id": f"ci_{number}",
                        "code": "print(1)",
                        "container_id": f"cntr_{number}",
                        "status": "completed",
                        "outputs": [],
                    }
                ],
                "usage": {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
            }
        )

    pool = await make_pool(2, upstream)
    first = None
    for index, source_id in enumerate(pool.ids):
        await _set_model_metadata(
            async_client, source_id, f"upstream-{index}", experimental_supported_tools=["code_interpreter"]
        )
        assignment = await async_client.patch(f"/api/api-keys/{pool.key_id}", json={"assignedSourceIds": [source_id]})
        assert assignment.status_code == 200, assignment.text
        created = await async_client.post(
            route, headers=pool.headers, json=body(tools=[{"type": "code_interpreter", "container": {"type": "auto"}}])
        )
        assert created.status_code == 200, created.text
        if first is None:
            first = created.json()
    assert first is not None
    assert (
        await async_client.patch(f"/api/api-keys/{pool.key_id}", json={"assignedSourceIds": pool.ids})
    ).status_code == 200
    container = {"same": "cntr_1", "other": "cntr_2", "unknown": "cntr_missing"}[container_owner]
    if reference_surface == "tool":
        extra = {"tools": [{"type": "code_interpreter", "container": container}]}
    else:
        retained = {**first["output"][0], "container_id": container}
        extra = {"input": [retained]}
    payload = body(previous_response_id=first["id"])
    payload.update(extra)
    result = await async_client.post(route, headers=pool.headers, json=payload)
    assert result.status_code == (200 if container_owner == "same" else 409), result.text
    assert len(calls) == (3 if container_owner == "same" else 2)
    if container_owner == "same":
        assert calls[-1][0] == calls[0][0]


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("vector_owner", ["same", "other", "unknown"])
async def test_file_search_vector_store_owner_is_resolved(async_client, make_pool, route, vector_owner):
    calls = []

    async def upstream(request):
        sent = await request.json()
        calls.append((request.headers["Authorization"], sent))
        number = len(calls)
        return web.json_response(
            {
                "id": f"resp_vector_{number}",
                "object": "response",
                "status": "completed",
                "model": sent["model"],
                "output": [],
                "usage": {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
            }
        )

    pool = await make_pool(2, upstream)
    first = None
    for index, source_id in enumerate(pool.ids):
        await _set_model_metadata(
            async_client, source_id, f"upstream-{index}", experimental_supported_tools=["file_search"]
        )
        assigned = await async_client.patch(f"/api/api-keys/{pool.key_id}", json={"assignedSourceIds": [source_id]})
        assert assigned.status_code == 200, assigned.text
        created = await async_client.post(
            route,
            headers=pool.headers,
            json=body(tools=[{"type": "file_search", "vector_store_ids": [f"vs_{index + 1}"]}]),
        )
        assert created.status_code == 200, created.text
        if first is None:
            first = created.json()
    assert first is not None
    assert (
        await async_client.patch(f"/api/api-keys/{pool.key_id}", json={"assignedSourceIds": pool.ids})
    ).status_code == 200
    vector_id = {"same": "vs_1", "other": "vs_2", "unknown": "vs_unverified"}[vector_owner]
    result = await async_client.post(
        route,
        headers=pool.headers,
        json=body(previous_response_id=first["id"], tools=[{"type": "file_search", "vector_store_ids": [vector_id]}]),
    )
    assert result.status_code == (200 if vector_owner == "same" else 409), result.text
    assert len(calls) == (3 if vector_owner == "same" else 2)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("source_count", [1, 2])
@pytest.mark.parametrize("kind", ["namespace", "web_search"])
async def test_declared_fresh_source_tools_preserve_payload(async_client, make_pool, route, source_count, kind):
    pool = await make_pool(source_count)
    if kind == "namespace":
        metadata = {"experimental_supported_tools": ["namespace"]}
        tools = [
            {
                "type": "namespace",
                "name": "custom_group",
                "tools": [{"type": "function", "name": "echo", "parameters": {"type": "object", "properties": {}}}],
            }
        ]
    else:
        metadata = {"supports_search_tool": True}
        tools = [{"type": "web_search", "external_web_access": False}]
    for index, source_id in enumerate(pool.ids):
        await _set_model_metadata(async_client, source_id, f"upstream-{index}", **metadata)
    result = await async_client.post(route, headers=pool.headers, json=body(tools=tools))
    assert result.status_code == 200, result.text
    assert len(pool.calls) == 1
    assert pool.calls[0][1]["tools"] == tools


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("source_count", [1, 2])
async def test_empty_stream_options_is_neutral_for_source_pool(async_client, make_pool, route, source_count):
    pool = await make_pool(source_count)
    result = await async_client.post(route, headers=pool.headers, json=body(stream_options={}))
    assert result.status_code == 200, result.text
    assert len(pool.calls) == 1
    assert pool.calls[0][1]["stream_options"] == {}


@pytest.mark.parametrize("stream", [False, True])
async def test_failed_publication_logs_observed_upstream_usage_and_timing(async_client, make_pool, stream):
    calls = []

    async def upstream(request):
        sent = await request.json()
        calls.append(request.headers["Authorization"])
        response = {
            "id": "resp_collision",
            "object": "response",
            "status": "completed",
            "model": sent["model"],
            "output": [],
            "usage": {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
            "metrics": {"time_to_first_token_ms": 25, "generation_time_ms": 120},
        }
        if sent.get("stream"):
            return web.Response(
                text="data: " + json.dumps({"type": "response.completed", "response": response}) + "\n\n",
                content_type="text/event-stream",
            )
        return web.json_response(response)

    pool = await make_pool(2, upstream)
    first = await async_client.post("/v1/responses", headers=pool.headers, json=body(stream=stream))
    second = await async_client.post("/v1/responses", headers=pool.headers, json=body(stream=stream))
    assert first.status_code == 200, first.text
    assert second.status_code == (200 if stream else 502), second.text
    assert "model_source_ownership_unavailable" in second.text
    assert len(calls) == 2
    async with SessionLocal() as session:
        rows = list(
            (
                await session.scalars(
                    select(RequestLog).where(RequestLog.api_key_id == pool.key_id).order_by(RequestLog.id)
                )
            ).all()
        )
    assert len(rows) == 2
    assert rows[1].status == "error"
    assert rows[1].error_code == "model_source_ownership_unavailable"
    assert rows[1].upstream_status_code == 200
    assert (rows[1].input_tokens, rows[1].output_tokens, rows[1].latency_ms) == (3, 1, 145)
    assert (await reservations(pool))[1].status == "released"

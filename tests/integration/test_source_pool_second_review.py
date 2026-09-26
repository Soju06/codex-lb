import json

import pytest

from tests.integration import test_model_source_pool as pool_fixtures
from tests.integration.test_model_source_pool import MODEL, body

make_pool = pool_fixtures.make_pool
pool_clock = pool_fixtures.pool_clock
pytestmark = pytest.mark.integration
POOL_ROUTES = [
    "/v1/responses",
    "/v1/responses/",
    "/backend-api/codex/responses",
    "/backend-api/codex/responses/",
]


@pytest.mark.parametrize(
    "extra",
    [
        {"background": False},
        {"stream_options": {"include_obfuscation": False}},
        {"max_tool_calls": 4},
    ],
)
@pytest.mark.parametrize("route", POOL_ROUTES)
@pytest.mark.parametrize("stream", [False, True])
async def test_fresh_generation_knobs_remain_accepted(async_client, make_pool, extra, route, stream):
    pool = await make_pool(1)
    single = await async_client.post(route, headers=pool.headers, json=body(stream=stream, **extra))
    assert single.status_code == 200, single.text
    # Register an equivalent source with a second credential.
    sources = (await async_client.get("/api/model-sources/")).json()["sources"]
    second = await async_client.post(
        "/api/model-sources/",
        json={
            "name": "pool-1",
            "baseUrl": sources[0]["baseUrl"],
            "apiKey": "token-pool-1",
            "supportsResponses": True,
            "models": [{"model": MODEL, "supportsStreaming": True, "supportsTools": True}],
        },
    )
    assert second.status_code == 200, second.text
    await async_client.patch(
        f"/api/api-keys/{pool.key_id}", json={"assignedSourceIds": [pool.ids[0], second.json()["id"]]}
    )
    pooled = await async_client.post(route, headers=pool.headers, json=body(stream=stream, **extra))
    assert pooled.status_code == 200, pooled.text


async def test_reentering_same_key_does_not_break_continuation(async_client, make_pool):
    pool = await make_pool(2)
    first = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    anchor = first.json()["id"]
    updated = await async_client.patch(f"/api/model-sources/{pool.ids[0]}", json={"apiKey": "token-pool-0"})
    assert updated.status_code == 200
    followup = await async_client.post("/v1/responses", headers=pool.headers, json=body(previous_response_id=anchor))
    assert followup.status_code == 200, followup.text


async def test_source_override_references_checked_before_dispatch(async_client, make_pool):
    pool = await make_pool(2)
    first = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    second = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert len(pool.calls) == 2
    # A configured source override replaces the input with state owned by B.
    updated = await async_client.patch(
        f"/api/model-sources/{pool.ids[0]}",
        json={
            "models": [
                {
                    "model": MODEL,
                    "supportsStreaming": True,
                    "supportsTools": True,
                    "rawMetadataJson": json.dumps(
                        {
                            "upstream_model": "upstream-0",
                            "source_request_overrides": {"previous_response_id": second.json()["id"]},
                        }
                    ),
                }
            ]
        },
    )
    assert updated.status_code == 200, updated.text
    followup = await async_client.post(
        "/v1/responses", headers=pool.headers, json=body(previous_response_id=first.json()["id"])
    )
    assert len(pool.calls) == 2, f"Cross-owner state was sent: {pool.calls[-1]}; status={followup.status_code}"
    assert followup.status_code == 409, followup.text


@pytest.mark.parametrize("change", ["delete_all", "disable_streaming"])
@pytest.mark.parametrize("anchor_kind", ["response", "opaque_item", "subscription"])
async def test_no_matching_candidate_still_checks_known_source_ownership(
    async_client, make_pool, monkeypatch, change, anchor_kind
):
    from aiohttp import web
    from fastapi.responses import JSONResponse

    from app.modules.proxy import api as proxy_api

    async def opaque_output(request):
        sent = await request.json()
        return web.json_response(
            {
                "id": "resp_opaque_owner",
                "object": "response",
                "status": "completed",
                "model": sent["model"],
                "output": [
                    {"type": "reasoning", "id": "reasoning_owner", "encrypted_content": "opaque", "summary": []}
                ],
                "usage": {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
            }
        )

    pool = await make_pool(2, opaque_output)
    opaque = [{"type": "reasoning", "encrypted_content": "opaque", "summary": []}]
    first = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert first.status_code == 200, first.text
    if anchor_kind == "subscription":
        from app.core.utils.time import utcnow
        from app.db.models import Account, RequestLog
        from app.db.session import SessionLocal

        async with SessionLocal() as session:
            session.add(
                Account(
                    id="subscription-owner",
                    email="owner@example.test",
                    plan_type="plus",
                    access_token_encrypted=b"unused",
                    refresh_token_encrypted=b"unused",
                    id_token_encrypted=b"unused",
                    last_refresh=utcnow(),
                )
            )
            await session.flush()
            session.add(
                RequestLog(
                    request_id=first.json()["id"],
                    account_id="subscription-owner",
                    api_key_id=pool.key_id,
                    model=MODEL,
                    status="success",
                )
            )
            await session.commit()
    for source_id in pool.ids:
        if change == "delete_all":
            assert (await async_client.delete(f"/api/model-sources/{source_id}")).status_code == 204
        else:
            assert (
                await async_client.patch(
                    f"/api/model-sources/{source_id}", json={"models": [{"model": MODEL, "supportsStreaming": False}]}
                )
            ).status_code == 200
    account_dispatches = []

    async def account_path(*args, **kwargs):
        account_dispatches.append(True)
        return JSONResponse({"id": "wrong-account-path"})

    monkeypatch.setattr(proxy_api, "_collect_responses", account_path)
    monkeypatch.setattr(proxy_api, "_stream_responses", account_path)
    response = await async_client.post(
        "/v1/responses",
        headers=pool.headers,
        json=body(
            stream=change == "disable_streaming",
            previous_response_id=first.json()["id"] if anchor_kind == "response" else None,
            input=opaque if anchor_kind == "opaque_item" else "hello",
        ),
    )
    if anchor_kind == "subscription":
        assert len(account_dispatches) == 1
        assert response.status_code == 200
    else:
        assert not account_dispatches, (
            f"Source-owned request fell into subscription handling: {response.status_code} {response.text}"
        )
        assert response.status_code in (409, 503)


@pytest.mark.parametrize("route", POOL_ROUTES)
@pytest.mark.parametrize("stream", [False, True])
async def test_declared_collaboration_tools_can_start_fresh_pool_request(async_client, make_pool, route, stream):
    pool = await make_pool(2)
    for i, source_id in enumerate(pool.ids):
        patched = await async_client.patch(
            f"/api/model-sources/{source_id}",
            json={
                "models": [
                    {
                        "model": MODEL,
                        "supportsStreaming": True,
                        "supportsTools": True,
                        "rawMetadataJson": json.dumps({"upstream_model": f"upstream-{i}", "multi_agent_version": "v2"}),
                    }
                ]
            },
        )
        assert patched.status_code == 200
    tools = [
        {
            "type": "namespace",
            "name": "collaboration",
            "description": "Agent tools",
            "tools": [
                {
                    "type": "function",
                    "name": "spawn_agent",
                    "description": "Spawn agent",
                    "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
                    "strict": True,
                }
            ],
        }
    ]
    await async_client.patch(f"/api/api-keys/{pool.key_id}", json={"assignedSourceIds": pool.ids[:1]})
    single = await async_client.post(route, headers=pool.headers, json=body(stream=stream, tools=tools))
    assert single.status_code == 200, single.text
    assert pool.calls[-1][1]["tools"] == tools
    await async_client.patch(f"/api/api-keys/{pool.key_id}", json={"assignedSourceIds": pool.ids})
    response = await async_client.post(route, headers=pool.headers, json=body(stream=stream, tools=tools))
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("history_present", [False, True])
async def test_legacy_log_owner_cannot_be_overridden_by_other_source(async_client, make_pool, history_present):
    from aiohttp import web
    from sqlalchemy import delete

    from app.db.models import ModelSourceOwnership, ModelSourceOwnershipHistory
    from app.db.session import SessionLocal

    calls = []

    async def duplicate_id(request):
        sent = await request.json()
        calls.append(request.headers["Authorization"])
        return web.json_response(
            {
                "id": "resp_duplicate_legacy",
                "object": "response",
                "status": "completed",
                "model": sent["model"],
                "output": [],
                "usage": {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
            }
        )

    pool = await make_pool(2, duplicate_id)
    assert (await async_client.post("/v1/responses", headers=pool.headers, json=body())).status_code == 200
    async with SessionLocal() as session:
        await session.execute(delete(ModelSourceOwnership))
        if not history_present:
            await session.execute(delete(ModelSourceOwnershipHistory))
        await session.commit()
    second = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    resumed = await async_client.post(
        "/v1/responses", headers=pool.headers, json=body(previous_response_id="resp_duplicate_legacy")
    )
    assert second.status_code == 502, second.text
    assert resumed.status_code == 200, resumed.text
    assert calls == ["Bearer token-pool-0", "Bearer token-pool-1", "Bearer token-pool-0"]


async def test_log_only_null_revision_collision_is_not_published(async_client, make_pool):
    from aiohttp import web

    from app.db.session import SessionLocal

    calls = []

    async def legacy_then_duplicate(request):
        sent = await request.json()
        calls.append(request.headers["Authorization"])
        return web.json_response(
            {
                "id": "resp_null_revision_legacy",
                "object": "response",
                "status": "completed",
                "model": sent["model"],
                "output": [],
                "usage": {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
            }
        )

    pool = await make_pool(2, legacy_then_duplicate)
    # An old log with a source ID but no credential revision is not safe to
    # reconcile by guessing that the present token generated the response.
    from app.db.models import RequestLog

    async with SessionLocal() as session:
        session.add(
            RequestLog(
                request_id="resp_null_revision_legacy",
                model_source_id=pool.ids[0],
                api_key_id=pool.key_id,
                model=MODEL,
                status="success",
            )
        )
        await session.commit()
    response = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "model_source_ownership_unavailable"
    assert len(calls) == 1


async def test_log_only_legacy_owner_without_revision_fails_closed(async_client, make_pool):
    from app.db.session import SessionLocal
    from app.modules.request_logs.repository import RequestLogsRepository

    pool = await make_pool(2)
    async with SessionLocal() as session:
        await RequestLogsRepository(session).add_log(
            account_id=None,
            request_id="old-log-only-response",
            model=MODEL,
            input_tokens=None,
            output_tokens=None,
            latency_ms=None,
            status="error",
            error_code=None,
            api_key_id=pool.key_id,
            model_source_id=pool.ids[0],
        )
    response = await async_client.post(
        "/v1/responses",
        headers=pool.headers,
        json=body(previous_response_id="old-log-only-response"),
    )
    assert response.status_code == 409, response.text
    assert not pool.calls


@pytest.mark.parametrize("replace_credential", [False, True])
async def test_request_log_revision_remains_a_legacy_owner_fence(async_client, make_pool, replace_credential):
    from sqlalchemy import delete

    from app.db.models import ModelSourceOwnership, ModelSourceOwnershipHistory
    from app.db.session import SessionLocal

    pool = await make_pool(2)
    first = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert first.status_code == 200
    async with SessionLocal() as session:
        await session.execute(delete(ModelSourceOwnership))
        await session.execute(delete(ModelSourceOwnershipHistory))
        await session.commit()
    if replace_credential:
        changed = await async_client.patch(f"/api/model-sources/{pool.ids[0]}", json={"apiKey": "token-replaced"})
        assert changed.status_code == 200
    resumed = await async_client.post(
        "/v1/responses", headers=pool.headers, json=body(previous_response_id=first.json()["id"])
    )
    assert resumed.status_code == (409 if replace_credential else 200), resumed.text
    assert [token for token, _ in pool.calls] == (["pool-0"] if replace_credential else ["pool-0", "pool-0"])


@pytest.mark.parametrize("pruned", [False, True])
async def test_expired_ownership_keeps_credential_fence(async_client, make_pool, pruned):
    from datetime import timedelta

    from sqlalchemy import update

    from app.core.utils.time import utcnow
    from app.db.models import ModelSourceOwnership
    from app.db.session import SessionLocal
    from app.modules.model_sources.ownership_repository import SourceOwnershipRepository

    pool = await make_pool(2)
    first = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    async with SessionLocal() as session:
        await session.execute(update(ModelSourceOwnership).values(expires_at=utcnow() - timedelta(days=1)))
        if pruned:
            await SourceOwnershipRepository(session).prune(now=utcnow(), batch_size=100)
        await session.commit()
    assert (
        await async_client.patch(f"/api/model-sources/{pool.ids[0]}", json={"apiKey": "token-new-owner"})
    ).status_code == 200
    resumed = await async_client.post(
        "/v1/responses", headers=pool.headers, json=body(previous_response_id=first.json()["id"])
    )
    assert len(pool.calls) == 1, (
        f"Old state reached replacement credential: {pool.calls[-1][0]}, response {resumed.status_code}"
    )
    assert resumed.status_code == 409


async def test_call_id_only_output_cannot_cross_source_owners(async_client, make_pool):
    from aiohttp import web

    calls = []

    async def tool_output(request):
        sent = await request.json()
        calls.append((request.headers["Authorization"], sent))
        n = len(calls)
        return web.json_response(
            {
                "id": f"resp_call_{n}",
                "object": "response",
                "status": "completed",
                "model": sent["model"],
                "output": [
                    {
                        "type": "function_call",
                        "id": f"fc_{n}",
                        "call_id": f"call_{n}",
                        "name": "lookup",
                        "arguments": "{}",
                        "status": "completed",
                    }
                ],
                "usage": {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
            }
        )

    pool = await make_pool(2, tool_output)
    first = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    second = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert first.status_code == second.status_code == 200
    resumed = await async_client.post(
        "/v1/responses",
        headers=pool.headers,
        json=body(
            previous_response_id=first.json()["id"],
            input=[
                {
                    "type": "function_call_output",
                    "call_id": second.json()["output"][0]["call_id"],
                    "output": "B tool result",
                }
            ],
        ),
    )
    assert len(calls) == 2, f"Mixed owner tool output sent: {calls[-1]}; status {resumed.status_code}"
    assert resumed.status_code == 409

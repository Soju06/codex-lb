import asyncio
import json

import pytest

from app.modules.proxy.source_dispatch import SourceDispatch
from tests.integration import test_model_source_pool as pool_fixtures
from tests.integration.model_source_helpers import _AsgiStream
from tests.integration.test_model_source_pool import body

make_pool = pool_fixtures.make_pool
pool_clock = pool_fixtures.pool_clock

pytestmark = pytest.mark.integration


async def test_next_turn_immediately_after_completed(async_client, make_pool, monkeypatch):
    pool = await make_pool(2)
    entered = asyncio.Event()
    release = asyncio.Event()
    real_write = SourceDispatch.write_row

    async def delayed_write(owner, **kwargs):
        holder = owner.usage_holder
        if holder is not None and holder.response_id == "resp_pool_1":
            entered.set()
            await release.wait()
        await real_write(owner, **kwargs)

    monkeypatch.setattr(SourceDispatch, "write_row", delayed_write)
    wire = _AsgiStream(
        async_client._transport.app, "/v1/responses", pool.headers, json.dumps(body(stream=True)).encode()
    )
    pending = asyncio.create_task(wire.run())
    try:
        await wire.wait_for_text("response.completed")
        await asyncio.wait_for(entered.wait(), 5)
        early = await async_client.post(
            "/v1/responses", headers=pool.headers, json=body(previous_response_id="resp_pool_1")
        )
    finally:
        release.set()
        await asyncio.wait_for(pending, 10)
    late = await async_client.post("/v1/responses", headers=pool.headers, json=body(previous_response_id="resp_pool_1"))
    assert late.status_code == 200
    assert early.status_code == 200, early.text


async def test_conversation_stays_on_original_credential(async_client, make_pool):
    from aiohttp import web

    pool = None

    async def scoped_conversation(request):
        assert pool is not None
        if request.headers["Authorization"] != "Bearer token-pool-0":
            request_body = await request.json()
            pool.calls.append((request.headers["Authorization"].removeprefix("Bearer token-"), request_body))
            return web.json_response(
                {
                    "error": {
                        "message": "Conversation not found for this credential",
                        "type": "invalid_request_error",
                        "code": "conversation_not_found",
                    }
                },
                status=400,
            )
        return await pool.upstream(request)

    pool = await make_pool(2, scoped_conversation)
    unknown = await async_client.post("/v1/responses", headers=pool.headers, json=body(conversation="conv_existing"))
    assert unknown.status_code == 409 and not pool.calls
    await async_client.patch(f"/api/api-keys/{pool.key_id}", json={"assignedSourceIds": [pool.ids[0]]})
    first = await async_client.post("/v1/responses", headers=pool.headers, json=body(conversation="conv_existing"))
    await async_client.patch(f"/api/api-keys/{pool.key_id}", json={"assignedSourceIds": pool.ids})
    second = await async_client.post("/v1/responses", headers=pool.headers, json=body(conversation="conv_existing"))
    assert [token for token, _ in pool.calls] == ["pool-0", "pool-0"]
    assert first.status_code == 200
    assert second.status_code == 200, second.text


@pytest.mark.parametrize("stream", [False, True])
async def test_connection_failure_after_redirect_is_not_replayed(async_client, make_pool, stream):
    from aiohttp import web

    pool = None

    async def accepted_then_redirected(request):
        assert pool is not None
        if request.headers["Authorization"] == "Bearer token-pool-0":
            request_body = await request.json()
            pool.calls.append(("pool-0", request_body))
            return web.Response(status=303, headers={"Location": "http://127.0.0.1:1/result/accepted-job"})
        return await pool.upstream(request)

    pool = await make_pool(2, accepted_then_redirected)
    response = await async_client.post("/v1/responses", headers=pool.headers, json=body(stream=stream))
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "model_source_redirect"
    assert "location" not in response.headers
    assert len(pool.calls) == 1, "A failed redirected GET cannot prove the original POST was never accepted"


@pytest.mark.parametrize("stream", [False, True])
async def test_encrypted_history_survives_backend_change_and_rejects_mixed_owners(
    async_client, make_pool, monkeypatch, stream
):
    from aiohttp import web

    from app.modules.proxy import source_admission, source_pool
    from tests.integration.test_model_source_pool import MODEL

    calls = []
    counter = 0

    async def encrypted_upstream(request):
        nonlocal counter
        token = request.headers["Authorization"].removeprefix("Bearer token-")
        sent = await request.json()
        calls.append(token)
        for item in sent.get("input", []) if isinstance(sent.get("input"), list) else []:
            if item.get("encrypted_content") and not item["encrypted_content"].startswith(token + ":"):
                return web.json_response(
                    {"error": {"code": "invalid_encrypted_content", "message": "wrong credential"}}, status=400
                )
        counter += 1
        result = {
            "id": f"resp_encrypted_{counter}",
            "object": "response",
            "status": "completed",
            "model": sent["model"],
            "output": [
                {
                    "type": "reasoning",
                    "id": f"rs_{token}_{counter}",
                    "summary": [],
                    "encrypted_content": f"{token}:ciphertext:{counter}",
                }
            ],
            "usage": {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
        }
        if sent.get("stream"):
            return web.Response(
                text="data: " + json.dumps({"type": "response.completed", "response": result}) + "\n\n",
                content_type="text/event-stream",
            )
        return web.json_response(result)

    pool = await make_pool(2, encrypted_upstream)
    first = await async_client.post("/v1/responses", headers=pool.headers, json=body(stream=stream))
    assert first.status_code == 200, first.text
    encrypted_item = {
        "type": "reasoning",
        "id": "rs_pool-0_1",
        "summary": [],
        "encrypted_content": "pool-0:ciphertext:1",
    }
    # A different worker shares storage but has fresh rotation and admission state.
    monkeypatch.setattr(source_pool, "_SOURCE_POOL", source_pool.SourcePool())
    monkeypatch.setattr(source_admission, "_BULKHEAD", source_admission.SourceBulkhead())
    monkeypatch.setattr(source_pool.random, "choice", lambda values: values[-1])
    resumed = await async_client.post("/v1/responses", headers=pool.headers, json=body(input=[encrypted_item]))
    assert resumed.status_code == 200, resumed.text
    assert calls == ["pool-0", "pool-0"]
    other = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert other.status_code == 200 and calls[-1] == "pool-1"
    other_item = other.json()["output"][0]
    mixed = await async_client.post(
        "/v1/responses", headers=pool.headers, json=body(input=[encrypted_item, other_item])
    )
    assert mixed.status_code == 409 and len(calls) == 3
    # Ownership is scoped to both client-key identity and public model.
    key = await async_client.post(
        "/api/api-keys/", json={"name": "other-scope", "assignedSourceIds": pool.ids, "allowedModels": [MODEL]}
    )
    cross_key = await async_client.post(
        "/v1/responses", headers={"Authorization": "Bearer " + key.json()["key"]}, json=body(input=[encrypted_item])
    )
    assert cross_key.status_code == 409 and len(calls) == 3
    changed = await async_client.patch(f"/api/model-sources/{pool.ids[0]}", json={"apiKey": "token-replacement"})
    assert changed.status_code == 200
    replaced = await async_client.post("/v1/responses", headers=pool.headers, json=body(input=[encrypted_item]))
    assert replaced.status_code == 409 and len(calls) == 3


@pytest.mark.parametrize("stream", [False, True])
async def test_ownership_storage_failure_withholds_success_and_releases(async_client, make_pool, monkeypatch, stream):
    from app.modules.model_sources.ownership_repository import SourceOwnershipRepository
    from app.modules.proxy.source_admission import get_source_bulkhead
    from tests.integration.test_model_source_pool import reservations

    pool = await make_pool(2)

    async def unavailable(*args, **kwargs):
        raise RuntimeError("simulated storage failure")

    monkeypatch.setattr(SourceOwnershipRepository, "claim", unavailable)
    response = await async_client.post("/v1/responses", headers=pool.headers, json=body(stream=stream))
    assert response.status_code == (200 if stream else 502)
    assert "model_source_ownership_unavailable" in response.text
    assert "response.completed" not in response.text
    assert len(pool.calls) == 1
    assert all(row.status == "released" for row in await reservations(pool))
    assert all(get_source_bulkhead().in_flight(source_id) == 0 for source_id in pool.ids)


async def test_cancel_during_ownership_write_awaits_cleanup_without_replay(async_client, make_pool, monkeypatch):
    from app.modules.proxy.source_admission import get_source_bulkhead
    from app.modules.proxy.source_ownership import SourceOwnershipRecorder
    from tests.integration.test_model_source_pool import reservations

    pool = await make_pool(2)
    entered = asyncio.Event()
    release = asyncio.Event()
    original = SourceOwnershipRecorder._commit

    async def blocked_commit(recorder, keys):
        entered.set()
        await release.wait()
        await original(recorder, keys)

    monkeypatch.setattr(SourceOwnershipRecorder, "_commit", blocked_commit)
    wire = _AsgiStream(
        async_client._transport.app, "/v1/responses", pool.headers, json.dumps(body(stream=True)).encode()
    )
    task = asyncio.create_task(wire.run())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        wire.disconnect()
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(task, 10)
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert len(pool.calls) == 1 and b"response.completed" not in wire.received()
    # Captured upstream usage is finalized under the existing cancellation policy.
    assert all(row.status == "finalized" for row in await reservations(pool))
    assert all(get_source_bulkhead().in_flight(source_id) == 0 for source_id in pool.ids)


@pytest.mark.parametrize(
    "extra",
    [
        {"input": [{"type": "reasoning", "encrypted_content": "unknown", "summary": []}]},
        {"input": [{"type": "item_reference", "id": "unknown"}]},
        {"prompt": {"id": "pmpt_unknown"}},
    ],
)
async def test_unknown_nonportable_state_never_rotates(async_client, make_pool, extra):
    pool = await make_pool(2)
    response = await async_client.post("/v1/responses", headers=pool.headers, json=body(**extra))
    assert response.status_code == 409, response.text
    assert not pool.calls


async def test_chat_redirect_policy_is_unchanged(async_client, make_pool):
    from aiohttp import web

    calls = []

    async def upstream(request):
        calls.append(request.path)
        if request.path == "/v1/chat/completions":
            await request.read()
            return web.Response(status=303, headers={"Location": "/v1/chat-result"})
        return web.json_response(
            {
                "id": "chat_result",
                "object": "chat.completion",
                "created": 1,
                "model": "upstream-0",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
            }
        )

    pool = await make_pool(2, upstream)
    response = await async_client.post(
        "/v1/chat/completions",
        headers=pool.headers,
        json={"model": "cd/pool-model", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert response.status_code == 200, response.text
    assert calls == ["/v1/chat/completions", "/v1/chat-result"]


async def test_separate_application_instances_share_source_ownership(async_client, make_pool, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app
    from app.modules.proxy import source_admission, source_pool

    pool = await make_pool(2)
    first = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert first.status_code == 200
    first_service = async_client._transport.app.state.proxy_service
    second_app = create_app()
    async with second_app.router.lifespan_context(second_app):
        monkeypatch.setattr(source_pool, "_SOURCE_POOL", source_pool.SourcePool())
        monkeypatch.setattr(source_admission, "_BULKHEAD", source_admission.SourceBulkhead())
        monkeypatch.setattr(source_pool.random, "choice", lambda values: values[-1])
        async with AsyncClient(transport=ASGITransport(app=second_app), base_url="http://testserver") as second_client:
            response = await second_client.post(
                "/v1/responses", headers=pool.headers, json=body(previous_response_id=first.json()["id"])
            )
        assert second_app.state.proxy_service is not first_service
        await second_app.state.proxy_service.drain_persistence_tasks(timeout_seconds=5)
    assert response.status_code == 200, response.text
    assert [token for token, _ in pool.calls] == ["pool-0", "pool-0"]


async def test_failed_duplicate_reference_cannot_poison_the_durable_owner(async_client, make_pool):
    from aiohttp import web

    calls = []

    async def same_response_id(request):
        token = request.headers["Authorization"]
        payload = await request.json()
        calls.append(token)
        return web.json_response(
            {
                "id": "resp_collision",
                "object": "response",
                "status": "completed",
                "model": payload["model"],
                "output": [],
                "usage": {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
            }
        )

    pool = await make_pool(2, same_response_id)
    first = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert first.status_code == 200
    collision = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert collision.status_code == 502 and "model_source_ownership_unavailable" in collision.text
    # The failed second publication is logged but never displaces the owner
    # that successfully published the reference to the client.
    resumed = await async_client.post(
        "/v1/responses", headers=pool.headers, json=body(previous_response_id="resp_collision")
    )
    assert resumed.status_code == 200
    assert calls == ["Bearer token-pool-0", "Bearer token-pool-1", "Bearer token-pool-0"]

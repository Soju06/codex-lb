"""Public-path regressions found during the source pool ownership review."""

import asyncio
import json

import pytest
from aiohttp import web

from app.core.utils.sse import parse_sse_data_json
from app.modules.model_sources.ownership_repository import SourceOwnershipRepository
from app.modules.proxy.source_admission import get_source_bulkhead
from app.modules.proxy.source_ownership import OwnershipScope, SourceOwnershipRecorder
from tests.integration import test_model_source_pool as pool_fixtures
from tests.integration.model_source_helpers import _AsgiStream
from tests.integration.test_model_source_pool import MODEL, body, reservations

make_pool = pool_fixtures.make_pool
pool_clock = pool_fixtures.pool_clock
pytestmark = pytest.mark.integration


@pytest.fixture
def output_upstream():
    calls = []

    async def handler(request):
        sent = await request.json()
        token = request.headers["Authorization"].removeprefix("Bearer token-")
        calls.append(token)
        sequence = len(calls)
        response = {
            "id": f"resp_output_{sequence}",
            "object": "response",
            "status": "completed",
            "model": sent["model"],
            "output": [
                {
                    "type": "message",
                    "id": f"msg_output_{sequence}",
                    "role": "assistant",
                    "status": "completed",
                    "content": [{"type": "output_text", "text": "hello", "annotations": []}],
                },
                {
                    "type": "function_call",
                    "id": f"fc_output_{sequence}",
                    "call_id": f"call_{sequence}",
                    "name": "lookup",
                    "arguments": "{}",
                    "status": "completed",
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

    return handler, calls


@pytest.mark.parametrize("item_index", [0, 1], ids=["message", "function_call"])
async def test_replayed_output_item_resolves_owner_and_rejects_mixed_history(
    async_client, make_pool, output_upstream, item_index
):
    handler, calls = output_upstream
    pool = await make_pool(2, handler)
    first = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    second = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert first.status_code == second.status_code == 200
    assert calls == ["pool-0", "pool-1"]
    resumed = await async_client.post(
        "/v1/responses", headers=pool.headers, json=body(input=[second.json()["output"][item_index]])
    )
    assert resumed.status_code == 200, resumed.text
    assert calls[-1] == "pool-1"
    mixed = await async_client.post(
        "/v1/responses",
        headers=pool.headers,
        json=body(previous_response_id=first.json()["id"], input=[second.json()["output"][item_index]]),
    )
    assert mixed.status_code == 409 and len(calls) == 3


async def test_normalized_delta_reference_is_owned_before_completion(
    async_client, make_pool, output_upstream, monkeypatch
):
    handler, calls = output_upstream
    pool = await make_pool(2, handler)
    entered = asyncio.Event()
    release = asyncio.Event()
    original = SourceOwnershipRecorder.record_frame

    async def delay_completion(recorder, frame):
        event = parse_sse_data_json(frame)
        if event and event.get("type") == "response.completed":
            entered.set()
            await release.wait()
        await original(recorder, frame)

    monkeypatch.setattr(SourceOwnershipRecorder, "record_frame", delay_completion)
    wire = _AsgiStream(
        async_client._transport.app, "/v1/responses", pool.headers, json.dumps(body(stream=True)).encode()
    )
    pending = asyncio.create_task(wire.run())
    try:
        await wire.wait_for_text("response.output_text.delta")
        await asyncio.wait_for(entered.wait(), 5)
        assert b"msg_output_1" in wire.received()
        resumed = await async_client.post(
            "/v1/responses",
            headers=pool.headers,
            json=body(input=[{"type": "item_reference", "id": "msg_output_1"}]),
        )
        assert resumed.status_code == 200, resumed.text
        assert calls == ["pool-0", "pool-0"]
    finally:
        release.set()
        await asyncio.wait_for(pending, 10)


async def test_failed_delta_ownership_never_exposes_the_item(async_client, make_pool, output_upstream, monkeypatch):
    handler, calls = output_upstream
    pool = await make_pool(2, handler)
    item_key = OwnershipScope(pool.key_id, MODEL).key("item", "msg_output_1")
    original = SourceOwnershipRepository.claim

    async def fail_item_claim(repository, keys, **kwargs):
        if item_key in keys:
            raise RuntimeError("item claim unavailable")
        await original(repository, keys, **kwargs)

    monkeypatch.setattr(SourceOwnershipRepository, "claim", fail_item_claim)
    response = await async_client.post("/v1/responses", headers=pool.headers, json=body(stream=True))
    assert "model_source_ownership_unavailable" in response.text
    assert "msg_output_1" not in response.text
    assert "response.completed" not in response.text
    assert len(calls) == 1
    assert all(row.status == "released" for row in await reservations(pool))


async def test_json_cancellation_during_ownership_write_settles_captured_usage(async_client, make_pool, monkeypatch):
    pool = await make_pool(2)
    entered = asyncio.Event()
    release = asyncio.Event()
    original = SourceOwnershipRecorder._commit

    async def blocked_commit(recorder, keys):
        entered.set()
        await release.wait()
        await original(recorder, keys)

    monkeypatch.setattr(SourceOwnershipRecorder, "_commit", blocked_commit)
    wire = _AsgiStream(async_client._transport.app, "/v1/responses", pool.headers, json.dumps(body()).encode())
    pending = asyncio.create_task(wire.run())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        pending.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, 10)
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
    rows = await reservations(pool)
    assert len(rows) == 1 and rows[0].status == "finalized"
    assert len(pool.calls) == 1 and not wire.received()
    assert all(get_source_bulkhead().in_flight(source_id) == 0 for source_id in pool.ids)

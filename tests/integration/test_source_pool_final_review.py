from __future__ import annotations

import json

import pytest
from fastapi.responses import JSONResponse
from sqlalchemy import delete

from app.db.models import ModelSourceOwnership, ModelSourceOwnershipHistory
from app.db.session import SessionLocal
from app.modules.proxy import api as proxy_api
from tests.integration import test_model_source_pool as pool_fixtures
from tests.integration import test_source_ownership_review_regressions as output_fixtures
from tests.integration.test_model_source_pool import MODEL, body

make_pool = pool_fixtures.make_pool
pool_clock = pool_fixtures.pool_clock
output_upstream = output_fixtures.output_upstream
pytestmark = pytest.mark.integration


@pytest.mark.parametrize("source_count", [1, 2])
async def test_log_owner_does_not_resolve_an_unknown_overridden_anchor(async_client, make_pool, source_count) -> None:
    pool = await make_pool(source_count)
    first = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert first.status_code == 200, first.text
    async with SessionLocal() as session:
        await session.execute(delete(ModelSourceOwnership))
        await session.execute(delete(ModelSourceOwnershipHistory))
        await session.commit()
    patched = await async_client.patch(
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
                            "source_request_overrides": {"previous_response_id": "unknown-override-anchor"},
                        }
                    ),
                }
            ]
        },
    )
    assert patched.status_code == 200, patched.text
    resumed = await async_client.post(
        "/v1/responses", headers=pool.headers, json=body(previous_response_id=first.json()["id"])
    )
    assert resumed.status_code == 409, resumed.text
    assert len(pool.calls) == 1


@pytest.mark.parametrize("shape", ["partial-call", "mismatched-output", "reversed-pair"])
async def test_incomplete_tool_pair_keeps_its_other_source_owner(
    async_client, make_pool, output_upstream, shape
) -> None:
    handler, calls = output_upstream
    pool = await make_pool(2, handler)
    first = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    second = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert first.status_code == second.status_code == 200
    call = second.json()["output"][1]
    if shape == "partial-call":
        call = {key: call[key] for key in ("id", "call_id", "type")}
    output = {
        "type": "custom_tool_call_output" if shape == "mismatched-output" else "function_call_output",
        "call_id": call["call_id"],
        "output": "result",
    }
    pair = [output, call] if shape == "reversed-pair" else [call, output]
    resumed = await async_client.post(
        "/v1/responses",
        headers=pool.headers,
        json=body(previous_response_id=first.json()["id"], input=pair),
    )
    assert resumed.status_code == 409, resumed.text
    assert calls == ["pool-0", "pool-1"]


@pytest.mark.parametrize("route", ["/v1/responses", "/backend-api/codex/responses"])
async def test_removed_source_retains_raw_model_scope_after_normalization(
    async_client, make_pool, monkeypatch, route
) -> None:
    pool = await make_pool(2)
    public_model = "gpt-5-high"
    for index, source_id in enumerate(pool.ids):
        patched = await async_client.patch(
            f"/api/model-sources/{source_id}",
            json={
                "models": [
                    {
                        "model": public_model,
                        "supportsStreaming": True,
                        "supportsTools": True,
                        "rawMetadataJson": json.dumps({"upstream_model": f"upstream-{index}"}),
                    }
                ]
            },
        )
        assert patched.status_code == 200, patched.text
    patched = await async_client.patch(f"/api/api-keys/{pool.key_id}", json={"allowedModels": [public_model, "gpt-5"]})
    assert patched.status_code == 200, patched.text
    first = await async_client.post(route, headers=pool.headers, json=body(model=public_model))
    assert first.status_code == 200, first.text
    for source_id in pool.ids:
        assert (await async_client.delete(f"/api/model-sources/{source_id}")).status_code == 204
    account_dispatches = []

    async def account_path(*args, **kwargs):
        account_dispatches.append(True)
        return JSONResponse({"id": "wrong-subscription-path"})

    monkeypatch.setattr(proxy_api, "_collect_responses", account_path)
    response = await async_client.post(
        route, headers=pool.headers, json=body(model=public_model, previous_response_id=first.json()["id"])
    )
    assert not account_dispatches
    assert response.status_code == 409, response.text
    assert len(pool.calls) == 1


async def test_complete_tool_pair_with_returned_item_id_remains_replayable(
    async_client, make_pool, output_upstream
) -> None:
    handler, calls = output_upstream
    pool = await make_pool(2, handler)
    first = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    call = first.json()["output"][1]
    response = await async_client.post(
        "/v1/responses",
        headers=pool.headers,
        json=body(input=[call, {"type": "function_call_output", "call_id": call["call_id"], "output": "complete"}]),
    )
    assert response.status_code == 200, response.text
    assert len(calls) == 2


@pytest.mark.parametrize("override_owner", ["unknown", "other-source"])
async def test_other_source_override_does_not_block_an_owned_continuation(
    async_client, make_pool, override_owner
) -> None:
    pool = await make_pool(2)
    first = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    override_response_id = "unknown-on-unused-source"
    if override_owner == "other-source":
        other = await async_client.post("/v1/responses", headers=pool.headers, json=body())
        assert other.status_code == 200, other.text
        override_response_id = other.json()["id"]
    calls_before = len(pool.calls)
    patched = await async_client.patch(
        f"/api/model-sources/{pool.ids[1]}",
        json={
            "models": [
                {
                    "model": MODEL,
                    "supportsStreaming": True,
                    "supportsTools": True,
                    "rawMetadataJson": json.dumps(
                        {
                            "upstream_model": "upstream-1",
                            "source_request_overrides": {"previous_response_id": override_response_id},
                        }
                    ),
                }
            ]
        },
    )
    assert patched.status_code == 200, patched.text
    response = await async_client.post(
        "/v1/responses", headers=pool.headers, json=body(previous_response_id=first.json()["id"])
    )
    assert response.status_code == 200, response.text
    assert len(pool.calls) == calls_before + 1
    assert pool.calls[-1][0] == "pool-0"

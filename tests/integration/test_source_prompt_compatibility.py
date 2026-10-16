"""Prompt ownership and source-body compatibility through public Responses routes."""

from __future__ import annotations

from datetime import timedelta

import pytest
from openai import AsyncOpenAI
from sqlalchemy import delete

from app.core.utils.time import utcnow
from app.db.models import Account, FileAccountPin, ModelSourceOwnership, RequestLog
from app.db.session import SessionLocal
from app.modules.proxy.source_admission import get_source_bulkhead
from tests.integration import test_model_source_pool as pool_fixtures
from tests.integration.test_model_source_pool import MODEL, ROUTES, body, reservations
from tests.integration.test_source_reference_scope_gaps import _set_model
from tests.integration.test_source_reference_scope_replica import another_replica

make_pool = pool_fixtures.make_pool
pool_clock = pool_fixtures.pool_clock
pytestmark = pytest.mark.integration
CANONICAL_ROUTES = ["/v1/responses", "/backend-api/codex/responses"]


async def _assign(async_client, pool, source_ids):
    result = await async_client.patch(f"/api/api-keys/{pool.key_id}", json={"assignedSourceIds": source_ids})
    assert result.status_code == 200, result.text


@pytest.mark.parametrize("route", CANONICAL_ROUTES)
@pytest.mark.parametrize("target", ["same", "other", "unknown"])
async def test_prompt_and_anchor_must_share_an_owner(async_client, make_pool, route, target):
    pool = await make_pool(2)
    anchors = []
    for index, source_id in enumerate(pool.ids):
        await _assign(async_client, pool, [source_id])
        response = await async_client.post(route, headers=pool.headers, json=body(prompt={"id": f"pmpt_{index}"}))
        assert response.status_code == 200, response.text
        anchors.append(response.json()["id"])
    await _assign(async_client, pool, pool.ids)
    prompt_id = {"same": "pmpt_0", "other": "pmpt_1", "unknown": "pmpt_missing"}[target]
    result = await async_client.post(
        route, headers=pool.headers, json=body(previous_response_id=anchors[0], prompt={"id": prompt_id})
    )
    assert result.status_code == (200 if target == "same" else 409), result.text
    assert len(pool.calls) == (3 if target == "same" else 2)
    if target == "same":
        assert pool.calls[-1][0] == "pool-0"
        assert pool.calls[-1][1]["prompt"] == {"id": prompt_id}
    else:
        assert result.json()["error"]["code"] == "previous_response_owner_unavailable"


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("replace_token", [False, True])
async def test_prompt_only_continuity_survives_replica_and_history(
    async_client, make_pool, monkeypatch, route, replace_token
):
    pool = await make_pool(2)
    await _assign(async_client, pool, pool.ids[:1])
    first = await async_client.post(route, headers=pool.headers, json=body(stream=True, prompt={"id": "pmpt_shared"}))
    assert first.status_code == 200 and "response.completed" in first.text
    # Only durable history remains; the next backend has no local pool state.
    async with SessionLocal() as session:
        await session.execute(delete(ModelSourceOwnership))
        await session.execute(delete(RequestLog))
        await session.commit()
    await _assign(async_client, pool, pool.ids)
    if replace_token:
        updated = await async_client.patch(
            f"/api/model-sources/{pool.ids[0]}", json={"apiKey": "synthetic-replacement"}
        )
        assert updated.status_code == 200
    async with another_replica(monkeypatch) as replica:
        response = await replica.post(route, headers=pool.headers, json=body(prompt={"id": "pmpt_shared"}))
    assert response.status_code == (409 if replace_token else 200), response.text
    assert [token for token, _ in pool.calls] == (["pool-0"] if replace_token else ["pool-0", "pool-0"])


@pytest.mark.parametrize("target", ["same", "other", "unknown"])
async def test_prompt_override_requires_its_own_known_owner(async_client, make_pool, target):
    pool = await make_pool(2)
    for index, source_id in enumerate(pool.ids):
        await _assign(async_client, pool, [source_id])
        response = await async_client.post(
            "/v1/responses", headers=pool.headers, json=body(prompt={"id": f"pmpt_{index}"})
        )
        assert response.status_code == 200
    await _assign(async_client, pool, pool.ids[:1])
    prompt_id = {"same": "pmpt_0", "other": "pmpt_1", "unknown": "pmpt_missing"}[target]
    await _set_model(
        async_client, pool.ids[0], MODEL, "upstream-0", source_request_overrides={"prompt": {"id": prompt_id}}
    )
    response = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert response.status_code == (200 if target == "same" else 409), response.text
    assert len(pool.calls) == (3 if target == "same" else 2)


@pytest.mark.parametrize("route", CANONICAL_ROUTES)
@pytest.mark.parametrize("source_count", [1, 2])
@pytest.mark.parametrize(
    "variable",
    [
        {"type": "input_file", "file_id": "file-prompt-pinned"},
        {"type": "input_image", "file_id": "file-prompt-pinned"},
        {"type": "input_image", "image_url": "sediment://file-prompt-pinned"},
    ],
)
async def test_prompt_variable_files_are_denied_before_reservation(
    async_client, make_pool, route, source_count, variable
):
    pool = await make_pool(source_count)
    await _assign(async_client, pool, pool.ids[:1])
    first = await async_client.post(route, headers=pool.headers, json=body(prompt={"id": "pmpt_external"}))
    assert first.status_code == 200
    await _assign(async_client, pool, pool.ids)
    async with SessionLocal() as session:
        session.add(
            Account(
                id="prompt-file-owner",
                email="file@example.test",
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
                file_id="file-prompt-pinned",
                account_id="prompt-file-owner",
                expires_at=utcnow() + timedelta(days=1),
            )
        )
        await session.commit()
    before = len(await reservations(pool))
    response = await async_client.post(
        route,
        headers=pool.headers,
        json=body(
            previous_response_id=first.json()["id"],
            prompt={"id": "pmpt_external", "variables": {"document": variable}},
        ),
    )
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "model_source_owner_unavailable"
    assert len(pool.calls) == 1
    assert len(await reservations(pool)) == before
    assert all(get_source_bulkhead().in_flight(source_id) == 0 for source_id in pool.ids)


@pytest.mark.parametrize("override_kind", ["unknown-prompt", "file-variable"])
async def test_invalid_prompt_override_does_not_block_a_safe_candidate(async_client, make_pool, override_kind):
    pool = await make_pool(2)
    await _assign(async_client, pool, pool.ids[:1])
    first = await async_client.post("/v1/responses", headers=pool.headers, json=body(prompt={"id": "pmpt_0"}))
    assert first.status_code == 200
    await _assign(async_client, pool, pool.ids)
    prompt = (
        {"id": "pmpt_missing"}
        if override_kind == "unknown-prompt"
        else {
            "id": "pmpt_0",
            "variables": {"document": {"type": "input_file", "file_id": "file_override"}},
        }
    )
    await _set_model(async_client, pool.ids[0], MODEL, "upstream-0", source_request_overrides={"prompt": prompt})
    response = await async_client.post("/v1/responses", headers=pool.headers, json=body())
    assert response.status_code == 200, response.text
    assert pool.calls[-1][0] == "pool-1"
    assert "prompt" not in pool.calls[-1][1]


@pytest.mark.parametrize(
    "variables",
    [
        {
            "text": "file-text-is-not-a-reference",
            "file": {"type": "input_file", "filename": "x.txt", "file_data": "eA=="},
        },
        {"image": {"type": "input_image", "image_url": "https://example.test/image.png"}},
        {"malformed": {"type": [], "file_id": "not-a-valid-reference"}},
    ],
)
async def test_reference_free_prompt_variables_are_forwarded_unchanged(async_client, make_pool, variables):
    pool = await make_pool(1)
    prompt = {"id": "pmpt_external", "variables": variables}
    response = await async_client.post("/v1/responses", headers=pool.headers, json=body(prompt=prompt))
    assert response.status_code == 200, response.text
    assert pool.calls[-1][1]["prompt"] == prompt


def _compacted_input():
    return [
        {
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": "Local compact fallback preserved the latest encrypted reasoning state.",
                }
            ],
        },
        {"type": "compaction", "encrypted_content": "synthetic-external-compaction"},
        {"role": "user", "content": "continue"},
    ]


@pytest.mark.parametrize("route", CANONICAL_ROUTES)
async def test_original_compacted_history_is_not_an_override(async_client, make_pool, route):
    pool = await make_pool(1)
    history = _compacted_input()
    response = await async_client.post(route, headers=pool.headers, json=body(input=history))
    assert response.status_code == 200, response.text
    assert pool.calls[-1][1]["input"] == history


@pytest.mark.parametrize("route", CANONICAL_ROUTES)
@pytest.mark.parametrize("owner_change", ["replacement", "removed"])
async def test_compaction_ownership_is_retained_without_subscription_cleanup(
    async_client, make_pool, route, owner_change
):
    pool = await make_pool(1)
    history = _compacted_input()
    first = await async_client.post(route, headers=pool.headers, json=body(input=history[1:]))
    assert first.status_code == 200
    if owner_change == "replacement":
        update = await async_client.patch(f"/api/model-sources/{pool.ids[0]}", json={"apiKey": "synthetic-replacement"})
        assert update.status_code == 200
    else:
        assert (await async_client.delete(f"/api/model-sources/{pool.ids[0]}")).status_code == 204
    response = await async_client.post(route, headers=pool.headers, json=body(input=history))
    assert response.status_code == 409, response.text
    assert len(pool.calls) == 1


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("value", [0, 5, 20])
async def test_top_logprobs_survives_pool_transition(async_client, make_pool, route, value):
    pool = await make_pool(2)
    payload = body(top_logprobs=value, include=["message.output_text.logprobs"])
    for source_ids in [pool.ids[:1], pool.ids]:
        await _assign(async_client, pool, source_ids)
        response = await async_client.post(route, headers=pool.headers, json=payload)
        assert response.status_code == 200, response.text
        assert pool.calls[-1][1]["top_logprobs"] == value
        assert pool.calls[-1][1]["include"] == payload["include"]


@pytest.mark.parametrize("value", [True, -1, 21, 2.5, "5", [], {}])
async def test_invalid_top_logprobs_does_not_become_portable(async_client, make_pool, value):
    pool = await make_pool(2)
    response = await async_client.post("/v1/responses", headers=pool.headers, json=body(top_logprobs=value))
    assert response.status_code == 409, response.text
    assert pool.calls == []
    assert not await reservations(pool)


@pytest.mark.parametrize("prefix", ["/v1", "/backend-api/codex"])
async def test_sdk_stream_top_logprobs_can_fail_over(async_client, make_pool, prefix):
    pool = await make_pool(2)
    pool.statuses["pool-0"] = 429
    sdk = AsyncOpenAI(
        api_key=pool.headers["Authorization"].removeprefix("Bearer "),
        base_url=f"http://testserver{prefix}",
        http_client=async_client,
        max_retries=0,
    )
    stream = await sdk.responses.create(
        model=MODEL,
        input="hello",
        stream=True,
        top_logprobs=5,
        include=["message.output_text.logprobs"],
    )
    assert any(event.type == "response.completed" for event in [event async for event in stream])
    assert [token for token, _ in pool.calls] == ["pool-0", "pool-1"]
    assert all(sent["top_logprobs"] == 5 for _, sent in pool.calls)
    assert sorted(item.status for item in await reservations(pool)) == ["finalized", "released"]

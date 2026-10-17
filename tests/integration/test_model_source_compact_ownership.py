"""Native compact ownership survives a competing external model source."""

from __future__ import annotations

import json

import pytest
from aiohttp import web

import app.modules.proxy.service as proxy_module
from app.core.openai.models import CompactResponsePayload
from app.db.session import SessionLocal
from app.modules.proxy.durable_bridge_repository import DurableBridgeRepository, durable_bridge_api_key_scope
from tests.integration.compact_test_helpers import _make_auth_json
from tests.integration.model_source_helpers import _create_model_source, _enable_api_key_auth, stub_source_upstreams

pytestmark = pytest.mark.integration


async def _import_native_account(async_client):
    auth = _make_auth_json("native-compact-account", "compact@example.com")
    response = await async_client.post(
        "/api/accounts/import", files={"auth_json": ("auth.json", json.dumps(auth), "application/json")}
    )
    assert response.status_code == 200, response.text
    return response.json()["accountId"]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["/v1/responses/compact", "/backend-api/codex/responses/compact"])
async def test_compact_native_model_keeps_registry_precedence(async_client, monkeypatch, route):
    await _import_native_account(async_client)
    native_accounts = []
    source_requests = []

    async def native_compact(payload, headers, access_token, account_id):
        native_accounts.append(account_id)
        return CompactResponsePayload.model_validate(
            {"object": "response.compaction", "output": [{"type": "compaction", "encrypted_content": "native"}]}
        )

    async def source(request):
        source_requests.append(request.path)
        return web.json_response({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", native_compact)
    async with stub_source_upstreams() as start:
        base = await start(source)
        await _create_model_source(
            async_client, name="native-collision", model="gpt-5.6-sol", base_url=base, supports_responses=True
        )
        response = await async_client.post(route, json={"model": "gpt-5.6-sol", "instructions": "Compact", "input": []})

    assert response.status_code == 200, response.text
    assert response.json()["output"][0]["encrypted_content"] == "native"
    assert native_accounts == ["native-compact-account"]
    assert source_requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("pin", ["file", "turn-state"])
@pytest.mark.parametrize("route", ["/v1/responses/compact", "/backend-api/codex/responses/compact"])
async def test_compact_native_file_and_durable_turn_owners_precede_source(async_client, monkeypatch, route, pin):
    account_id = await _import_native_account(async_client)
    await _enable_api_key_auth(async_client)
    created = await async_client.post("/api/api-keys/", json={"name": "pinned-compact-key"})
    assert created.status_code == 200
    key_id = created.json()["id"]
    headers = {"Authorization": f"Bearer {created.json()['key']}"}
    native_accounts = []
    source_requests = []
    body = {"model": "gpt-5.6-sol", "instructions": "Compact", "input": []}

    async def native_compact(payload, headers, access_token, account_id):
        native_accounts.append(account_id)
        return CompactResponsePayload.model_validate(
            {"object": "response.compaction", "output": [{"type": "compaction", "encrypted_content": "native"}]}
        )

    async def native_file(**kwargs):
        return {"file_id": "file_native_compact", "upload_url": "https://example.invalid/upload"}

    async def source(request):
        source_requests.append(request.path)
        return web.json_response({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", native_compact)
    monkeypatch.setattr(proxy_module, "core_create_file", native_file)
    if pin == "file":
        uploaded = await async_client.post(
            "/backend-api/files", headers=headers, json={"file_name": "note.txt", "file_size": 42}
        )
        assert uploaded.status_code == 200, uploaded.text
        body["input"] = [{"role": "user", "content": [{"type": "input_file", "file_id": uploaded.json()["file_id"]}]}]
    else:
        # Seed the durable session a different replica wrote. The request still
        # resolves it through the real compact route and repository lookup.
        async with SessionLocal() as session:
            repository = DurableBridgeRepository(session)
            snapshot = await repository.claim_session(
                session_key_kind="codex_session",
                session_key_value="fixture-session",
                api_key_scope=durable_bridge_api_key_scope(key_id),
                instance_id="fixture-replica",
                lease_ttl_seconds=60,
                account_id=account_id,
                model="gpt-5.6-sol",
                service_tier=None,
                latest_turn_state="opaque-native-turn",
                latest_response_id=None,
                allow_takeover=False,
                owner_process_epoch="fixture-epoch",
            )
            await repository.upsert_alias(
                session_id=snapshot.id,
                alias_kind="turn_state",
                alias_value="opaque-native-turn",
                api_key_scope=durable_bridge_api_key_scope(key_id),
            )
        headers["x-codex-turn-state"] = "opaque-native-turn"

    async with stub_source_upstreams() as start:
        base = await start(source)
        source_id = await _create_model_source(
            async_client, name="pinned-collision", model="gpt-5.6-sol", base_url=base, supports_responses=True
        )
        assigned = await async_client.patch(f"/api/api-keys/{key_id}", json={"assignedSourceIds": [source_id]})
        assert assigned.status_code == 200
        response = await async_client.post(route, headers=headers, json=body)

    assert response.status_code == 200, response.text
    assert response.json()["output"][0]["encrypted_content"] == "native"
    assert native_accounts == ["native-compact-account"]
    assert source_requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False], ids=["enabled-source", "disabled-source"])
@pytest.mark.parametrize("route", ["/v1/responses/compact", "/backend-api/codex/responses/compact"])
async def test_compact_previous_response_owner_precedes_assigned_source(async_client, monkeypatch, route, enabled):
    await _import_native_account(async_client)
    await _enable_api_key_auth(async_client)
    created = await async_client.post("/api/api-keys/", json={"name": "continuity-key"})
    assert created.status_code == 200, created.text
    key_id = created.json()["id"]
    headers = {"Authorization": f"Bearer {created.json()['key']}"}
    native_accounts = []
    source_requests = []

    async def native_response(payload, headers, access_token, account_id, **kwargs):
        yield (
            'data: {"type":"response.created","response":{"id":"resp_native_compact",'
            '"object":"response","status":"in_progress","output":[]}}\n\n'
        )
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_native_compact",'
            '"object":"response","status":"completed","output":[]}}\n\n'
        )

    async def native_compact(payload, headers, access_token, account_id):
        native_accounts.append(account_id)
        return CompactResponsePayload.model_validate(
            {"object": "response.compaction", "output": [{"type": "compaction", "encrypted_content": "native"}]}
        )

    async def source(request):
        source_requests.append(request.path)
        return web.json_response({"object": "response.compaction", "output": []})

    monkeypatch.setattr(proxy_module, "core_stream_responses", native_response)
    monkeypatch.setattr(proxy_module, "core_compact_responses", native_compact)
    first = await async_client.post(
        "/v1/responses", headers=headers, json={"model": "gpt-5.6-sol", "input": "Initial native turn", "stream": False}
    )
    assert first.status_code == 200, first.text
    assert first.json()["id"] == "resp_native_compact"

    async with stub_source_upstreams() as start:
        base = await start(source)
        source_id = await _create_model_source(
            async_client, name="later-source", model="gpt-5.6-sol", base_url=base, supports_responses=True
        )
        assigned = await async_client.patch(f"/api/api-keys/{key_id}", json={"assignedSourceIds": [source_id]})
        assert assigned.status_code == 200, assigned.text
        if not enabled:
            disabled = await async_client.patch(f"/api/model-sources/{source_id}", json={"isEnabled": False})
            assert disabled.status_code == 200
        response = await async_client.post(
            route,
            headers=headers,
            json={
                "model": "gpt-5.6-sol",
                "instructions": "Compact",
                "input": [],
                "previous_response_id": first.json()["id"],
            },
        )

    assert response.status_code == 200, response.text
    assert response.json()["output"][0]["encrypted_content"] == "native"
    assert native_accounts == ["native-compact-account"]
    assert source_requests == []

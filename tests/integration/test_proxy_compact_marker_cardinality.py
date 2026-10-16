from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.modules.proxy.service as proxy_module
from app.core.auth import generate_unique_account_id
from app.core.openai.models import CompactResponsePayload
from app.db.models import ApiKey
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeysService
from tests.integration.compact_test_helpers import _make_auth_json

pytestmark = pytest.mark.integration

ROUTES = ["/backend-api/codex/responses/compact", "/v1/responses/compact"]
MARKERS = ["turn_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "http_turn_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"]


async def _scoped_key(async_client, candidate_count: int) -> tuple[str, list[str]]:
    account_ids = []
    # The global pool is always ambiguous, even when the key permits one account.
    for index in range(3):
        raw_id = f"acc_marker_cardinality_{index}"
        email = f"marker-cardinality-{index}@example.com"
        imported = await async_client.post(
            "/api/accounts/import",
            files={"auth_json": ("auth.json", json.dumps(_make_auth_json(raw_id, email)), "application/json")},
        )
        assert imported.status_code == 200
        account_ids.append(generate_unique_account_id(raw_id, email))
    async with SessionLocal() as session:
        created = await ApiKeysService(ApiKeysRepository(session)).create_key(
            ApiKeyCreateData(
                name="marker-cardinality", allowed_models=None, assigned_account_ids=account_ids[:candidate_count]
            )
        )
        if candidate_count == 0:
            # An emptied assignment scope must not silently become unrestricted.
            key = await session.get(ApiKey, created.id)
            assert key is not None
            key.account_assignment_scope_enabled = True
            await session.commit()
    settings = await async_client.put("/api/settings", json={"apiKeyAuthEnabled": True, "stickyThreadsEnabled": False})
    assert settings.status_code == 200
    return created.key, account_ids


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("marker", MARKERS)
@pytest.mark.parametrize("candidate_count", [0, 1, 2])
async def test_marker_only_compact_requires_sole_scoped_candidate(
    async_client, monkeypatch, route: str, marker: str, candidate_count: int
):
    key, _ = await _scoped_key(async_client, candidate_count)
    dispatch = AsyncMock(
        return_value=CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})
    )
    monkeypatch.setattr(proxy_module, "core_compact_responses", dispatch)

    response = await async_client.post(
        route,
        headers={"Authorization": f"Bearer {key}", "x-codex-turn-state": marker},
        json={"model": "gpt-5.1", "instructions": "continue", "input": []},
    )

    if candidate_count == 1:
        assert response.status_code == 200, response.text
        assert response.json()["object"] == "response.compaction"
        dispatch.assert_awaited_once()
        assert dispatch.await_args is not None
        assert dispatch.await_args.args[3] == "acc_marker_cardinality_0"
    else:
        assert response.status_code == 502, response.text
        assert response.json()["error"] == {
            "code": "previous_response_owner_unavailable",
            "message": "Previous response owner account is unavailable; retry later.",
            "type": "server_error",
        }
        dispatch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("marker", MARKERS)
@pytest.mark.parametrize("owner_source", ["registered-turn", "input-file"])
async def test_marker_only_compact_preserves_known_owner(
    async_client, monkeypatch, route: str, marker: str, owner_source: str
):
    key, account_ids = await _scoped_key(async_client, 2)
    service = get_proxy_service_for_app(async_client._transport.app)
    payload = {"model": "gpt-5.1", "instructions": "continue", "input": []}
    if owner_source == "registered-turn":
        monkeypatch.setattr(
            service._durable_bridge,
            "lookup_turn_state_target",
            AsyncMock(return_value=SimpleNamespace(account_id=account_ids[1], session_id="registered-marker-session")),
        )
    else:
        await service._pin_file_account("file_marker_cardinality", account_ids[1])
        payload["input"] = [{"role": "user", "content": [{"type": "input_file", "file_id": "file_marker_cardinality"}]}]
    dispatch = AsyncMock(
        return_value=CompactResponsePayload.model_validate({"object": "response.compaction", "output": []})
    )
    monkeypatch.setattr(proxy_module, "core_compact_responses", dispatch)

    response = await async_client.post(
        route,
        headers={"Authorization": f"Bearer {key}", "x-codex-turn-state": marker},
        json=payload,
    )

    assert response.status_code == 200, response.text
    dispatch.assert_awaited_once()
    assert dispatch.await_args is not None
    assert dispatch.await_args.args[3] == "acc_marker_cardinality_1"

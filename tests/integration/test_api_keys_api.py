from __future__ import annotations

import base64
import json

import pytest
from sqlalchemy import select

import app.modules.proxy.service as proxy_module
from app.core.auth import generate_unique_account_id
from app.core.openai.model_registry import ReasoningLevel, UpstreamModel, get_model_registry
from app.core.openai.models import OpenAIResponsePayload
from app.db.models import RequestLog
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository

pytestmark = pytest.mark.integration

_TEST_MODELS = ["model-alpha", "model-beta"]


def _make_upstream_model(slug: str) -> UpstreamModel:
    return UpstreamModel(
        slug=slug,
        display_name=slug,
        description=f"Test model {slug}",
        context_window=128000,
        input_modalities=("text",),
        supported_reasoning_levels=(ReasoningLevel(effort="medium", description="default"),),
        default_reasoning_level="medium",
        supports_reasoning_summaries=False,
        support_verbosity=False,
        default_verbosity=None,
        prefer_websockets=False,
        supports_parallel_tool_calls=True,
        supported_in_api=True,
        minimal_client_version=None,
        priority=0,
        available_in_plans=frozenset({"plus", "pro"}),
        raw={},
    )


def _populate_test_registry() -> None:
    registry = get_model_registry()
    models = [_make_upstream_model(slug) for slug in _TEST_MODELS]
    registry.update({"plus": models, "pro": models})


def _encode_jwt(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _make_auth_json(account_id: str, email: str) -> dict:
    payload = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    return {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": account_id,
        },
    }


async def _import_account(async_client, account_id: str, email: str) -> str:
    auth_json = _make_auth_json(account_id, email)
    files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    return generate_unique_account_id(account_id, email)


@pytest.mark.asyncio
async def test_api_keys_crud_and_regenerate(async_client):
    create = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "dev-key",
            "allowedModels": [],
            "weeklyTokenLimit": 1000,
        },
    )
    assert create.status_code == 200
    payload = create.json()
    assert payload["name"] == "dev-key"
    assert payload["key"].startswith("sk-clb-")
    key_id = payload["id"]
    first_key = payload["key"]

    listed = await async_client.get("/api/api-keys/")
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["id"] == key_id
    assert "key" not in rows[0]

    updated = await async_client.patch(
        f"/api/api-keys/{key_id}",
        json={
            "name": "prod-key",
            "isActive": False,
        },
    )
    assert updated.status_code == 200
    updated_payload = updated.json()
    assert updated_payload["name"] == "prod-key"
    assert updated_payload["isActive"] is False

    regenerated = await async_client.post(f"/api/api-keys/{key_id}/regenerate")
    assert regenerated.status_code == 200
    regenerated_payload = regenerated.json()
    assert regenerated_payload["id"] == key_id
    assert regenerated_payload["key"].startswith("sk-clb-")
    assert regenerated_payload["key"] != first_key

    deleted = await async_client.delete(f"/api/api-keys/{key_id}")
    assert deleted.status_code == 204

    listed_after_delete = await async_client.get("/api/api-keys/")
    assert listed_after_delete.status_code == 200
    assert listed_after_delete.json() == []


@pytest.mark.asyncio
async def test_api_key_model_restriction_and_models_filter(async_client):
    _populate_test_registry()
    model_ids = sorted(_TEST_MODELS)
    assert len(model_ids) >= 2
    allowed_model = model_ids[0]
    blocked_model = model_ids[1]

    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200

    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "restricted",
            "allowedModels": [allowed_model],
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]

    models = await async_client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert models.status_code == 200
    returned_ids = [item["id"] for item in models.json()["data"]]
    assert returned_ids == [allowed_model]

    blocked = await async_client.post(
        "/backend-api/codex/responses",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": blocked_model, "instructions": "hi", "input": [], "stream": True},
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "model_not_allowed"


@pytest.mark.asyncio
async def test_api_key_usage_tracking_and_request_log_link(async_client, monkeypatch):
    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200

    created = await async_client.post("/api/api-keys/", json={"name": "usage-key"})
    assert created.status_code == 200
    payload = created.json()
    key = payload["key"]
    key_id = payload["id"]

    await _import_account(async_client, "acc_usage_key", "usage-key@example.com")

    async def fake_stream(_payload, _headers, _access_token, _account_id, base_url=None, raise_for_status=False):
        usage = {"input_tokens": 10, "output_tokens": 5}
        event = {"type": "response.completed", "response": {"id": "resp_1", "usage": usage}}
        yield f"data: {json.dumps(event)}\n\n"

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": _TEST_MODELS[0],
            "instructions": "hi",
            "input": [],
            "stream": True,
        },
    ) as response:
        assert response.status_code == 200
        _ = [line async for line in response.aiter_lines() if line]

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        row = await repo.get_by_id(key_id)
        assert row is not None
        assert row.weekly_tokens_used == 15
        assert row.last_used_at is not None

        result = await session.execute(select(RequestLog).order_by(RequestLog.requested_at.desc()))
        latest_log = result.scalars().first()
        assert latest_log is not None
        assert latest_log.api_key_id == key_id


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/backend-api/codex/responses/compact", "/v1/responses/compact"])
async def test_api_key_weekly_limit_applies_to_compact_responses(async_client, monkeypatch, endpoint):
    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200

    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "compact-usage-limit",
            "weeklyTokenLimit": 10,
        },
    )
    assert created.status_code == 200
    payload = created.json()
    key = payload["key"]
    key_id = payload["id"]

    await _import_account(async_client, "acc_compact_usage_key", "compact-usage-key@example.com")

    seen = {"calls": 0}

    async def fake_compact(_payload, _headers, _access_token, _account_id):
        seen["calls"] += 1
        return OpenAIResponsePayload.model_validate(
            {
                "id": "resp_compact_1",
                "status": "completed",
                "usage": {
                    "input_tokens": 7,
                    "output_tokens": 5,
                    "total_tokens": 12,
                },
                "output": [],
            }
        )

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    request_payload = {
        "model": _TEST_MODELS[0],
        "instructions": "hi",
        "input": [],
    }

    first = await async_client.post(
        endpoint,
        headers={"Authorization": f"Bearer {key}"},
        json=request_payload,
    )
    assert first.status_code == 200

    blocked = await async_client.post(
        endpoint,
        headers={"Authorization": f"Bearer {key}"},
        json=request_payload,
    )
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limit_exceeded"
    assert seen["calls"] == 1

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        row = await repo.get_by_id(key_id)
        assert row is not None
        assert row.weekly_tokens_used == 12

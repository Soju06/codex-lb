from __future__ import annotations

import asyncio
import base64
import json
from datetime import timedelta

import pytest
from sqlalchemy import select

import app.modules.proxy.service as proxy_module
from app.core.auth import generate_unique_account_id
from app.core.clients.proxy import ProxyResponseError
from app.core.openai.model_registry import ReasoningLevel, UpstreamModel, get_model_registry
from app.core.openai.models import OpenAIResponsePayload
from app.core.utils.time import utcnow
from app.db.models import RequestLog
from app.db.session import SessionLocal
from app.modules.api_keys.repository import ApiKeysRepository

pytestmark = pytest.mark.integration

_TEST_MODELS = ["model-alpha", "model-beta"]
_HIDDEN_MODEL = "model-hidden"


def _make_upstream_model(slug: str, *, supported_in_api: bool = True) -> UpstreamModel:
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
        supported_in_api=supported_in_api,
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
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 1000},
            ],
        },
    )
    assert create.status_code == 200
    payload = create.json()
    assert payload["name"] == "dev-key"
    assert payload["key"].startswith("sk-clb-")
    assert len(payload["limits"]) == 1
    assert payload["limits"][0]["limitType"] == "total_tokens"
    assert payload["limits"][0]["maxValue"] == 1000
    assert "weeklyTokenLimit" not in payload
    key_id = payload["id"]
    first_key = payload["key"]

    listed = await async_client.get("/api/api-keys/")
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["id"] == key_id
    assert "key" not in rows[0]
    assert len(rows[0]["limits"]) == 1

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
async def test_api_keys_crud_with_legacy_weekly_limit(async_client):
    """Legacy weeklyTokenLimit field is auto-converted to a limit rule."""
    create = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "legacy-key",
            "weeklyTokenLimit": 500,
        },
    )
    assert create.status_code == 200
    payload = create.json()
    assert len(payload["limits"]) == 1
    assert payload["limits"][0]["limitType"] == "total_tokens"
    assert payload["limits"][0]["limitWindow"] == "weekly"
    assert payload["limits"][0]["maxValue"] == 500
    assert "weeklyTokenLimit" not in payload

    await async_client.delete(f"/api/api-keys/{payload['id']}")


@pytest.mark.asyncio
async def test_api_keys_update_limits(async_client):
    """Updating limits replaces the entire set."""
    create = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "update-limits-key",
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 1000},
            ],
        },
    )
    assert create.status_code == 200
    key_id = create.json()["id"]

    updated = await async_client.patch(
        f"/api/api-keys/{key_id}",
        json={
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "daily", "maxValue": 500},
                {"limitType": "cost_usd", "limitWindow": "monthly", "maxValue": 10_000_000},
            ],
        },
    )
    assert updated.status_code == 200
    assert len(updated.json()["limits"]) == 2
    types = {li["limitType"] for li in updated.json()["limits"]}
    assert types == {"total_tokens", "cost_usd"}

    await async_client.delete(f"/api/api-keys/{key_id}")


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

    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "usage-key",
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 1_000_000},
            ],
        },
    )
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
        assert row.last_used_at is not None

        # Verify usage tracked in limits table
        limits = await repo.get_limits_by_key(key_id)
        assert len(limits) == 1
        assert limits[0].current_value == 15  # 10 input + 5 output

        result = await session.execute(select(RequestLog).order_by(RequestLog.requested_at.desc()))
        latest_log = result.scalars().first()
        assert latest_log is not None
        assert latest_log.api_key_id == key_id


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/backend-api/codex/responses/compact", "/v1/responses/compact"])
async def test_api_key_limit_applies_to_compact_responses(async_client, monkeypatch, endpoint):
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
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 10},
            ],
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
                "model": _TEST_MODELS[0],
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
        limits = await repo.get_limits_by_key(key_id)
        assert len(limits) == 1
        assert limits[0].current_value == 12  # 7 input + 5 output


@pytest.mark.asyncio
async def test_api_key_reservation_released_on_compact_upstream_failure(async_client, monkeypatch):
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
            "name": "compact-upstream-fail",
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 100},
            ],
        },
    )
    assert created.status_code == 200
    payload = created.json()
    key = payload["key"]
    key_id = payload["id"]

    await _import_account(async_client, "acc_compact_upstream_fail", "compact-upstream-fail@example.com")

    async def failing_compact(_payload, _headers, _access_token, _account_id):
        raise ProxyResponseError(
            502,
            {"error": {"message": "upstream failed", "type": "server_error", "code": "upstream_error"}},
        )

    monkeypatch.setattr(proxy_module, "core_compact_responses", failing_compact)

    response = await async_client.post(
        "/v1/responses/compact",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": _TEST_MODELS[0], "instructions": "hi", "input": []},
    )
    assert response.status_code == 502

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        assert len(limits) == 1
        assert limits[0].current_value == 0


@pytest.mark.asyncio
async def test_api_key_limit_parallel_requests_do_not_exceed_quota(async_client, monkeypatch):
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
            "name": "compact-parallel-limit",
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 30},
            ],
        },
    )
    assert created.status_code == 200
    payload = created.json()
    key = payload["key"]
    key_id = payload["id"]

    await _import_account(async_client, "acc_compact_parallel", "compact-parallel@example.com")

    async def fake_compact(_payload, _headers, _access_token, _account_id):
        await asyncio.sleep(0.05)
        return OpenAIResponsePayload.model_validate(
            {
                "id": "resp_compact_parallel",
                "model": _TEST_MODELS[0],
                "status": "completed",
                "usage": {
                    "input_tokens": 7,
                    "output_tokens": 3,
                    "total_tokens": 10,
                },
                "output": [],
            }
        )

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    async def _send_once():
        return await async_client.post(
            "/v1/responses/compact",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": _TEST_MODELS[0], "instructions": "hi", "input": []},
        )

    responses = await asyncio.gather(*[_send_once() for _ in range(5)])
    allowed = [resp for resp in responses if resp.status_code == 200]
    blocked = [resp for resp in responses if resp.status_code == 429]
    assert len(allowed) >= 1
    assert len(allowed) + len(blocked) == 5

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        assert len(limits) == 1
        assert limits[0].current_value <= 30


@pytest.mark.asyncio
async def test_api_key_limit_atomic_with_global_and_model_scope(async_client):
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
            "name": "atomic-global-model",
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 100},
                {
                    "limitType": "total_tokens",
                    "limitWindow": "weekly",
                    "maxValue": 5,
                    "modelFilter": _TEST_MODELS[0],
                },
            ],
        },
    )
    assert created.status_code == 200
    payload = created.json()
    key = payload["key"]
    key_id = payload["id"]

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        model_limit = next(limit for limit in limits if limit.model_filter == _TEST_MODELS[0])
        model_limit.current_value = 5
        await session.commit()

    blocked = await async_client.post(
        "/v1/responses/compact",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": _TEST_MODELS[0], "instructions": "hi", "input": []},
    )
    assert blocked.status_code == 429

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        global_limit = next(limit for limit in limits if limit.model_filter is None)
        model_limit = next(limit for limit in limits if limit.model_filter == _TEST_MODELS[0])
        assert global_limit.current_value == 0
        assert model_limit.current_value == 5


@pytest.mark.asyncio
async def test_model_scoped_limit_allows_other_models(async_client, monkeypatch):
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
            "name": "model-scoped-limit",
            "limits": [
                {
                    "limitType": "total_tokens",
                    "limitWindow": "weekly",
                    "maxValue": 5,
                    "modelFilter": _TEST_MODELS[0],
                },
            ],
        },
    )
    assert created.status_code == 200
    payload = created.json()
    key = payload["key"]
    key_id = payload["id"]

    await _import_account(async_client, "acc_model_scoped_other", "model-scoped-other@example.com")

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        assert len(limits) == 1
        limits[0].current_value = 5
        await session.commit()

    async def fake_compact(_payload, _headers, _access_token, _account_id):
        return OpenAIResponsePayload.model_validate(
            {
                "id": "resp_model_scope_other",
                "model": _TEST_MODELS[1],
                "status": "completed",
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                "output": [],
            }
        )

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    allowed = await async_client.post(
        "/v1/responses/compact",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": _TEST_MODELS[1], "instructions": "hi", "input": []},
    )
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_model_scoped_limit_does_not_block_v1_models(async_client):
    _populate_test_registry()

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
            "name": "model-scoped-v1-models",
            "limits": [
                {
                    "limitType": "total_tokens",
                    "limitWindow": "weekly",
                    "maxValue": 5,
                    "modelFilter": _TEST_MODELS[0],
                },
            ],
        },
    )
    assert created.status_code == 200
    payload = created.json()
    key = payload["key"]
    key_id = payload["id"]

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        assert len(limits) == 1
        limits[0].current_value = 5
        await session.commit()

    allowed = await async_client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_global_limit_blocks_models_and_response_routes(async_client):
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
            "name": "global-limit-lock",
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 1},
            ],
        },
    )
    assert created.status_code == 200
    payload = created.json()
    key = payload["key"]
    key_id = payload["id"]

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        assert len(limits) == 1
        limits[0].current_value = 1
        await session.commit()

    blocked_models = await async_client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert blocked_models.status_code == 429
    assert blocked_models.json()["error"]["code"] == "rate_limit_exceeded"

    blocked_responses = await async_client.post(
        "/v1/responses/compact",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": _TEST_MODELS[0], "instructions": "hi", "input": []},
    )
    assert blocked_responses.status_code == 429
    assert blocked_responses.json()["error"]["code"] == "rate_limit_exceeded"


@pytest.mark.asyncio
async def test_update_key_metadata_only_preserves_limit_usage_state(async_client):
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "preserve-usage-metadata",
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 1000},
            ],
        },
    )
    assert created.status_code == 200
    key_id = created.json()["id"]

    original_reset_at = utcnow() + timedelta(hours=12)
    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        assert len(limits) == 1
        limits[0].current_value = 345
        limits[0].reset_at = original_reset_at
        await session.commit()

    name_only = await async_client.patch(
        f"/api/api-keys/{key_id}",
        json={"name": "renamed-only"},
    )
    assert name_only.status_code == 200

    active_only = await async_client.patch(
        f"/api/api-keys/{key_id}",
        json={"isActive": False},
    )
    assert active_only.status_code == 200

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        assert len(limits) == 1
        assert limits[0].current_value == 345
        assert limits[0].reset_at == original_reset_at


@pytest.mark.asyncio
async def test_update_key_same_policy_and_max_change_preserve_usage_state(async_client):
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "preserve-usage-policy",
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 1000},
            ],
        },
    )
    assert created.status_code == 200
    key_id = created.json()["id"]

    original_reset_at = utcnow() + timedelta(hours=8)
    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        assert len(limits) == 1
        limits[0].current_value = 222
        limits[0].reset_at = original_reset_at
        await session.commit()

    unchanged = await async_client.patch(
        f"/api/api-keys/{key_id}",
        json={
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 1000},
            ],
        },
    )
    assert unchanged.status_code == 200

    max_changed = await async_client.patch(
        f"/api/api-keys/{key_id}",
        json={
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 2000},
            ],
        },
    )
    assert max_changed.status_code == 200
    assert max_changed.json()["limits"][0]["maxValue"] == 2000

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        assert len(limits) == 1
        assert limits[0].current_value == 222
        assert limits[0].reset_at == original_reset_at
        assert limits[0].max_value == 2000


@pytest.mark.asyncio
async def test_update_key_reset_usage_requires_explicit_action(async_client):
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "reset-usage-explicit",
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 1000},
            ],
        },
    )
    assert created.status_code == 200
    key_id = created.json()["id"]

    original_reset_at = utcnow() + timedelta(hours=4)
    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        assert len(limits) == 1
        limits[0].current_value = 111
        limits[0].reset_at = original_reset_at
        await session.commit()

    untouched = await async_client.patch(
        f"/api/api-keys/{key_id}",
        json={"name": "still-no-reset"},
    )
    assert untouched.status_code == 200

    explicit_reset = await async_client.patch(
        f"/api/api-keys/{key_id}",
        json={"resetUsage": True},
    )
    assert explicit_reset.status_code == 200

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        assert len(limits) == 1
        assert limits[0].current_value == 0
        assert limits[0].reset_at > original_reset_at


@pytest.mark.asyncio
async def test_allowed_but_unsupported_model_is_exposed(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model(_TEST_MODELS[0], supported_in_api=True),
        _make_upstream_model(_HIDDEN_MODEL, supported_in_api=False),
    ]
    registry.update({"plus": models, "pro": models})

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
            "name": "allowed-hidden",
            "allowedModels": [_TEST_MODELS[0], _HIDDEN_MODEL],
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]

    listed = await async_client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert listed.status_code == 200
    ids = {item["id"] for item in listed.json()["data"]}
    assert _TEST_MODELS[0] in ids
    assert _HIDDEN_MODEL in ids


# ---------------------------------------------------------------------------
# Reservation lifecycle regression tests (fix-api-key-reservation-leak)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_401_retry_success_finalizes_once(async_client, monkeypatch):
    """401 refresh retry 성공 시 finalize 1회만 호출되어 usage가 정확히 반영된다."""
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
            "name": "stream-401-retry",
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 1000},
            ],
        },
    )
    assert created.status_code == 200
    payload = created.json()
    key = payload["key"]
    key_id = payload["id"]

    await _import_account(async_client, "acc_401_retry", "acc-401-retry@example.com")

    call_count = {"value": 0}

    async def fake_stream(_payload, _headers, _access_token, _account_id, base_url=None, raise_for_status=False):
        call_count["value"] += 1
        if call_count["value"] == 1:
            raise ProxyResponseError(
                401,
                {"error": {"message": "unauthorized", "type": "auth_error", "code": "unauthorized"}},
            )
        usage = {"input_tokens": 20, "output_tokens": 10}
        event = {"type": "response.completed", "response": {"id": "resp_retry", "usage": usage}}
        yield f"data: {json.dumps(event)}\n\n"

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": _TEST_MODELS[0], "instructions": "hi", "input": [], "stream": True},
    ) as response:
        assert response.status_code == 200
        _ = [line async for line in response.aiter_lines() if line]

    assert call_count["value"] == 2  # first failed, second succeeded

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        assert len(limits) == 1
        assert limits[0].current_value == 30  # 20 input + 10 output, finalized once


@pytest.mark.asyncio
async def test_stream_no_accounts_releases_reservation(async_client, monkeypatch):
    """no_accounts 즉시 종료 시 reservation이 release되어 quota가 원복된다."""
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
            "name": "stream-no-accounts",
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 100},
            ],
        },
    )
    assert created.status_code == 200
    payload = created.json()
    key = payload["key"]
    key_id = payload["id"]

    # Intentionally NOT importing any accounts → no_accounts path

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": _TEST_MODELS[0], "instructions": "hi", "input": [], "stream": True},
    ) as response:
        assert response.status_code == 200
        lines = [line async for line in response.aiter_lines() if line]
        assert any("no_accounts" in line for line in lines)

    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        assert len(limits) == 1
        assert limits[0].current_value == 0  # reservation released


@pytest.mark.asyncio
async def test_compact_unexpected_exception_releases_reservation(async_client, monkeypatch):
    """compact에서 ProxyResponseError 외 일반 예외 발생 시 reservation이 release된다."""
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
            "name": "compact-unexpected-error",
            "limits": [
                {"limitType": "total_tokens", "limitWindow": "weekly", "maxValue": 100},
            ],
        },
    )
    assert created.status_code == 200
    payload = created.json()
    key = payload["key"]
    key_id = payload["id"]

    await _import_account(async_client, "acc_compact_unexpected", "compact-unexpected@example.com")

    async def raising_compact(_payload, _headers, _access_token, _account_id):
        raise RuntimeError("unexpected internal error")

    monkeypatch.setattr(proxy_module, "core_compact_responses", raising_compact)

    # ASGITransport propagates unhandled exceptions directly in tests
    with pytest.raises(RuntimeError, match="unexpected internal error"):
        await async_client.post(
            "/v1/responses/compact",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": _TEST_MODELS[0], "instructions": "hi", "input": []},
        )

    # Despite the unhandled exception, the finally block should have released the reservation
    async with SessionLocal() as session:
        repo = ApiKeysRepository(session)
        limits = await repo.get_limits_by_key(key_id)
        assert len(limits) == 1
        assert limits[0].current_value == 0  # reservation released despite unexpected error


@pytest.mark.asyncio
async def test_stream_without_api_key_auth_skips_settlement(async_client, monkeypatch):
    """API key auth 비활성 시 정산 로직이 스킵되고 에러 없이 동작한다."""
    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": False,
        },
    )
    assert enable.status_code == 200

    await _import_account(async_client, "acc_no_auth", "no-auth@example.com")

    async def fake_stream(_payload, _headers, _access_token, _account_id, base_url=None, raise_for_status=False):
        usage = {"input_tokens": 10, "output_tokens": 5}
        event = {"type": "response.completed", "response": {"id": "resp_no_auth", "usage": usage}}
        yield f"data: {json.dumps(event)}\n\n"

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json={"model": _TEST_MODELS[0], "instructions": "hi", "input": [], "stream": True},
    ) as response:
        assert response.status_code == 200
        lines = [line async for line in response.aiter_lines() if line]
        assert len(lines) >= 1  # stream completed without error

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from datetime import timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.responses import JSONResponse
from starlette.testclient import WebSocketDenialResponse

import app.modules.accounts.service as accounts_service_module
import app.modules.proxy.api as proxy_api_module
import app.modules.proxy.provider_adapters as provider_adapters_module
import app.modules.proxy.service as proxy_service_module
import app.modules.upstream_identities.repository as platform_repository_module
from app.core.clients.openai_platform import (
    OpenAIPlatformError,
    PlatformModelsResponse,
    PlatformResponseResult,
    PlatformStreamResponse,
)
from app.core.crypto import TokenEncryptor
from app.core.openai.model_registry import ReasoningLevel, UpstreamModel, get_model_registry
from app.core.openai.models import OpenAIResponsePayload
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, OpenAIPlatformIdentity, RequestLog
from app.db.session import SessionLocal
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration


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
        base_instructions="",
        raw={},
    )


async def _populate_platform_model_registry() -> None:
    registry = get_model_registry()
    models = [_make_upstream_model("gpt-5.1"), _make_upstream_model("gpt-5.1-codex")]
    await registry.update({"plus": models, "pro": models})


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


@pytest.fixture(autouse=True)
def _disable_http_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(
        http_responses_session_bridge_enabled=False,
        prefer_earlier_reset_accounts=False,
        sticky_reallocation_budget_threshold_pct=95.0,
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=300,
        openai_prompt_cache_key_derivation_enabled=True,
        routing_strategy="usage_weighted",
        proxy_request_budget_seconds=75.0,
        compact_request_budget_seconds=75.0,
        transcription_request_budget_seconds=120.0,
        upstream_compact_timeout_seconds=None,
        upstream_stream_transport="auto",
        log_proxy_request_payload=False,
        log_proxy_request_shape=False,
        log_proxy_request_shape_raw_cache_key=False,
        log_proxy_service_tier_trace=False,
        stream_idle_timeout_seconds=300.0,
        drain_primary_threshold_pct=85.0,
        drain_secondary_threshold_pct=90.0,
    )

    class _SettingsCache:
        async def get(self):
            return settings

    monkeypatch.setattr(proxy_service_module, "get_settings_cache", lambda: _SettingsCache())
    monkeypatch.setattr(proxy_service_module, "get_settings", lambda: settings)


def _assert_platform_text_input(payload: dict[str, object], expected_text: str) -> None:
    input_value = payload.get("input")
    if input_value == expected_text:
        return
    assert input_value == [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": expected_text,
                }
            ],
        }
    ]


async def _import_account(async_client, account_id: str, email: str) -> str:
    files = {"auth_json": ("auth.json", json.dumps(_make_auth_json(account_id, email)), "application/json")}
    response = await async_client.post("/api/accounts/import", files=files)
    assert response.status_code == 200
    return response.json()["accountId"]


async def _seed_usage(account_id: str, *, window: str, used_percent: float, reset_after_seconds: int) -> None:
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    async with SessionLocal() as session:
        result = await session.execute(
            select(Account.id).where((Account.id == account_id) | (Account.chatgpt_account_id == account_id)).limit(1)
        )
        resolved_account_id = result.scalar_one_or_none()
        assert resolved_account_id is not None
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=resolved_account_id,
            used_percent=used_percent,
            window=window,
            reset_at=now_epoch + reset_after_seconds,
            window_minutes=300 if window == "primary" else 10080,
        )


async def _seed_primary_usage(account_id: str, used_percent: float) -> None:
    await _seed_usage(account_id, window="primary", used_percent=used_percent, reset_after_seconds=3600)


async def _seed_secondary_usage(account_id: str, used_percent: float) -> None:
    await _seed_usage(account_id, window="secondary", used_percent=used_percent, reset_after_seconds=86400)


async def _create_platform_identity(async_client, monkeypatch, *, route_families: list[str] | None = None) -> str:
    async def fake_validate_platform_identity(self, *, api_key, organization=None, project=None):
        del self, api_key, organization, project
        return PlatformModelsResponse(
            payload={
                "object": "list",
                "data": [{"id": "gpt-5.1", "object": "model", "owned_by": "openai"}],
            },
            upstream_request_id="up_req_models_validate",
        )

    monkeypatch.setattr(
        accounts_service_module.OpenAIPlatformProviderAdapter,
        "validate_identity",
        fake_validate_platform_identity,
    )
    response = await async_client.post(
        "/api/accounts/platform",
        json={
            "label": "Platform Key",
            "apiKey": "sk-platform-test",
            "organization": "org_test",
            "project": "proj_test",
            "eligibleRouteFamilies": route_families or ["public_responses_http", "public_models_http"],
        },
    )
    assert response.status_code == 200
    return response.json()["accountId"]


async def _insert_platform_identity_direct(route_families: list[str] | None = None) -> str:
    identity = OpenAIPlatformIdentity(
        id="plat_direct",
        label="Platform Key",
        api_key_encrypted=TokenEncryptor().encrypt("sk-platform-test"),
        organization_id="org_test",
        project_id="proj_test",
        eligible_route_families=",".join(route_families or ["public_responses_http", "public_models_http"]),
        status=AccountStatus.ACTIVE,
        last_validated_at=None,
        last_auth_failure_reason=None,
        deactivation_reason=None,
    )
    async with SessionLocal() as session:
        session.add(identity)
        await session.commit()
    return identity.id


def _platform_identity_payload(route_families: list[str] | None = None) -> dict[str, object]:
    return {
        "label": "Platform Key",
        "apiKey": "sk-platform-test",
        "organization": "org_test",
        "project": "proj_test",
        "eligibleRouteFamilies": route_families or ["public_responses_http", "public_models_http"],
    }


async def _stream_lines(lines: list[str]) -> AsyncIterator[str]:
    for line in lines:
        yield line


async def _latest_request_log() -> RequestLog | None:
    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog).order_by(RequestLog.requested_at.desc()))
        return result.scalars().first()


@pytest.mark.asyncio
async def test_create_and_list_platform_identity(async_client, monkeypatch):
    await _import_account(async_client, "acc_platform_list", "platform-list@example.com")
    identity_id = await _create_platform_identity(async_client, monkeypatch)

    response = await async_client.get("/api/accounts")
    assert response.status_code == 200
    accounts = response.json()["accounts"]
    platform_identity = next(item for item in accounts if item["accountId"] == identity_id)

    assert platform_identity["providerKind"] == "openai_platform"
    assert platform_identity["routingSubjectId"] == identity_id
    assert platform_identity["label"] == "Platform Key"
    assert sorted(platform_identity["eligibleRouteFamilies"]) == [
        "public_models_http",
        "public_responses_http",
    ]
    assert platform_identity["organization"] == "org_test"
    assert platform_identity["project"] == "proj_test"


@pytest.mark.asyncio
async def test_create_platform_identity_requires_existing_chatgpt_account(async_client, monkeypatch):
    async def fake_validate_platform_identity(self, *, api_key, organization=None, project=None):
        del self, api_key, organization, project
        return PlatformModelsResponse(
            payload={"object": "list", "data": [{"id": "gpt-5.1", "object": "model", "owned_by": "openai"}]},
            upstream_request_id="up_req_models_validate",
        )

    monkeypatch.setattr(
        accounts_service_module.OpenAIPlatformProviderAdapter,
        "validate_identity",
        fake_validate_platform_identity,
    )

    response = await async_client.post("/api/accounts/platform", json=_platform_identity_payload())
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "platform_identity_prerequisite_failed"


@pytest.mark.asyncio
async def test_create_platform_identity_rejects_second_platform_key(async_client, monkeypatch):
    await _import_account(async_client, "acc_platform_dupe", "platform-dupe@example.com")
    await _create_platform_identity(async_client, monkeypatch)

    response = await async_client.post(
        "/api/accounts/platform",
        json={
            **_platform_identity_payload(),
            "label": "Second Platform Key",
            "apiKey": "sk-platform-test-2",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "platform_identity_conflict"


@pytest.mark.asyncio
async def test_create_platform_identity_translates_integrity_conflict(async_client, monkeypatch):
    await _import_account(async_client, "acc_platform_race", "platform-race@example.com")

    async def fake_validate_platform_identity(self, *, api_key, organization=None, project=None):
        del self, api_key, organization, project
        return PlatformModelsResponse(
            payload={"object": "list", "data": [{"id": "gpt-5.1", "object": "model", "owned_by": "openai"}]},
            upstream_request_id="up_req_models_validate",
        )

    async def raise_integrity_error(self, create):
        del self, create
        raise IntegrityError("INSERT INTO openai_platform_identities", {}, Exception("duplicate singleton"))

    monkeypatch.setattr(
        accounts_service_module.OpenAIPlatformProviderAdapter,
        "validate_identity",
        fake_validate_platform_identity,
    )
    monkeypatch.setattr(
        platform_repository_module.OpenAIPlatformIdentitiesRepository,
        "create_identity",
        raise_integrity_error,
    )

    response = await async_client.post("/api/accounts/platform", json=_platform_identity_payload())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "platform_identity_conflict"


@pytest.mark.asyncio
async def test_v1_models_keeps_chatgpt_primary_when_usage_is_healthy(async_client, monkeypatch):
    account_id = await _import_account(async_client, "acc_models_primary", "models-primary@example.com")
    await _seed_primary_usage(account_id, 10.0)
    await _create_platform_identity(async_client, monkeypatch, route_families=["public_models_http"])

    async def fail_fetch_platform_models(*, base_url, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        raise AssertionError("healthy ChatGPT pool must stay primary")

    monkeypatch.setattr(provider_adapters_module, "fetch_platform_models", fail_fetch_platform_models)

    response = await async_client.get("/v1/models")
    assert response.status_code == 200
    assert response.json()["object"] == "list"


@pytest.mark.asyncio
async def test_v1_models_falls_back_to_platform_when_primary_usage_is_depleted(async_client, monkeypatch):
    await _populate_platform_model_registry()
    account_id = await _import_account(async_client, "acc_models_fallback", "models-fallback@example.com")
    await _seed_primary_usage(account_id, 95.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch, route_families=["public_models_http"])

    async def fake_fetch_platform_models(*, base_url, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        return PlatformModelsResponse(
            payload={
                "object": "list",
                "data": [
                    {"id": "gpt-5.1", "object": "model", "owned_by": "openai"},
                    {"id": "gpt-5.1-codex", "object": "model", "owned_by": "openai"},
                ],
            },
            upstream_request_id="up_req_models_1",
        )

    monkeypatch.setattr(provider_adapters_module, "fetch_platform_models", fake_fetch_platform_models)

    response = await async_client.get("/v1/models")
    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "list"
    assert [item["id"] for item in payload["data"]] == ["gpt-5.1", "gpt-5.1-codex"]

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "openai_platform"
    assert log.routing_subject_id == identity_id
    assert log.route_class == "openai_public_http"
    assert log.upstream_request_id == "up_req_models_1"
    assert log.status == "success"


@pytest.mark.asyncio
async def test_v1_models_falls_back_to_platform_when_secondary_usage_is_depleted(async_client, monkeypatch):
    await _populate_platform_model_registry()
    account_id = await _import_account(async_client, "acc_models_secondary_fallback", "models-secondary@example.com")
    await _seed_primary_usage(account_id, 10.0)
    await _seed_secondary_usage(account_id, 95.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch, route_families=["public_models_http"])

    async def fake_fetch_platform_models(*, base_url, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        return PlatformModelsResponse(
            payload={
                "object": "list",
                "data": [
                    {"id": "gpt-5.1", "object": "model", "owned_by": "openai"},
                ],
            },
            upstream_request_id="up_req_models_secondary_1",
        )

    monkeypatch.setattr(provider_adapters_module, "fetch_platform_models", fake_fetch_platform_models)

    response = await async_client.get("/v1/models")
    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "list"
    assert [item["id"] for item in payload["data"]] == ["gpt-5.1"]

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "openai_platform"
    assert log.routing_subject_id == identity_id
    assert log.upstream_request_id == "up_req_models_secondary_1"
    assert log.status == "success"


@pytest.mark.asyncio
async def test_v1_models_keeps_chatgpt_primary_when_any_candidate_remains_healthy(async_client, monkeypatch):
    await _populate_platform_model_registry()
    drained_account_id = await _import_account(async_client, "acc_models_drained", "models-drained@example.com")
    healthy_account_id = await _import_account(async_client, "acc_models_healthy", "models-healthy@example.com")
    await _seed_primary_usage(drained_account_id, 95.0)
    await _seed_secondary_usage(drained_account_id, 95.0)
    await _seed_primary_usage(healthy_account_id, 10.0)
    await _seed_secondary_usage(healthy_account_id, 10.0)
    await _create_platform_identity(async_client, monkeypatch, route_families=["public_models_http"])

    async def fail_fetch_platform_models(*, base_url, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        raise AssertionError("healthy ChatGPT candidates must keep the platform fallback idle")

    monkeypatch.setattr(provider_adapters_module, "fetch_platform_models", fail_fetch_platform_models)

    response = await async_client.get("/v1/models")
    assert response.status_code == 200
    assert response.json()["object"] == "list"


@pytest.mark.asyncio
async def test_v1_responses_keeps_chatgpt_primary_when_usage_is_healthy(async_client, monkeypatch):
    raw_account_id = "acc_resp_primary"
    expected_account_id = await _import_account(async_client, raw_account_id, "resp-primary@example.com")
    await _seed_primary_usage(expected_account_id, 10.0)
    await _create_platform_identity(async_client, monkeypatch, route_families=["public_responses_http"])

    async def fail_create_platform_response(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, payload, api_key, organization, project
        raise AssertionError("healthy ChatGPT pool must stay primary")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        del payload, headers, access_token, base_url, raise_for_status, _kw
        assert account_id == raw_account_id
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_chatgpt_primary","status":"completed",'
            '"usage":{"input_tokens":2,"output_tokens":3,"total_tokens":5}}}\n\n'
        )

    monkeypatch.setattr(provider_adapters_module, "create_platform_response", fail_create_platform_response)
    monkeypatch.setattr(provider_adapters_module, "core_stream_responses", fake_stream)

    response = await async_client.post("/v1/responses", json={"model": "gpt-5.1", "input": "hi"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "resp_chatgpt_primary"
    assert payload["status"] == "completed"

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "chatgpt_web"
    assert log.account_id == expected_account_id


@pytest.mark.asyncio
async def test_v1_responses_routes_using_enforced_api_key_model(async_client, monkeypatch):
    raw_account_id = "acc_resp_enforced_model"
    expected_account_id = await _import_account(async_client, raw_account_id, "resp-enforced@example.com")
    await _seed_primary_usage(expected_account_id, 10.0)
    await _create_platform_identity(async_client, monkeypatch, route_families=["public_responses_http"])

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
            "name": "responses-enforced-model",
            "allowedModels": ["gpt-5.1"],
            "enforcedModel": "gpt-5.1",
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]

    async def fail_create_platform_response(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, payload, api_key, organization, project
        raise AssertionError("enforced ChatGPT model should prevent platform fallback or rejection")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        del headers, access_token, base_url, raise_for_status, _kw
        assert payload.model == "gpt-5.1"
        assert account_id == raw_account_id
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_chatgpt_enforced","status":"completed",'
            '"usage":{"input_tokens":2,"output_tokens":3,"total_tokens":5}}}\n\n'
        )

    monkeypatch.setattr(provider_adapters_module, "create_platform_response", fail_create_platform_response)
    monkeypatch.setattr(provider_adapters_module, "core_stream_responses", fake_stream)

    response = await async_client.post(
        "/v1/responses",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "platform-only-model", "input": "hi"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "resp_chatgpt_enforced"
    assert payload["status"] == "completed"

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "chatgpt_web"
    assert log.account_id == expected_account_id


@pytest.mark.asyncio
async def test_v1_responses_falls_back_to_platform_for_stateless_requests(async_client, monkeypatch):
    account_id = await _import_account(async_client, "acc_resp_fallback", "resp-fallback@example.com")
    await _seed_primary_usage(account_id, 95.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch, route_families=["public_responses_http"])

    async def fake_create_platform_response(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        assert payload["model"] == "gpt-5.1"
        _assert_platform_text_input(payload, "hi")
        return PlatformResponseResult(
            payload=OpenAIResponsePayload.model_validate(
                {
                    "id": "resp_platform_1",
                    "status": "completed",
                    "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                }
            ),
            upstream_request_id="up_req_resp_1",
        )

    monkeypatch.setattr(provider_adapters_module, "create_platform_response", fake_create_platform_response)

    response = await async_client.post("/v1/responses", json={"model": "gpt-5.1", "input": "hi"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "resp_platform_1"
    assert payload["status"] == "completed"

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "openai_platform"
    assert log.routing_subject_id == identity_id
    assert log.route_class == "openai_public_http"
    assert log.upstream_request_id == "up_req_resp_1"


@pytest.mark.asyncio
async def test_v1_responses_keeps_chatgpt_primary_when_any_account_in_pool_is_healthy(async_client, monkeypatch):
    unhealthy_account_id = await _import_account(async_client, "acc_resp_unhealthy", "resp-unhealthy@example.com")
    healthy_account_id = await _import_account(async_client, "acc_resp_healthy", "resp-healthy@example.com")
    await _seed_primary_usage(unhealthy_account_id, 95.0)
    await _seed_primary_usage(healthy_account_id, 10.0)
    await _create_platform_identity(async_client, monkeypatch, route_families=["public_responses_http"])

    async def fail_create_platform_response(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, payload, api_key, organization, project
        raise AssertionError("a healthy ChatGPT account in the pool must keep platform fallback disabled")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        del payload, headers, access_token, base_url, raise_for_status, _kw
        assert account_id == "acc_resp_healthy"
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_chatgpt_pool_healthy","status":"completed",'
            '"usage":{"input_tokens":2,"output_tokens":3,"total_tokens":5}}}\n\n'
        )

    monkeypatch.setattr(provider_adapters_module, "create_platform_response", fail_create_platform_response)
    monkeypatch.setattr(provider_adapters_module, "core_stream_responses", fake_stream)

    response = await async_client.post("/v1/responses", json={"model": "gpt-5.1", "input": "hi"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "resp_chatgpt_pool_healthy"
    assert payload["status"] == "completed"

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "chatgpt_web"
    assert log.account_id == healthy_account_id


@pytest.mark.asyncio
async def test_v1_responses_falls_back_to_platform_when_secondary_usage_is_depleted(async_client, monkeypatch):
    account_id = await _import_account(async_client, "acc_resp_secondary_fallback", "resp-secondary@example.com")
    await _seed_primary_usage(account_id, 10.0)
    await _seed_secondary_usage(account_id, 95.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch, route_families=["public_responses_http"])

    async def fake_create_platform_response(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        assert payload["model"] == "gpt-5.1"
        _assert_platform_text_input(payload, "hi")
        return PlatformResponseResult(
            payload=OpenAIResponsePayload.model_validate(
                {
                    "id": "resp_platform_secondary",
                    "status": "completed",
                    "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                }
            ),
            upstream_request_id="up_req_resp_secondary_1",
        )

    monkeypatch.setattr(provider_adapters_module, "create_platform_response", fake_create_platform_response)

    response = await async_client.post("/v1/responses", json={"model": "gpt-5.1", "input": "hi"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "resp_platform_secondary"
    assert payload["status"] == "completed"

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "openai_platform"
    assert log.routing_subject_id == identity_id
    assert log.route_class == "openai_public_http"
    assert log.upstream_request_id == "up_req_resp_secondary_1"


@pytest.mark.asyncio
async def test_updating_platform_route_families_enables_public_responses_fallback(async_client, monkeypatch):
    account_id = await _import_account(async_client, "acc_resp_route_edit", "resp-route-edit@example.com")
    await _seed_primary_usage(account_id, 95.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch, route_families=["public_models_http"])

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        del payload, headers, access_token, base_url, raise_for_status, _kw
        assert account_id == "acc_resp_route_edit"
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_chatgpt_before_route_edit","status":"completed",'
            '"usage":{"input_tokens":2,"output_tokens":3,"total_tokens":5}}}\n\n'
        )

    async def fail_create_platform_response(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, payload, api_key, organization, project
        raise AssertionError("platform fallback must stay disabled until public_responses_http is enabled")

    monkeypatch.setattr(provider_adapters_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(provider_adapters_module, "create_platform_response", fail_create_platform_response)

    initial_response = await async_client.post("/v1/responses", json={"model": "gpt-5.1", "input": "hi"})
    assert initial_response.status_code == 200
    assert initial_response.json()["id"] == "resp_chatgpt_before_route_edit"

    update_response = await async_client.patch(
        f"/api/accounts/platform/{identity_id}",
        json={"eligibleRouteFamilies": ["public_models_http", "public_responses_http"]},
    )
    assert update_response.status_code == 200
    assert sorted(update_response.json()["eligibleRouteFamilies"]) == [
        "public_models_http",
        "public_responses_http",
    ]

    async def fake_create_platform_response(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        assert payload["model"] == "gpt-5.1"
        _assert_platform_text_input(payload, "hi")
        return PlatformResponseResult(
            payload=OpenAIResponsePayload.model_validate(
                {
                    "id": "resp_platform_after_route_edit",
                    "status": "completed",
                    "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                }
            ),
            upstream_request_id="up_req_resp_route_edit_1",
        )

    async def fail_stream_after_route_edit(
        payload,
        headers,
        access_token,
        account_id,
        base_url=None,
        raise_for_status=False,
        **_kw,
    ):
        del payload, headers, access_token, account_id, base_url, raise_for_status, _kw
        raise AssertionError("platform fallback should take over after enabling public_responses_http")

    monkeypatch.setattr(provider_adapters_module, "create_platform_response", fake_create_platform_response)
    monkeypatch.setattr(provider_adapters_module, "core_stream_responses", fail_stream_after_route_edit)

    fallback_response = await async_client.post("/v1/responses", json={"model": "gpt-5.1", "input": "hi"})
    assert fallback_response.status_code == 200
    assert fallback_response.json()["id"] == "resp_platform_after_route_edit"


@pytest.mark.asyncio
async def test_v1_responses_stream_falls_back_to_platform_when_primary_usage_is_depleted(async_client, monkeypatch):
    account_id = await _import_account(async_client, "acc_resp_stream_fallback", "resp-stream-fallback@example.com")
    await _seed_primary_usage(account_id, 95.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch, route_families=["public_responses_http"])

    async def fake_stream_platform_responses(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        assert payload["model"] == "gpt-5.1"
        _assert_platform_text_input(payload, "hi")
        return PlatformStreamResponse(
            event_stream=_stream_lines(
                [
                    'data: {"type":"response.created","response":{"id":"resp_platform_stream"}}\n\n',
                    'data: {"type":"response.completed","response":{"id":"resp_platform_stream","status":"completed",'
                    '"usage":{"input_tokens":2,"output_tokens":3,"total_tokens":5}}}\n\n',
                ]
            ),
            upstream_request_id="up_req_resp_stream_1",
        )

    monkeypatch.setattr(provider_adapters_module, "stream_platform_responses", fake_stream_platform_responses)

    async with async_client.stream(
        "POST",
        "/v1/responses",
        json={"model": "gpt-5.1", "input": "hi", "stream": True},
    ) as response:
        assert response.status_code == 200
        lines = [line async for line in response.aiter_lines() if line]

    events = [json.loads(line[6:]) for line in lines if line.startswith("data: ")]
    event_types = [event.get("type") for event in events]
    assert event_types == ["response.created", "response.completed"]

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "openai_platform"
    assert log.routing_subject_id == identity_id
    assert log.route_class == "openai_public_http"
    assert log.upstream_request_id == "up_req_resp_stream_1"


@pytest.mark.asyncio
async def test_v1_responses_stream_returns_502_when_platform_stream_fails_before_first_event(async_client, monkeypatch):
    account_id = await _import_account(async_client, "acc_resp_stream_bootstrap", "resp-stream-bootstrap@example.com")
    await _seed_primary_usage(account_id, 95.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch, route_families=["public_responses_http"])

    async def failing_stream():
        raise RuntimeError("broken stream bootstrap")
        yield "unreachable"

    async def fake_stream_platform_responses(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, payload, api_key, organization, project
        return PlatformStreamResponse(
            event_stream=failing_stream(),
            upstream_request_id="up_req_resp_stream_bootstrap",
        )

    monkeypatch.setattr(provider_adapters_module, "stream_platform_responses", fake_stream_platform_responses)

    response = await async_client.post(
        "/v1/responses",
        json={"model": "gpt-5.1", "input": "hi", "stream": True},
    )
    assert response.status_code == 502
    payload = response.json()
    assert payload["error"]["code"] == "upstream_unavailable"

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "openai_platform"
    assert log.routing_subject_id == identity_id
    assert log.error_code == "upstream_unavailable"
    assert log.rejection_reason == "platform_stream_start_failed"


@pytest.mark.asyncio
async def test_v1_chat_completions_stays_on_chatgpt_even_when_platform_fallback_exists(async_client, monkeypatch):
    raw_account_id = "acc_chat_completion_primary"
    expected_account_id = await _import_account(async_client, raw_account_id, "chat-completion-primary@example.com")
    await _seed_primary_usage(expected_account_id, 95.0)
    await _seed_secondary_usage(expected_account_id, 95.0)
    await _create_platform_identity(async_client, monkeypatch, route_families=["public_responses_http"])

    async def fail_stream_platform_responses(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, payload, api_key, organization, project
        raise AssertionError("chat completions must stay on the ChatGPT path in phase 1")

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        del payload, headers, access_token, base_url, raise_for_status, _kw
        assert account_id == raw_account_id
        yield 'data: {"type":"response.output_text.delta","delta":"hi"}\n\n'
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_chat_completion_primary",'
            '"usage":{"input_tokens":2,"output_tokens":3,"total_tokens":5}}}\n\n'
        )

    monkeypatch.setattr(provider_adapters_module, "stream_platform_responses", fail_stream_platform_responses)
    monkeypatch.setattr(provider_adapters_module, "core_stream_responses", fake_stream)

    response = await async_client.post(
        "/v1/chat/completions",
        json={"model": "gpt-5.1", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "resp_chat_completion_primary"
    assert payload["object"] == "chat.completion"

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "chatgpt_web"
    assert log.account_id == expected_account_id


@pytest.mark.asyncio
async def test_backend_codex_models_stays_on_chatgpt_even_when_platform_fallback_exists(async_client, monkeypatch):
    await _import_account(async_client, "acc_backend_models_primary", "backend-models-primary@example.com")
    await _create_platform_identity(async_client, monkeypatch, route_families=["public_models_http"])

    async def fake_build_codex_models_response(_api_key):
        return JSONResponse(
            {
                "object": "list",
                "data": [{"id": "gpt-5.1-codex", "object": "model", "owned_by": "openai"}],
            }
        )

    monkeypatch.setattr(proxy_api_module, "_build_codex_models_response", fake_build_codex_models_response)

    response = await async_client.get("/backend-api/codex/models")
    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "gpt-5.1-codex"


@pytest.mark.asyncio
async def test_backend_codex_websocket_stays_on_chatgpt_even_when_platform_fallback_exists(
    async_client,
    app_instance,
    monkeypatch,
):
    await _import_account(async_client, "acc_backend_ws_primary", "backend-ws-primary@example.com")
    await _create_platform_identity(async_client, monkeypatch, route_families=["public_responses_http"])

    async def allow_firewall(_websocket):
        return None

    async def allow_proxy_auth(_authorization, request=None):
        del _authorization, request
        return None

    async def fake_proxy_responses_websocket(self, websocket, forwarded_headers, **kwargs):
        del self, forwarded_headers, kwargs
        await websocket.send_text(json.dumps({"type": "response.created"}))
        await websocket.close(code=1000)

    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_auth)
    monkeypatch.setattr(
        proxy_service_module.ProxyService,
        "proxy_responses_websocket",
        fake_proxy_responses_websocket,
    )

    with TestClient(app_instance) as client:
        with client.websocket_connect("/backend-api/codex/responses") as websocket:
            payload = json.loads(websocket.receive_text())

    assert payload["type"] == "response.created"


@pytest.mark.asyncio
async def test_v1_chat_completions_rejects_platform_only_operation(async_client, monkeypatch):
    await _insert_platform_identity_direct(route_families=["public_responses_http"])

    def fail_stream_responses(self, *args, **kwargs):
        del self, args, kwargs
        raise AssertionError("platform-only chat completions rejection must not start ChatGPT transport")

    monkeypatch.setattr(proxy_service_module.ProxyService, "stream_responses", fail_stream_responses)

    response = await async_client.post(
        "/v1/chat/completions",
        json={"model": "gpt-5.1", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "provider_feature_unsupported"


@pytest.mark.asyncio
async def test_v1_models_rejects_platform_only_public_operation(async_client, monkeypatch):
    await _insert_platform_identity_direct(route_families=["public_models_http"])

    async def fail_build_models_response(*args, **kwargs):
        del args, kwargs
        raise AssertionError("platform-only public-route rejection must not fall through to ChatGPT models")

    monkeypatch.setattr(proxy_api_module, "_build_models_response", fail_build_models_response)

    response = await async_client.get("/v1/models")
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "provider_fallback_requires_chatgpt"


@pytest.mark.asyncio
async def test_v1_models_platform_fallback_respects_api_key_allowed_models(async_client, monkeypatch):
    await _populate_platform_model_registry()
    account_id = await _import_account(async_client, "acc_models_restricted", "models-restricted@example.com")
    await _seed_primary_usage(account_id, 95.0)
    await _create_platform_identity(async_client, monkeypatch, route_families=["public_models_http"])

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
            "name": "platform-models-restricted",
            "allowedModels": ["gpt-5.1"],
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]

    async def fake_fetch_platform_models(*, base_url, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        return PlatformModelsResponse(
            payload={
                "object": "list",
                "data": [
                    {"id": "gpt-5.1", "object": "model", "owned_by": "openai"},
                    {"id": "gpt-5.1-codex", "object": "model", "owned_by": "openai"},
                ],
            },
            upstream_request_id="up_req_models_restricted",
        )

    monkeypatch.setattr(provider_adapters_module, "fetch_platform_models", fake_fetch_platform_models)

    response = await async_client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["data"]] == ["gpt-5.1"]


@pytest.mark.asyncio
async def test_v1_models_platform_auth_failure_updates_identity_state(async_client, monkeypatch):
    await _populate_platform_model_registry()
    account_id = await _import_account(async_client, "acc_models_auth_fail", "models-auth-fail@example.com")
    await _seed_primary_usage(account_id, 95.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch, route_families=["public_models_http"])

    async def fail_fetch_platform_models(*, base_url, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        raise OpenAIPlatformError(
            401,
            {
                "error": {
                    "code": "invalid_api_key",
                    "message": "Invalid API key",
                }
            },
        )

    monkeypatch.setattr(provider_adapters_module, "fetch_platform_models", fail_fetch_platform_models)

    response = await async_client.get("/v1/models")
    assert response.status_code == 401

    accounts_response = await async_client.get("/api/accounts")
    assert accounts_response.status_code == 200
    platform_account = next(
        account for account in accounts_response.json()["accounts"] if account["accountId"] == identity_id
    )
    assert platform_account["status"] == "deactivated"
    assert platform_account["lastAuthFailureReason"] == "Invalid API key"


@pytest.mark.asyncio
async def test_v1_responses_rejects_platform_only_public_operation(async_client, monkeypatch):
    await _insert_platform_identity_direct(route_families=["public_responses_http"])

    async def fail_collect_responses(*args, **kwargs):
        del args, kwargs
        raise AssertionError("platform-only public-route rejection must not fall through to ChatGPT responses")

    monkeypatch.setattr(proxy_api_module, "_collect_responses", fail_collect_responses)

    response = await async_client.post("/v1/responses", json={"model": "gpt-5.1", "input": "hi"})
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "provider_fallback_requires_chatgpt"


@pytest.mark.asyncio
async def test_v1_responses_rejects_previous_response_id_when_only_platform(async_client, monkeypatch):
    await _insert_platform_identity_direct(route_families=["public_responses_http"])

    async def fail_create_platform_response(self, *args, **kwargs):
        del self, args, kwargs
        raise AssertionError("continuity rejection must not start upstream transport")

    monkeypatch.setattr(proxy_service_module.ProxyService, "create_platform_response", fail_create_platform_response)

    response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "input": "hi",
            "previous_response_id": "resp_prev_1",
        },
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "provider_continuity_unsupported"
    assert payload["error"]["param"] == "previous_response_id"


@pytest.mark.asyncio
async def test_v1_responses_rejects_conversation_when_only_platform_before_upstream_transport(
    async_client,
    monkeypatch,
):
    await _insert_platform_identity_direct(route_families=["public_responses_http"])

    async def fail_create_platform_response(self, *args, **kwargs):
        del self, args, kwargs
        raise AssertionError("continuity rejection must not start upstream transport")

    monkeypatch.setattr(proxy_service_module.ProxyService, "create_platform_response", fail_create_platform_response)

    response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "input": "hi",
            "conversation": "conv_1",
        },
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "provider_continuity_unsupported"
    assert payload["error"]["param"] == "conversation"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header_name",
    ["session_id", "x-codex-session-id", "x-codex-conversation-id", "x-codex-turn-state"],
)
async def test_v1_responses_rejects_continuity_headers_when_only_platform_before_upstream_transport(
    async_client,
    monkeypatch,
    header_name: str,
):
    await _insert_platform_identity_direct(route_families=["public_responses_http"])

    async def fail_create_platform_response(self, *args, **kwargs):
        del self, args, kwargs
        raise AssertionError("continuity rejection must not start upstream transport")

    monkeypatch.setattr(proxy_service_module.ProxyService, "create_platform_response", fail_create_platform_response)

    response = await async_client.post(
        "/v1/responses",
        headers={header_name: "sid_1"},
        json={"model": "gpt-5.1", "input": "hi"},
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "provider_continuity_unsupported"
    assert payload["error"]["param"] == header_name


@pytest.mark.asyncio
async def test_platform_only_rejects_compact_and_backend_codex_routes(async_client, monkeypatch):
    await _insert_platform_identity_direct(route_families=["public_responses_http"])

    async def fail_compact_responses(self, *args, **kwargs):
        del self, args, kwargs
        raise AssertionError("rejected compact path must not start upstream transport")

    def fail_stream_http_responses(self, *args, **kwargs):
        del self, args, kwargs
        raise AssertionError("rejected backend codex path must not start upstream transport")

    monkeypatch.setattr(proxy_service_module.ProxyService, "compact_responses", fail_compact_responses)
    monkeypatch.setattr(proxy_service_module.ProxyService, "stream_http_responses", fail_stream_http_responses)

    compact_response = await async_client.post(
        "/v1/responses/compact",
        json={"model": "gpt-5.1", "input": "hi"},
    )
    assert compact_response.status_code == 400
    assert compact_response.json()["error"]["code"] == "provider_feature_unsupported"

    backend_response = await async_client.post(
        "/backend-api/codex/responses",
        json={"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True},
    )
    assert backend_response.status_code == 400
    assert backend_response.json()["error"]["code"] == "provider_feature_unsupported"


@pytest.mark.parametrize(
    ("path", "expected_code", "expected_param"),
    [
        ("/v1/responses", "provider_transport_unsupported", "transport"),
        ("/backend-api/codex/responses", "provider_feature_unsupported", None),
    ],
)
@pytest.mark.asyncio
async def test_platform_only_websocket_routes_reject_before_upstream_transport(
    async_client,
    app_instance,
    monkeypatch,
    path: str,
    expected_code: str,
    expected_param: str | None,
):
    async def allow_firewall(_websocket):
        return None

    async def allow_proxy_auth(_authorization, request=None):
        del _authorization, request
        return None

    async def fail_proxy_responses_websocket(self, *args, **kwargs):
        del self, args, kwargs
        raise AssertionError("rejected websocket path must not start upstream transport")

    await _insert_platform_identity_direct(route_families=["public_responses_http"])
    monkeypatch.setattr(proxy_api_module, "_websocket_firewall_denial_response", allow_firewall)
    monkeypatch.setattr(proxy_api_module, "validate_proxy_api_key_authorization", allow_proxy_auth)
    monkeypatch.setattr(
        proxy_service_module.ProxyService,
        "proxy_responses_websocket",
        fail_proxy_responses_websocket,
    )

    with TestClient(app_instance) as client:
        with pytest.raises(WebSocketDenialResponse) as excinfo:
            with client.websocket_connect(path):
                pass

    denial = excinfo.value
    assert denial.status_code == 400
    payload = denial.json()
    assert payload["error"]["code"] == expected_code
    if expected_param is None:
        assert "param" not in payload["error"]
    else:
        assert payload["error"]["param"] == expected_param


@pytest.mark.asyncio
async def test_backend_codex_models_rejects_platform_only_operation(async_client, monkeypatch):
    await _insert_platform_identity_direct(route_families=["public_models_http"])

    async def fail_build_codex_models_response(*args, **kwargs):
        del args, kwargs
        raise AssertionError("platform-only backend codex models rejection must not fall through to ChatGPT")

    monkeypatch.setattr(proxy_api_module, "_build_codex_models_response", fail_build_codex_models_response)

    response = await async_client.get("/backend-api/codex/models")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "provider_feature_unsupported"


@pytest.mark.asyncio
async def test_backend_codex_responses_stays_on_chatgpt_even_when_platform_fallback_exists(
    async_client,
    monkeypatch,
):
    await _import_account(async_client, "acc_backend_http_primary", "backend-http-primary@example.com")
    await _create_platform_identity(async_client, monkeypatch, route_families=["public_responses_http"])

    async def fail_stream_platform_responses(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, payload, api_key, organization, project
        raise AssertionError("backend codex responses must stay on the ChatGPT path in phase 1")

    async def _fake_backend_http_stream():
        yield 'data: {"type":"response.created"}\n\n'
        yield 'data: {"type":"response.completed","response":{"id":"resp_backend_http_primary"}}\n\n'

    def fake_stream_http_responses(self, payload, headers, **kwargs):
        del self, payload, headers, kwargs
        return _fake_backend_http_stream()

    monkeypatch.setattr(provider_adapters_module, "stream_platform_responses", fail_stream_platform_responses)
    monkeypatch.setattr(proxy_service_module.ProxyService, "stream_http_responses", fake_stream_http_responses)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json={"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True},
    ) as response:
        assert response.status_code == 200
        lines = [line async for line in response.aiter_lines() if line]

    assert lines == [
        'data: {"type":"response.created"}',
        'data: {"type":"response.completed","response":{"id":"resp_backend_http_primary"}}',
    ]

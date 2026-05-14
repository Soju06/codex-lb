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
from starlette.testclient import WebSocketDenialResponse

import app.modules.accounts.service as accounts_service_module
import app.modules.proxy.api as proxy_api_module
import app.modules.proxy.load_balancer as load_balancer_module
import app.modules.proxy.platform_cache_alerts as platform_cache_alerts_module
import app.modules.proxy.provider_adapters as provider_adapters_module
import app.modules.proxy.service as proxy_service_module
import app.modules.upstream_identities.repository as platform_repository_module
from app.core.clients.openai_platform import (
    OpenAIPlatformError,
    PlatformCompactResponseResult,
    PlatformModelsResponse,
    PlatformResponseResult,
    PlatformStreamResponse,
)
from app.core.crypto import TokenEncryptor
from app.core.openai.model_registry import ReasoningLevel, UpstreamModel, get_model_registry
from app.core.openai.models import CompactResponsePayload, OpenAIResponsePayload
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, OpenAIPlatformIdentity, RequestLog, StickySession, StickySessionKind
from app.db.session import SessionLocal
from app.modules.upstream_identities.types import PlatformRouteFamily
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration

EXPECTED_PLATFORM_ROUTE_FAMILY_TUPLE: tuple[PlatformRouteFamily, ...] = (
    "backend_codex_http",
    "public_models_http",
    "public_responses_http",
)
EXPECTED_PLATFORM_ROUTE_FAMILIES = list(EXPECTED_PLATFORM_ROUTE_FAMILY_TUPLE)


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
        http_responses_session_bridge_idle_ttl_seconds=120.0,
        http_responses_session_bridge_codex_idle_ttl_seconds=900.0,
        http_responses_session_bridge_codex_prewarm_enabled=False,
        http_responses_session_bridge_max_sessions=128,
        http_responses_session_bridge_queue_limit=8,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
        http_responses_session_bridge_gateway_safe_mode=False,
        http_responses_session_bridge_instance_id="test-instance",
        http_responses_session_bridge_instance_ring=[],
        http_responses_session_bridge_advertise_base_url=None,
        log_proxy_request_payload=False,
        log_proxy_request_shape=False,
        log_proxy_request_shape_raw_cache_key=False,
        log_proxy_service_tier_trace=False,
        stream_idle_timeout_seconds=300.0,
        proxy_downstream_websocket_idle_timeout_seconds=120.0,
        proxy_token_refresh_limit=32,
        proxy_upstream_websocket_connect_limit=64,
        proxy_response_create_limit=64,
        proxy_compact_response_create_limit=16,
        proxy_admission_wait_timeout_seconds=10.0,
        drain_primary_threshold_pct=85.0,
        drain_secondary_threshold_pct=90.0,
        platform_fallback_force_enabled=False,
        platform_cache_alert_proxy_url=None,
        platform_cache_alert_window_size=7,
        platform_cache_alert_threshold=4,
        platform_cache_alert_timeout_seconds=2.0,
        platform_cache_alert_cooldown_seconds=300.0,
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


async def _set_account_status(
    account_id: str,
    *,
    status: AccountStatus,
    reset_after_seconds: int | None = None,
) -> None:
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    async with SessionLocal() as session:
        result = await session.execute(
            select(Account).where((Account.id == account_id) | (Account.chatgpt_account_id == account_id)).limit(1)
        )
        account = result.scalar_one()
        account.status = status
        account.reset_at = None if reset_after_seconds is None else now_epoch + reset_after_seconds
        await session.commit()


async def _create_platform_identity(async_client, monkeypatch, *, route_families: list[str] | None = None) -> str:
    del route_families

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
        },
    )
    assert response.status_code == 200
    return response.json()["accountId"]


async def _insert_platform_identity_direct(route_families: list[str] | None = None) -> str:
    del route_families
    identity = OpenAIPlatformIdentity(
        id="plat_direct",
        label="Platform Key",
        api_key_encrypted=TokenEncryptor().encrypt("sk-platform-test"),
        organization_id="org_test",
        project_id="proj_test",
        eligible_route_families=",".join(EXPECTED_PLATFORM_ROUTE_FAMILIES),
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
    del route_families
    return {
        "label": "Platform Key",
        "apiKey": "sk-platform-test",
        "organization": "org_test",
        "project": "proj_test",
    }


async def _stream_lines(lines: list[str]) -> AsyncIterator[str]:
    for line in lines:
        yield line


async def _latest_request_log() -> RequestLog | None:
    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog).order_by(RequestLog.requested_at.desc()))
        return result.scalars().first()


async def _request_log_count() -> int:
    async with SessionLocal() as session:
        result = await session.execute(select(RequestLog.id))
        return len(result.scalars().all())


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
    assert platform_identity["eligibleRouteFamilies"] == EXPECTED_PLATFORM_ROUTE_FAMILIES
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
    await _seed_secondary_usage(account_id, 10.0)
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
    await _seed_secondary_usage(expected_account_id, 10.0)
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
    await _seed_secondary_usage(expected_account_id, 10.0)
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
        assert payload["service_tier"] == "default"
        _assert_platform_text_input(payload, "hi")
        return PlatformResponseResult(
            payload=OpenAIResponsePayload.model_validate(
                {
                    "id": "resp_platform_1",
                    "status": "completed",
                    "service_tier": "default",
                    "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                }
            ),
            upstream_request_id="up_req_resp_1",
        )

    monkeypatch.setattr(provider_adapters_module, "create_platform_response", fake_create_platform_response)

    response = await async_client.post(
        "/v1/responses",
        json={"model": "gpt-5.1", "input": "hi", "service_tier": "fast"},
    )
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
    assert log.requested_service_tier == "priority"
    assert log.actual_service_tier == "default"
    assert log.service_tier == "default"


@pytest.mark.asyncio
async def test_v1_responses_platform_cache_miss_window_alerts_with_key_suffix(async_client, monkeypatch):
    account_id = await _import_account(async_client, "acc_resp_cache_alert", "resp-cache-alert@example.com")
    await _seed_primary_usage(account_id, 95.0)
    await _create_platform_identity(async_client, monkeypatch, route_families=["public_responses_http"])

    settings = SimpleNamespace(
        platform_cache_alert_proxy_url="http://alerts.local",
        platform_cache_alert_window_size=7,
        platform_cache_alert_threshold=4,
        platform_cache_alert_timeout_seconds=2.0,
        platform_cache_alert_cooldown_seconds=300.0,
    )
    monkeypatch.setattr(platform_cache_alerts_module, "get_settings", lambda: settings)

    posts: list[tuple[str, str, float]] = []

    async def sender(url: str, suffix: str, timeout: float) -> None:
        posts.append((url, suffix, timeout))

    alert_service = platform_cache_alerts_module.PlatformCacheAlertService(sender=sender)
    monkeypatch.setattr(proxy_service_module, "get_platform_cache_alert_service", lambda: alert_service)
    cached_tokens_by_call = [0, 2, 0, 2, 0, 2, 0]
    seen_payloads: list[dict[str, object]] = []

    async def fake_create_platform_response(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        seen_payloads.append(payload)
        cached_tokens = cached_tokens_by_call[len(seen_payloads) - 1]
        return PlatformResponseResult(
            payload=OpenAIResponsePayload.model_validate(
                {
                    "id": f"resp_platform_cache_alert_{len(seen_payloads)}",
                    "status": "completed",
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 1,
                        "total_tokens": 11,
                        "input_tokens_details": {"cached_tokens": cached_tokens},
                    },
                }
            ),
            upstream_request_id=f"up_req_resp_cache_alert_{len(seen_payloads)}",
        )

    monkeypatch.setattr(provider_adapters_module, "create_platform_response", fake_create_platform_response)

    for index in range(7):
        response = await async_client.post("/v1/responses", json={"model": "gpt-5.1", "input": f"hi {index}"})
        assert response.status_code == 200

    assert len(seen_payloads) == 7
    assert posts == [("http://alerts.local", "test", 2.0)]


@pytest.mark.asyncio
async def test_v1_responses_reuses_platform_prompt_cache_affinity_after_chatgpt_pool_recovers(
    async_client,
    monkeypatch,
):
    account_id = await _import_account(
        async_client,
        "acc_resp_platform_cache_reuse",
        "resp-platform-cache-reuse@example.com",
    )
    await _seed_primary_usage(account_id, 95.0)
    await _seed_secondary_usage(account_id, 95.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch, route_families=["public_responses_http"])
    calls: list[str] = []

    async def fake_create_platform_response(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        assert payload["model"] == "gpt-5.1"
        assert payload["prompt_cache_key"] == "cache-key-v1-response-reuse"
        calls.append(payload["prompt_cache_key"])
        return PlatformResponseResult(
            payload=OpenAIResponsePayload.model_validate(
                {
                    "id": f"resp_platform_reuse_{len(calls)}",
                    "status": "completed",
                    "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                }
            ),
            upstream_request_id=f"up_req_resp_platform_reuse_{len(calls)}",
        )

    async def fail_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        del payload, headers, access_token, account_id, base_url, raise_for_status, _kw
        raise AssertionError("fresh Platform prompt-cache affinity must win over recovered ChatGPT pool")
        yield ""

    monkeypatch.setattr(provider_adapters_module, "create_platform_response", fake_create_platform_response)
    monkeypatch.setattr(provider_adapters_module, "core_stream_responses", fail_stream)

    first = await async_client.post(
        "/v1/responses",
        json={"model": "gpt-5.1", "input": "hi", "prompt_cache_key": "cache-key-v1-response-reuse"},
    )
    assert first.status_code == 200
    assert first.json()["id"] == "resp_platform_reuse_1"

    await _seed_primary_usage(account_id, 10.0)
    await _seed_secondary_usage(account_id, 10.0)

    second = await async_client.post(
        "/v1/responses",
        json={"model": "gpt-5.1", "input": "follow up", "prompt_cache_key": "cache-key-v1-response-reuse"},
    )
    assert second.status_code == 200
    assert second.json()["id"] == "resp_platform_reuse_2"
    assert calls == ["cache-key-v1-response-reuse", "cache-key-v1-response-reuse"]

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "openai_platform"
    assert log.routing_subject_id == identity_id
    assert log.upstream_request_id == "up_req_resp_platform_reuse_2"


@pytest.mark.asyncio
async def test_v1_responses_platform_uses_default_tier_when_fast_alias_response_omits_echo(async_client, monkeypatch):
    account_id = await _import_account(async_client, "acc_resp_fast_default", "resp-fast-default@example.com")
    await _seed_primary_usage(account_id, 95.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch, route_families=["public_responses_http"])

    async def fake_create_platform_response(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        assert payload["model"] == "gpt-5.1"
        assert payload["service_tier"] == "default"
        _assert_platform_text_input(payload, "hi")
        return PlatformResponseResult(
            payload=OpenAIResponsePayload.model_validate(
                {
                    "id": "resp_platform_default_fallback",
                    "status": "completed",
                    "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                }
            ),
            upstream_request_id="up_req_resp_default_fallback",
        )

    monkeypatch.setattr(provider_adapters_module, "create_platform_response", fake_create_platform_response)

    response = await async_client.post(
        "/v1/responses",
        json={"model": "gpt-5.1", "input": "hi", "service_tier": "fast"},
    )
    assert response.status_code == 200
    assert response.json()["id"] == "resp_platform_default_fallback"

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "openai_platform"
    assert log.routing_subject_id == identity_id
    assert log.requested_service_tier == "priority"
    assert log.actual_service_tier is None
    assert log.service_tier == "default"


@pytest.mark.asyncio
async def test_v1_responses_keeps_chatgpt_primary_when_any_account_in_pool_is_healthy(async_client, monkeypatch):
    unhealthy_account_id = await _import_account(async_client, "acc_resp_unhealthy", "resp-unhealthy@example.com")
    healthy_account_id = await _import_account(async_client, "acc_resp_healthy", "resp-healthy@example.com")
    await _seed_primary_usage(unhealthy_account_id, 95.0)
    await _seed_secondary_usage(unhealthy_account_id, 95.0)
    await _seed_primary_usage(healthy_account_id, 10.0)
    await _seed_secondary_usage(healthy_account_id, 10.0)
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
async def test_v1_responses_falls_back_to_platform_when_other_candidate_has_partial_usage_snapshot(
    async_client,
    monkeypatch,
):
    drained_account_id = await _import_account(
        async_client,
        "acc_resp_partial_drained",
        "resp-partial-drained@example.com",
    )
    await _seed_primary_usage(drained_account_id, 95.0)
    await _seed_secondary_usage(drained_account_id, 95.0)
    await _import_account(async_client, "acc_resp_partial_known", "resp-partial-known@example.com")
    async with SessionLocal() as session:
        result = await session.execute(select(Account.id).where(Account.chatgpt_account_id == "acc_resp_partial_known"))
        partial_account_id = result.scalar_one()
    await _seed_primary_usage(partial_account_id, 10.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch, route_families=["public_responses_http"])

    async def fake_create_platform_response(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        assert payload["model"] == "gpt-5.1"
        _assert_platform_text_input(payload, "hi")
        return PlatformResponseResult(
            payload=OpenAIResponsePayload.model_validate(
                {
                    "id": "resp_platform_partial_usage",
                    "status": "completed",
                    "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                }
            ),
            upstream_request_id="up_req_resp_partial_usage",
        )

    async def fail_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        del payload, headers, access_token, account_id, base_url, raise_for_status, _kw
        raise AssertionError("partial-missing ChatGPT usage snapshots must not keep public fallback on ChatGPT")

    monkeypatch.setattr(provider_adapters_module, "create_platform_response", fake_create_platform_response)
    monkeypatch.setattr(provider_adapters_module, "core_stream_responses", fail_stream)

    response = await async_client.post("/v1/responses", json={"model": "gpt-5.1", "input": "hi"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "resp_platform_partial_usage"
    assert payload["status"] == "completed"

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "openai_platform"
    assert log.routing_subject_id == identity_id
    assert log.route_class == "openai_public_http"
    assert log.upstream_request_id == "up_req_resp_partial_usage"


@pytest.mark.asyncio
async def test_platform_identity_automatically_enables_public_responses_fallback(async_client, monkeypatch):
    account_id = await _import_account(async_client, "acc_resp_route_edit", "resp-route-edit@example.com")
    await _seed_primary_usage(account_id, 95.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch, route_families=["public_models_http"])

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
        raise AssertionError("platform fallback should take over immediately after identity registration")

    monkeypatch.setattr(provider_adapters_module, "create_platform_response", fake_create_platform_response)
    monkeypatch.setattr(provider_adapters_module, "core_stream_responses", fail_stream_after_route_edit)

    fallback_response = await async_client.post("/v1/responses", json={"model": "gpt-5.1", "input": "hi"})
    assert fallback_response.status_code == 200
    assert fallback_response.json()["id"] == "resp_platform_after_route_edit"

    detail_response = await async_client.get("/api/accounts")
    assert detail_response.status_code == 200
    platform_identity = next(
        account for account in detail_response.json()["accounts"] if account["accountId"] == identity_id
    )
    assert platform_identity["eligibleRouteFamilies"] == EXPECTED_PLATFORM_ROUTE_FAMILIES


@pytest.mark.asyncio
async def test_v1_responses_stream_falls_back_to_platform_when_primary_usage_is_depleted(async_client, monkeypatch):
    account_id = await _import_account(async_client, "acc_resp_stream_fallback", "resp-stream-fallback@example.com")
    await _seed_primary_usage(account_id, 95.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch, route_families=["public_responses_http"])

    async def fake_stream_platform_responses(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        assert payload["model"] == "gpt-5.1"
        assert payload["service_tier"] == "default"
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
        json={"model": "gpt-5.1", "input": "hi", "stream": True, "service_tier": "fast"},
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
    assert log.requested_service_tier == "priority"
    assert log.actual_service_tier is None
    assert log.service_tier == "default"


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
async def test_v1_responses_stream_returns_502_when_platform_stream_is_empty(async_client, monkeypatch):
    account_id = await _import_account(async_client, "acc_resp_stream_empty", "resp-stream-empty@example.com")
    await _seed_primary_usage(account_id, 95.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch, route_families=["public_responses_http"])

    async def fake_stream_platform_responses(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, payload, api_key, organization, project
        return PlatformStreamResponse(
            event_stream=_stream_lines([]),
            upstream_request_id="up_req_resp_stream_empty",
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
    assert log.rejection_reason == "platform_stream_start_failed"
    assert log.upstream_request_id == "up_req_resp_stream_empty"


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
async def test_backend_codex_models_uses_platform_when_backend_http_usage_is_drained(async_client, monkeypatch):
    await _populate_platform_model_registry()
    account_id = await _import_account(async_client, "acc_backend_models_primary", "backend-models-primary@example.com")
    await _seed_primary_usage(account_id, 95.0)
    await _seed_secondary_usage(account_id, 95.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch, route_families=["backend_codex_http"])

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
            upstream_request_id="up_req_backend_codex_models",
        )

    async def fail_build_codex_models_response(*args, **kwargs):
        del args, kwargs
        raise AssertionError("backend codex models should use the Platform fallback path when backend HTTP is enabled")

    monkeypatch.setattr(provider_adapters_module, "fetch_platform_models", fake_fetch_platform_models)
    monkeypatch.setattr(proxy_api_module, "_build_codex_models_response", fail_build_codex_models_response)

    response = await async_client.get("/backend-api/codex/models")
    assert response.status_code == 200
    payload = response.json()
    assert [entry["slug"] for entry in payload["models"]] == ["gpt-5.1", "gpt-5.1-codex"]

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "openai_platform"
    assert log.routing_subject_id == identity_id
    assert log.route_class == "chatgpt_private"


@pytest.mark.asyncio
async def test_backend_codex_websocket_stays_on_chatgpt_even_when_platform_fallback_exists(
    async_client,
    app_instance,
    monkeypatch,
):
    await _import_account(async_client, "acc_backend_ws_primary", "backend-ws-primary@example.com")
    await _create_platform_identity(async_client, monkeypatch, route_families=["backend_codex_http"])

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
async def test_platform_only_rejects_compact_and_backend_codex_unsupported_routes(async_client, monkeypatch):
    await _insert_platform_identity_direct(route_families=["public_responses_http", "backend_codex_http"])

    async def fail_compact_responses(self, *args, **kwargs):
        del self, args, kwargs
        raise AssertionError("platform-only compact path must fail before upstream transport")

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
    assert compact_response.json()["error"]["code"] == "provider_fallback_requires_chatgpt"

    backend_compact_response = await async_client.post(
        "/backend-api/codex/responses/compact",
        json={"model": "gpt-5.1", "instructions": "hi", "input": []},
    )
    assert backend_compact_response.status_code == 400
    assert backend_compact_response.json()["error"]["code"] == "provider_fallback_requires_chatgpt"

    backend_response = await async_client.post(
        "/backend-api/codex/responses",
        json={"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True},
    )
    assert backend_response.status_code == 400
    assert backend_response.json()["error"]["code"] == "provider_fallback_requires_chatgpt"


@pytest.mark.asyncio
async def test_v1_compact_falls_back_to_platform_when_primary_usage_is_depleted(async_client, monkeypatch):
    account_id = await _import_account(async_client, "acc_v1_compact_fallback", "v1-compact-fallback@example.com")
    await _seed_primary_usage(account_id, 95.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch)

    async def fake_create_platform_compact_response(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        assert payload["model"] == "gpt-5.1"
        assert payload["instructions"] == ""
        _assert_platform_text_input(payload, "hi")
        assert "store" not in payload
        return PlatformCompactResponseResult(
            payload=CompactResponsePayload.model_validate(
                {
                    "object": "response.compaction",
                    "status": "completed",
                    "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                    "output": [],
                }
            ),
            upstream_request_id="up_req_v1_compact_fallback",
        )

    async def fail_chatgpt_compact(_payload, _headers, _access_token, _account_id):
        raise AssertionError("platform compact fallback should bypass ChatGPT compact transport")

    monkeypatch.setattr(
        provider_adapters_module,
        "create_platform_compact_response",
        fake_create_platform_compact_response,
    )
    monkeypatch.setattr(provider_adapters_module, "core_compact_responses", fail_chatgpt_compact)

    response = await async_client.post("/v1/responses/compact", json={"model": "gpt-5.1", "input": "hi"})
    assert response.status_code == 200
    assert response.json()["object"] == "response.compaction"

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "openai_platform"
    assert log.routing_subject_id == identity_id
    assert log.route_class == "openai_public_http"
    assert log.upstream_request_id == "up_req_v1_compact_fallback"


@pytest.mark.asyncio
async def test_v1_compact_falls_back_to_platform_and_rebinds_prompt_cache_affinity(async_client, monkeypatch):
    account_id = await _import_account(
        async_client,
        "acc_v1_compact_prompt_cache_fallback",
        "v1-compact-prompt-cache@example.com",
    )
    await _seed_primary_usage(account_id, 95.0)
    await _seed_secondary_usage(account_id, 95.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch)

    async def fake_create_platform_compact_response(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        assert payload["model"] == "gpt-5.1"
        return PlatformCompactResponseResult(
            payload=CompactResponsePayload.model_validate(
                {
                    "object": "response.compaction",
                    "status": "completed",
                    "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                    "output": [],
                }
            ),
            upstream_request_id="up_req_v1_compact_prompt_cache",
        )

    async def fail_chatgpt_compact(_payload, _headers, _access_token, _account_id):
        raise AssertionError("prompt-cache fallback must bypass ChatGPT compact transport")

    monkeypatch.setattr(
        provider_adapters_module,
        "create_platform_compact_response",
        fake_create_platform_compact_response,
    )
    monkeypatch.setattr(provider_adapters_module, "core_compact_responses", fail_chatgpt_compact)

    response = await async_client.post(
        "/v1/responses/compact",
        json={"model": "gpt-5.1", "input": "hi", "prompt_cache_key": "cache-key-v1-compact"},
    )
    assert response.status_code == 200

    async with SessionLocal() as session:
        sticky = await session.scalar(
            select(StickySession).where(
                StickySession.key == "cache-key-v1-compact",
                StickySession.kind == StickySessionKind.PROMPT_CACHE,
            )
        )

    assert sticky is not None
    assert sticky.provider_kind == "openai_platform"
    assert sticky.routing_subject_id == identity_id


@pytest.mark.asyncio
async def test_v1_compact_reuses_platform_prompt_cache_affinity_after_chatgpt_pool_recovers(
    async_client,
    monkeypatch,
):
    account_id = await _import_account(
        async_client,
        "acc_v1_compact_prompt_cache_reuse",
        "v1-compact-prompt-cache-reuse@example.com",
    )
    await _seed_primary_usage(account_id, 95.0)
    await _seed_secondary_usage(account_id, 95.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch)
    calls: list[str] = []

    async def fake_create_platform_compact_response(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        assert payload["model"] == "gpt-5.1"
        assert payload["prompt_cache_key"] == "cache-key-v1-compact-reuse"
        calls.append(payload["prompt_cache_key"])
        return PlatformCompactResponseResult(
            payload=CompactResponsePayload.model_validate(
                {
                    "object": "response.compaction",
                    "status": "completed",
                    "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                    "output": [],
                }
            ),
            upstream_request_id=f"up_req_v1_compact_reuse_{len(calls)}",
        )

    async def fail_chatgpt_compact(_payload, _headers, _access_token, _account_id):
        raise AssertionError("fresh Platform prompt-cache affinity must bypass recovered ChatGPT compact transport")

    monkeypatch.setattr(
        provider_adapters_module,
        "create_platform_compact_response",
        fake_create_platform_compact_response,
    )
    monkeypatch.setattr(provider_adapters_module, "core_compact_responses", fail_chatgpt_compact)

    first = await async_client.post(
        "/v1/responses/compact",
        json={"model": "gpt-5.1", "input": "hi", "prompt_cache_key": "cache-key-v1-compact-reuse"},
    )
    assert first.status_code == 200

    await _seed_primary_usage(account_id, 10.0)
    await _seed_secondary_usage(account_id, 10.0)

    second = await async_client.post(
        "/v1/responses/compact",
        json={"model": "gpt-5.1", "input": "follow up", "prompt_cache_key": "cache-key-v1-compact-reuse"},
    )
    assert second.status_code == 200
    assert calls == ["cache-key-v1-compact-reuse", "cache-key-v1-compact-reuse"]

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "openai_platform"
    assert log.routing_subject_id == identity_id
    assert log.upstream_request_id == "up_req_v1_compact_reuse_2"


@pytest.mark.asyncio
async def test_v1_compact_keeps_chatgpt_primary_when_any_account_in_pool_is_healthy(async_client, monkeypatch):
    drained_account_id = await _import_account(
        async_client,
        "acc_v1_compact_drained",
        "v1-compact-drained@example.com",
    )
    healthy_account_id = await _import_account(
        async_client,
        "acc_v1_compact_healthy",
        "v1-compact-healthy@example.com",
    )
    await _seed_primary_usage(drained_account_id, 95.0)
    await _seed_secondary_usage(drained_account_id, 95.0)
    await _seed_primary_usage(healthy_account_id, 10.0)
    await _seed_secondary_usage(healthy_account_id, 10.0)
    await _create_platform_identity(async_client, monkeypatch)

    async def fail_create_platform_compact_response(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, payload, api_key, organization, project
        raise AssertionError("a healthy ChatGPT candidate must keep compact fallback on ChatGPT")

    async def fake_chatgpt_compact(payload, headers, access_token, account_id):
        del headers, access_token
        assert payload.model == "gpt-5.1"
        assert account_id == "acc_v1_compact_healthy"
        return CompactResponsePayload.model_validate(
            {
                "object": "response.compaction",
                "status": "completed",
                "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                "output": [],
            }
        )

    monkeypatch.setattr(
        provider_adapters_module,
        "create_platform_compact_response",
        fail_create_platform_compact_response,
    )
    monkeypatch.setattr(provider_adapters_module, "core_compact_responses", fake_chatgpt_compact)

    response = await async_client.post("/v1/responses/compact", json={"model": "gpt-5.1", "input": "hi"})
    assert response.status_code == 200
    assert response.json()["object"] == "response.compaction"

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "chatgpt_web"
    assert log.account_id == healthy_account_id
    assert log.route_class == "openai_public_http"


@pytest.mark.asyncio
async def test_v1_compact_falls_back_to_platform_when_secondary_usage_is_depleted(async_client, monkeypatch):
    account_id = await _import_account(
        async_client,
        "acc_v1_compact_secondary_fallback",
        "v1-compact-secondary@example.com",
    )
    await _seed_primary_usage(account_id, 10.0)
    await _seed_secondary_usage(account_id, 95.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch)

    async def fake_create_platform_compact_response(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        assert payload["model"] == "gpt-5.1"
        assert payload["instructions"] == ""
        _assert_platform_text_input(payload, "hi")
        return PlatformCompactResponseResult(
            payload=CompactResponsePayload.model_validate(
                {
                    "object": "response.compaction",
                    "status": "completed",
                    "usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
                    "output": [],
                }
            ),
            upstream_request_id="up_req_v1_compact_secondary_fallback",
        )

    async def fail_chatgpt_compact(_payload, _headers, _access_token, _account_id):
        raise AssertionError("secondary-drained compact fallback should bypass ChatGPT compact transport")

    monkeypatch.setattr(
        provider_adapters_module,
        "create_platform_compact_response",
        fake_create_platform_compact_response,
    )
    monkeypatch.setattr(provider_adapters_module, "core_compact_responses", fail_chatgpt_compact)

    response = await async_client.post("/v1/responses/compact", json={"model": "gpt-5.1", "input": "hi"})
    assert response.status_code == 200
    assert response.json()["object"] == "response.compaction"

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "openai_platform"
    assert log.routing_subject_id == identity_id
    assert log.route_class == "openai_public_http"
    assert log.upstream_request_id == "up_req_v1_compact_secondary_fallback"


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_status", [401, 403])
async def test_v1_compact_retries_with_next_platform_identity_after_auth_failure(
    async_client,
    monkeypatch,
    auth_status: int,
):
    account_id = await _import_account(
        async_client,
        "acc_v1_compact_platform_failover",
        "v1-compact-platform-failover@example.com",
    )
    await _seed_primary_usage(account_id, 95.0)

    first_identity_id = await _create_platform_identity(async_client, monkeypatch)
    second_identity_id = "plat_compact_failover_secondary"
    second_identity = OpenAIPlatformIdentity(
        id=second_identity_id,
        label="Platform Key Secondary",
        api_key_encrypted=TokenEncryptor().encrypt("sk-platform-secondary"),
        organization_id="org_test",
        project_id="proj_test",
        eligible_route_families=",".join(EXPECTED_PLATFORM_ROUTE_FAMILIES),
        status=AccountStatus.ACTIVE,
        last_validated_at=None,
        last_auth_failure_reason=None,
        deactivation_reason=None,
    )

    original_list_eligible_identities = (
        platform_repository_module.OpenAIPlatformIdentitiesRepository.list_eligible_identities
    )
    original_get_by_id = platform_repository_module.OpenAIPlatformIdentitiesRepository.get_by_id

    async def fake_list_eligible_identities(self, route_family: PlatformRouteFamily):
        identities = await original_list_eligible_identities(self, route_family)
        return [*identities, second_identity]

    async def fake_get_by_id(self, identity_id: str):
        if identity_id == second_identity_id:
            return second_identity
        return await original_get_by_id(self, identity_id)

    async def fake_create_platform_compact_response(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, organization, project
        assert payload["model"] == "gpt-5.1"
        if api_key == "sk-platform-test":
            raise OpenAIPlatformError(
                auth_status,
                {
                    "error": {
                        "code": "invalid_api_key",
                        "message": "Invalid API key",
                    }
                },
                upstream_request_id="up_req_v1_compact_failover_primary",
            )
        assert api_key == "sk-platform-secondary"
        return PlatformCompactResponseResult(
            payload=CompactResponsePayload.model_validate(
                {
                    "object": "response.compaction",
                    "status": "completed",
                    "usage": {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7},
                    "output": [],
                }
            ),
            upstream_request_id="up_req_v1_compact_failover_secondary",
        )

    async def fail_chatgpt_compact(_payload, _headers, _access_token, _account_id):
        raise AssertionError("platform compact failover should not retry ChatGPT after pool drain")

    monkeypatch.setattr(
        platform_repository_module.OpenAIPlatformIdentitiesRepository,
        "list_eligible_identities",
        fake_list_eligible_identities,
    )
    monkeypatch.setattr(
        platform_repository_module.OpenAIPlatformIdentitiesRepository,
        "get_by_id",
        fake_get_by_id,
    )
    monkeypatch.setattr(
        provider_adapters_module,
        "create_platform_compact_response",
        fake_create_platform_compact_response,
    )
    monkeypatch.setattr(provider_adapters_module, "core_compact_responses", fail_chatgpt_compact)

    response = await async_client.post("/v1/responses/compact", json={"model": "gpt-5.1", "input": "hi"})
    assert response.status_code == 200
    assert response.json()["object"] == "response.compaction"

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "openai_platform"
    assert log.routing_subject_id == second_identity_id
    assert log.route_class == "openai_public_http"
    assert log.upstream_request_id == "up_req_v1_compact_failover_secondary"

    async with SessionLocal() as session:
        first_identity = await session.get(OpenAIPlatformIdentity, first_identity_id)

    assert first_identity is not None
    assert first_identity.status == AccountStatus.DEACTIVATED
    assert first_identity.last_auth_failure_reason == "Invalid API key"


@pytest.mark.asyncio
async def test_backend_codex_compact_falls_back_to_platform_when_primary_usage_is_depleted(async_client, monkeypatch):
    account_id = await _import_account(
        async_client,
        "acc_backend_compact_fallback",
        "backend-compact-fallback@example.com",
    )
    await _seed_primary_usage(account_id, 95.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch)

    async def fake_create_platform_compact_response(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        assert payload["model"] == "gpt-5.1"
        assert payload["instructions"] == "compact"
        assert payload["input"] == []
        assert "store" not in payload
        return PlatformCompactResponseResult(
            payload=CompactResponsePayload.model_validate(
                {
                    "object": "response.compaction",
                    "status": "completed",
                    "usage": {"input_tokens": 4, "output_tokens": 5, "total_tokens": 9},
                    "output": [],
                }
            ),
            upstream_request_id="up_req_backend_compact_fallback",
        )

    async def fail_chatgpt_compact(_payload, _headers, _access_token, _account_id):
        raise AssertionError("platform compact fallback should bypass ChatGPT compact transport")

    monkeypatch.setattr(
        provider_adapters_module,
        "create_platform_compact_response",
        fake_create_platform_compact_response,
    )
    monkeypatch.setattr(provider_adapters_module, "core_compact_responses", fail_chatgpt_compact)

    response = await async_client.post(
        "/backend-api/codex/responses/compact",
        json={"model": "gpt-5.1", "instructions": "compact", "input": []},
    )
    assert response.status_code == 200
    assert response.json()["object"] == "response.compaction"

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "openai_platform"
    assert log.routing_subject_id == identity_id
    assert log.route_class == "chatgpt_private"
    assert log.upstream_request_id == "up_req_backend_compact_fallback"


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_status", [401, 403])
async def test_backend_codex_compact_retries_with_next_platform_identity_after_auth_failure(
    async_client,
    monkeypatch,
    auth_status: int,
):
    account_id = await _import_account(
        async_client,
        "acc_backend_compact_platform_failover",
        "backend-compact-platform-failover@example.com",
    )
    await _seed_primary_usage(account_id, 95.0)

    first_identity_id = await _create_platform_identity(async_client, monkeypatch)
    second_identity_id = "plat_backend_compact_failover_secondary"
    second_identity = OpenAIPlatformIdentity(
        id=second_identity_id,
        label="Platform Key Secondary",
        api_key_encrypted=TokenEncryptor().encrypt("sk-platform-secondary"),
        organization_id="org_test",
        project_id="proj_test",
        eligible_route_families=",".join(EXPECTED_PLATFORM_ROUTE_FAMILIES),
        status=AccountStatus.ACTIVE,
        last_validated_at=None,
        last_auth_failure_reason=None,
        deactivation_reason=None,
    )

    original_list_eligible_identities = (
        platform_repository_module.OpenAIPlatformIdentitiesRepository.list_eligible_identities
    )
    original_get_by_id = platform_repository_module.OpenAIPlatformIdentitiesRepository.get_by_id

    async def fake_list_eligible_identities(self, route_family: PlatformRouteFamily):
        identities = await original_list_eligible_identities(self, route_family)
        return [*identities, second_identity]

    async def fake_get_by_id(self, identity_id: str):
        if identity_id == second_identity_id:
            return second_identity
        return await original_get_by_id(self, identity_id)

    async def fake_create_platform_compact_response(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, organization, project
        assert payload["model"] == "gpt-5.1"
        assert payload["instructions"] == "compact"
        assert payload["input"] == []
        if api_key == "sk-platform-test":
            raise OpenAIPlatformError(
                auth_status,
                {
                    "error": {
                        "code": "invalid_api_key",
                        "message": "Invalid API key",
                    }
                },
                upstream_request_id="up_req_backend_compact_failover_primary",
            )
        assert api_key == "sk-platform-secondary"
        return PlatformCompactResponseResult(
            payload=CompactResponsePayload.model_validate(
                {
                    "object": "response.compaction",
                    "status": "completed",
                    "usage": {"input_tokens": 4, "output_tokens": 5, "total_tokens": 9},
                    "output": [],
                }
            ),
            upstream_request_id="up_req_backend_compact_failover_secondary",
        )

    async def fail_chatgpt_compact(_payload, _headers, _access_token, _account_id):
        raise AssertionError("platform compact failover should not retry ChatGPT after backend pool drain")

    monkeypatch.setattr(
        platform_repository_module.OpenAIPlatformIdentitiesRepository,
        "list_eligible_identities",
        fake_list_eligible_identities,
    )
    monkeypatch.setattr(
        platform_repository_module.OpenAIPlatformIdentitiesRepository,
        "get_by_id",
        fake_get_by_id,
    )
    monkeypatch.setattr(
        provider_adapters_module,
        "create_platform_compact_response",
        fake_create_platform_compact_response,
    )
    monkeypatch.setattr(provider_adapters_module, "core_compact_responses", fail_chatgpt_compact)

    response = await async_client.post(
        "/backend-api/codex/responses/compact",
        json={"model": "gpt-5.1", "instructions": "compact", "input": []},
    )
    assert response.status_code == 200
    assert response.json()["object"] == "response.compaction"

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "openai_platform"
    assert log.routing_subject_id == second_identity_id
    assert log.route_class == "chatgpt_private"
    assert log.upstream_request_id == "up_req_backend_compact_failover_secondary"

    async with SessionLocal() as session:
        first_identity = await session.get(OpenAIPlatformIdentity, first_identity_id)

    assert first_identity is not None
    assert first_identity.status == AccountStatus.DEACTIVATED
    assert first_identity.last_auth_failure_reason == "Invalid API key"


@pytest.mark.parametrize(
    "header_name",
    ["session_id", "x-codex-session-id", "x-codex-conversation-id"],
)
@pytest.mark.asyncio
async def test_backend_codex_compact_keeps_sticky_chatgpt_session_during_rate_limit_grace(
    async_client,
    monkeypatch,
    header_name: str,
):
    sticky_account_id = await _import_account(
        async_client,
        "acc_backend_compact_sticky_grace",
        "backend-compact-sticky-grace@example.com",
    )
    await _seed_primary_usage(sticky_account_id, 10.0)
    await _seed_secondary_usage(sticky_account_id, 10.0)
    await _set_account_status(sticky_account_id, status=AccountStatus.RATE_LIMITED, reset_after_seconds=5)
    drained_account_id = await _import_account(
        async_client,
        "acc_backend_compact_sticky_grace_drained",
        "backend-compact-sticky-grace-drained@example.com",
    )
    await _seed_primary_usage(drained_account_id, 95.0)
    await _seed_secondary_usage(drained_account_id, 95.0)
    await _create_platform_identity(async_client, monkeypatch)

    session_key = f"backend-compact-session-{header_name}"
    async with SessionLocal() as session:
        session.add(
            StickySession(
                key=session_key,
                kind=StickySessionKind.CODEX_SESSION,
                provider_kind="chatgpt_web",
                routing_subject_id=sticky_account_id,
                account_id=sticky_account_id,
            )
        )
        await session.commit()

    async def fail_create_platform_compact_response(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, payload, api_key, organization, project
        raise AssertionError("grace-eligible sticky ChatGPT session must keep backend compact on ChatGPT")

    async def fake_chatgpt_compact(payload, headers, access_token, account_id):
        del headers, access_token
        assert payload.model == "gpt-5.1"
        assert account_id == "acc_backend_compact_sticky_grace"
        return CompactResponsePayload.model_validate(
            {
                "object": "response.compaction",
                "status": "completed",
                "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                "output": [],
            }
        )

    monkeypatch.setattr(
        provider_adapters_module,
        "create_platform_compact_response",
        fail_create_platform_compact_response,
    )
    monkeypatch.setattr(provider_adapters_module, "core_compact_responses", fake_chatgpt_compact)

    response = await async_client.post(
        "/backend-api/codex/responses/compact",
        headers={header_name: session_key},
        json={"model": "gpt-5.1", "instructions": "compact", "input": []},
    )
    assert response.status_code == 200
    assert response.json()["object"] == "response.compaction"

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "chatgpt_web"
    assert log.account_id == sticky_account_id
    assert log.route_class == "chatgpt_private"


@pytest.mark.asyncio
async def test_backend_codex_compact_keeps_chatgpt_when_sticky_session_usage_drained(
    async_client,
    monkeypatch,
):
    raw_sticky_account_id = "acc_backend_compact_sticky_drained"
    sticky_account_id = await _import_account(
        async_client,
        raw_sticky_account_id,
        "backend-compact-sticky-drained@example.com",
    )
    await _seed_primary_usage(sticky_account_id, 96.0)
    await _seed_secondary_usage(sticky_account_id, 96.0)
    other_account_id = await _import_account(
        async_client,
        "acc_backend_compact_sticky_drained_other",
        "backend-compact-sticky-drained-other@example.com",
    )
    await _seed_primary_usage(other_account_id, 10.0)
    await _seed_secondary_usage(other_account_id, 10.0)
    await _create_platform_identity(async_client, monkeypatch)

    session_key = "backend-compact-session-sticky-drained"
    async with SessionLocal() as session:
        session.add(
            StickySession(
                key=session_key,
                kind=StickySessionKind.CODEX_SESSION,
                provider_kind="chatgpt_web",
                routing_subject_id=sticky_account_id,
                account_id=sticky_account_id,
            )
        )
        await session.commit()

    async def fail_create_platform_compact_response(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, payload, api_key, organization, project
        raise AssertionError("selectable sticky ChatGPT session must keep backend compact on ChatGPT")

    async def fake_chatgpt_compact(payload, headers, access_token, account_id):
        del headers, access_token
        assert payload.model == "gpt-5.1"
        assert account_id in {raw_sticky_account_id, "acc_backend_compact_sticky_drained_other"}
        return CompactResponsePayload.model_validate(
            {
                "object": "response.compaction",
                "status": "completed",
                "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                "output": [],
            }
        )

    monkeypatch.setattr(
        provider_adapters_module,
        "create_platform_compact_response",
        fail_create_platform_compact_response,
    )
    monkeypatch.setattr(provider_adapters_module, "core_compact_responses", fake_chatgpt_compact)

    response = await async_client.post(
        "/backend-api/codex/responses/compact",
        headers={"x-codex-session-id": session_key},
        json={"model": "gpt-5.1", "instructions": "compact", "input": []},
    )
    assert response.status_code == 200
    assert response.json()["object"] == "response.compaction"

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "chatgpt_web"
    assert log.route_class == "chatgpt_private"


@pytest.mark.asyncio
async def test_backend_codex_compact_keeps_chatgpt_when_any_account_in_pool_is_healthy(async_client, monkeypatch):
    drained_account_id = await _import_account(
        async_client,
        "acc_backend_compact_pool_drained",
        "backend-compact-pool-drained@example.com",
    )
    healthy_account_id = await _import_account(
        async_client,
        "acc_backend_compact_pool_healthy",
        "backend-compact-pool-healthy@example.com",
    )
    await _seed_primary_usage(drained_account_id, 95.0)
    await _seed_secondary_usage(drained_account_id, 95.0)
    await _seed_primary_usage(healthy_account_id, 10.0)
    await _seed_secondary_usage(healthy_account_id, 10.0)
    await _create_platform_identity(async_client, monkeypatch)

    async def fail_create_platform_compact_response(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, payload, api_key, organization, project
        raise AssertionError("a healthy ChatGPT candidate must keep backend compact on ChatGPT")

    async def fake_chatgpt_compact(payload, headers, access_token, account_id):
        del headers, access_token
        assert payload.model == "gpt-5.1"
        assert account_id == "acc_backend_compact_pool_healthy"
        return CompactResponsePayload.model_validate(
            {
                "object": "response.compaction",
                "status": "completed",
                "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                "output": [],
            }
        )

    monkeypatch.setattr(
        provider_adapters_module,
        "create_platform_compact_response",
        fail_create_platform_compact_response,
    )
    monkeypatch.setattr(provider_adapters_module, "core_compact_responses", fake_chatgpt_compact)

    response = await async_client.post(
        "/backend-api/codex/responses/compact",
        json={"model": "gpt-5.1", "instructions": "compact", "input": []},
    )
    assert response.status_code == 200
    assert response.json()["object"] == "response.compaction"

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "chatgpt_web"
    assert log.account_id == healthy_account_id
    assert log.route_class == "chatgpt_private"


@pytest.mark.asyncio
async def test_backend_codex_compact_falls_back_to_platform_when_sticky_chatgpt_session_is_still_rate_limited(
    async_client,
    monkeypatch,
):
    sticky_account_id = await _import_account(
        async_client,
        "acc_backend_compact_sticky_blocked",
        "backend-compact-sticky-blocked@example.com",
    )
    await _seed_primary_usage(sticky_account_id, 10.0)
    await _seed_secondary_usage(sticky_account_id, 10.0)
    await _set_account_status(sticky_account_id, status=AccountStatus.RATE_LIMITED, reset_after_seconds=300)
    drained_account_id = await _import_account(
        async_client,
        "acc_backend_compact_sticky_blocked_drained",
        "backend-compact-sticky-blocked-drained@example.com",
    )
    await _seed_primary_usage(drained_account_id, 95.0)
    await _seed_secondary_usage(drained_account_id, 95.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch)

    session_key = "backend-compact-session-sticky-blocked"
    async with SessionLocal() as session:
        session.add(
            StickySession(
                key=session_key,
                kind=StickySessionKind.CODEX_SESSION,
                provider_kind="chatgpt_web",
                routing_subject_id=sticky_account_id,
                account_id=sticky_account_id,
            )
        )
        await session.commit()

    async def fake_create_platform_compact_response(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        assert payload["model"] == "gpt-5.1"
        assert payload["instructions"] == "compact"
        assert payload["input"] == []
        return PlatformCompactResponseResult(
            payload=CompactResponsePayload.model_validate(
                {
                    "object": "response.compaction",
                    "status": "completed",
                    "usage": {"input_tokens": 4, "output_tokens": 5, "total_tokens": 9},
                    "output": [],
                }
            ),
            upstream_request_id="up_req_backend_compact_sticky_blocked_fallback",
        )

    async def fail_chatgpt_compact(_payload, _headers, _access_token, _account_id):
        raise AssertionError("sticky session outside grace must not suppress platform compact fallback")

    monkeypatch.setattr(
        provider_adapters_module,
        "create_platform_compact_response",
        fake_create_platform_compact_response,
    )
    monkeypatch.setattr(provider_adapters_module, "core_compact_responses", fail_chatgpt_compact)

    response = await async_client.post(
        "/backend-api/codex/responses/compact",
        headers={"session_id": session_key},
        json={"model": "gpt-5.1", "instructions": "compact", "input": []},
    )
    assert response.status_code == 200
    assert response.json()["object"] == "response.compaction"

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "openai_platform"
    assert log.routing_subject_id == identity_id
    assert log.route_class == "chatgpt_private"
    assert log.upstream_request_id == "up_req_backend_compact_sticky_blocked_fallback"


@pytest.mark.asyncio
async def test_backend_codex_compact_falls_back_to_platform_when_secondary_usage_is_depleted(async_client, monkeypatch):
    account_id = await _import_account(
        async_client,
        "acc_backend_compact_secondary_fallback",
        "backend-compact-secondary@example.com",
    )
    await _seed_primary_usage(account_id, 10.0)
    await _seed_secondary_usage(account_id, 95.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch)

    async def fake_create_platform_compact_response(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        assert payload["model"] == "gpt-5.1"
        assert payload["instructions"] == "compact"
        assert payload["input"] == []
        return PlatformCompactResponseResult(
            payload=CompactResponsePayload.model_validate(
                {
                    "object": "response.compaction",
                    "status": "completed",
                    "usage": {"input_tokens": 4, "output_tokens": 5, "total_tokens": 9},
                    "output": [],
                }
            ),
            upstream_request_id="up_req_backend_compact_secondary_fallback",
        )

    async def fail_chatgpt_compact(_payload, _headers, _access_token, _account_id):
        raise AssertionError("secondary-drained backend compact fallback should bypass ChatGPT compact transport")

    monkeypatch.setattr(
        provider_adapters_module,
        "create_platform_compact_response",
        fake_create_platform_compact_response,
    )
    monkeypatch.setattr(provider_adapters_module, "core_compact_responses", fail_chatgpt_compact)

    response = await async_client.post(
        "/backend-api/codex/responses/compact",
        json={"model": "gpt-5.1", "instructions": "compact", "input": []},
    )
    assert response.status_code == 200
    assert response.json()["object"] == "response.compaction"

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "openai_platform"
    assert log.routing_subject_id == identity_id
    assert log.route_class == "chatgpt_private"
    assert log.upstream_request_id == "up_req_backend_compact_secondary_fallback"


@pytest.mark.parametrize(
    ("path", "route_families", "expected_code", "expected_param"),
    [
        ("/v1/responses", ["public_responses_http"], "provider_transport_unsupported", "transport"),
        ("/backend-api/codex/responses", ["backend_codex_http"], "provider_transport_unsupported", "transport"),
    ],
)
@pytest.mark.asyncio
async def test_platform_only_websocket_routes_reject_before_upstream_transport(
    async_client,
    app_instance,
    monkeypatch,
    path: str,
    route_families: list[str],
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

    await _insert_platform_identity_direct(route_families=route_families)
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
async def test_backend_codex_models_require_chatgpt_when_only_platform_is_available(async_client, monkeypatch):
    await _insert_platform_identity_direct(route_families=["backend_codex_http"])

    async def fail_build_codex_models_response(*args, **kwargs):
        del args, kwargs
        raise AssertionError("platform-only backend codex models rejection must not fall through to ChatGPT")

    monkeypatch.setattr(proxy_api_module, "_build_codex_models_response", fail_build_codex_models_response)

    response = await async_client.get("/backend-api/codex/models")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "provider_fallback_requires_chatgpt"


@pytest.mark.asyncio
async def test_backend_codex_responses_uses_platform_when_backend_http_usage_is_drained(
    async_client,
    monkeypatch,
):
    account_id = await _import_account(async_client, "acc_backend_http_primary", "backend-http-primary@example.com")
    await _seed_primary_usage(account_id, 95.0)
    await _seed_secondary_usage(account_id, 95.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch, route_families=["backend_codex_http"])

    async def fake_stream_platform_responses(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        assert payload["model"] == "gpt-5.1"
        return PlatformStreamResponse(
            event_stream=_stream_lines(
                [
                    'data: {"type":"response.created"}\n\n',
                    'data: {"type":"response.completed","response":{"id":"resp_backend_http_platform"}}\n\n',
                ]
            ),
            upstream_request_id="up_req_backend_http_stream",
        )

    def fail_stream_http_responses(self, *args, **kwargs):
        del self, args, kwargs
        raise AssertionError(
            "backend codex responses should not stay on the ChatGPT path when backend HTTP fallback is enabled"
        )

    monkeypatch.setattr(provider_adapters_module, "stream_platform_responses", fake_stream_platform_responses)
    monkeypatch.setattr(proxy_service_module.ProxyService, "stream_http_responses", fail_stream_http_responses)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [],
            "stream": True,
            "reasoning": {"effort": "high"},
        },
    ) as response:
        assert response.status_code == 200
        lines = [line async for line in response.aiter_lines() if line]

    assert lines == [
        'data: {"type":"response.created"}',
        'data: {"type":"response.completed","response":{"id":"resp_backend_http_platform"}}',
    ]

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "openai_platform"
    assert log.routing_subject_id == identity_id
    assert log.route_class == "chatgpt_private"
    assert log.reasoning_effort == "high"


@pytest.mark.asyncio
async def test_backend_codex_responses_platform_fallback_binds_prompt_cache_key_not_session(
    async_client,
    monkeypatch,
):
    account_id = await _import_account(
        async_client,
        "acc_backend_http_platform_cache_key",
        "backend-http-platform-cache-key@example.com",
    )
    await _seed_primary_usage(account_id, 95.0)
    await _seed_secondary_usage(account_id, 95.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch, route_families=["backend_codex_http"])
    forwarded_prompt_cache_keys: list[str] = []

    async def fake_stream_platform_responses(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        assert payload["model"] == "gpt-5.1"
        cache_key = payload.get("prompt_cache_key")
        assert isinstance(cache_key, str)
        forwarded_prompt_cache_keys.append(cache_key)
        return PlatformStreamResponse(
            event_stream=_stream_lines(
                [
                    'data: {"type":"response.created"}\n\n',
                    'data: {"type":"response.completed","response":{"id":"resp_backend_http_platform_cache"}}\n\n',
                ]
            ),
            upstream_request_id="up_req_backend_http_platform_cache_stream",
        )

    def fail_stream_http_responses(self, *args, **kwargs):
        del self, args, kwargs
        raise AssertionError("backend Codex Platform fallback should not use ChatGPT transport")

    monkeypatch.setattr(provider_adapters_module, "stream_platform_responses", fake_stream_platform_responses)
    monkeypatch.setattr(proxy_service_module.ProxyService, "stream_http_responses", fail_stream_http_responses)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        headers={"session_id": "backend-session-1"},
        json={
            "model": "gpt-5.1",
            "instructions": "stable system prompt",
            "input": [{"role": "user", "content": "first prompt"}],
            "stream": True,
        },
    ) as response:
        assert response.status_code == 200
        lines = [line async for line in response.aiter_lines() if line]

    assert lines[-1] == 'data: {"type":"response.completed","response":{"id":"resp_backend_http_platform_cache"}}'
    assert len(forwarded_prompt_cache_keys) == 1
    prompt_cache_key = forwarded_prompt_cache_keys[0]

    async with SessionLocal() as session:
        platform_sticky = await session.scalar(
            select(StickySession).where(
                StickySession.key == prompt_cache_key,
                StickySession.kind == StickySessionKind.PROMPT_CACHE,
                StickySession.provider_kind == "openai_platform",
            )
        )
        session_sticky = await session.scalar(
            select(StickySession).where(
                StickySession.key == "backend-session-1",
                StickySession.kind == StickySessionKind.PROMPT_CACHE,
                StickySession.provider_kind == "openai_platform",
            )
        )

    assert platform_sticky is not None
    assert platform_sticky.routing_subject_id == identity_id
    assert session_sticky is None
    log = await _latest_request_log()
    assert log is not None
    assert log.session_id == "backend-session-1"


@pytest.mark.asyncio
async def test_backend_codex_responses_uses_platform_when_force_fallback_is_enabled(
    async_client,
    monkeypatch,
):
    account_id = await _import_account(async_client, "acc_backend_http_force", "backend-http-force@example.com")
    await _seed_primary_usage(account_id, 10.0)
    await _seed_secondary_usage(account_id, 10.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch, route_families=["backend_codex_http"])

    settings = proxy_service_module.get_settings()
    settings.platform_fallback_force_enabled = True
    monkeypatch.setattr(load_balancer_module, "get_settings", lambda: settings)

    async def fake_stream_platform_responses(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        assert payload["model"] == "gpt-5.1"
        return PlatformStreamResponse(
            event_stream=_stream_lines(
                [
                    'data: {"type":"response.created"}\n\n',
                    'data: {"type":"response.completed","response":{"id":"resp_backend_http_platform_force"}}\n\n',
                ]
            ),
            upstream_request_id="up_req_backend_http_force_stream",
        )

    def fail_stream_http_responses(self, *args, **kwargs):
        del self, args, kwargs
        raise AssertionError("force fallback should bypass the ChatGPT transport for backend codex responses")

    monkeypatch.setattr(provider_adapters_module, "stream_platform_responses", fake_stream_platform_responses)
    monkeypatch.setattr(proxy_service_module.ProxyService, "stream_http_responses", fail_stream_http_responses)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json={"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True},
    ) as response:
        assert response.status_code == 200
        lines = [line async for line in response.aiter_lines() if line]

    assert lines == [
        'data: {"type":"response.created"}',
        'data: {"type":"response.completed","response":{"id":"resp_backend_http_platform_force"}}',
    ]

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "openai_platform"
    assert log.routing_subject_id == identity_id
    assert log.route_class == "chatgpt_private"


@pytest.mark.asyncio
async def test_backend_codex_responses_uses_platform_when_other_candidate_has_no_usage_snapshot(
    async_client,
    monkeypatch,
):
    drained_account_id = await _import_account(
        async_client,
        "acc_backend_http_drained",
        "backend-http-drained@example.com",
    )
    await _seed_primary_usage(drained_account_id, 95.0)
    await _seed_secondary_usage(drained_account_id, 95.0)
    await _import_account(async_client, "acc_backend_http_unknown", "backend-http-unknown@example.com")
    identity_id = await _create_platform_identity(async_client, monkeypatch, route_families=["backend_codex_http"])

    async def fake_stream_platform_responses(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        assert payload["model"] == "gpt-5.1"
        return PlatformStreamResponse(
            event_stream=_stream_lines(
                [
                    'data: {"type":"response.created"}\n\n',
                    (
                        'data: {"type":"response.completed","response":'
                        '{"id":"resp_backend_http_unknown_usage_platform"}}\n\n'
                    ),
                ]
            ),
            upstream_request_id="up_req_backend_http_unknown_usage_stream",
        )

    def fail_stream_http_responses(self, *args, **kwargs):
        del self, args, kwargs
        raise AssertionError("unknown ChatGPT usage snapshots must not keep backend Codex fallback on ChatGPT")

    monkeypatch.setattr(provider_adapters_module, "stream_platform_responses", fake_stream_platform_responses)
    monkeypatch.setattr(proxy_service_module.ProxyService, "stream_http_responses", fail_stream_http_responses)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json={"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True},
    ) as response:
        assert response.status_code == 200
        lines = [line async for line in response.aiter_lines() if line]

    assert lines == [
        'data: {"type":"response.created"}',
        'data: {"type":"response.completed","response":{"id":"resp_backend_http_unknown_usage_platform"}}',
    ]

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "openai_platform"
    assert log.routing_subject_id == identity_id
    assert log.route_class == "chatgpt_private"


@pytest.mark.asyncio
async def test_backend_codex_responses_uses_platform_when_other_candidate_has_partial_usage_snapshot(
    async_client,
    monkeypatch,
):
    drained_account_id = await _import_account(
        async_client,
        "acc_backend_http_partial_drained",
        "backend-http-partial-drained@example.com",
    )
    await _seed_primary_usage(drained_account_id, 95.0)
    await _seed_secondary_usage(drained_account_id, 95.0)
    await _import_account(async_client, "acc_backend_http_partial_known", "backend-http-partial-known@example.com")
    async with SessionLocal() as session:
        result = await session.execute(
            select(Account.id).where(Account.chatgpt_account_id == "acc_backend_http_partial_known")
        )
        partial_account_id = result.scalar_one()
    await _seed_primary_usage(partial_account_id, 10.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch, route_families=["backend_codex_http"])

    async def fake_stream_platform_responses(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        assert payload["model"] == "gpt-5.1"
        return PlatformStreamResponse(
            event_stream=_stream_lines(
                [
                    'data: {"type":"response.created"}\n\n',
                    (
                        'data: {"type":"response.completed","response":'
                        '{"id":"resp_backend_http_partial_usage_platform"}}\n\n'
                    ),
                ]
            ),
            upstream_request_id="up_req_backend_http_partial_usage_stream",
        )

    def fail_stream_http_responses(self, *args, **kwargs):
        del self, args, kwargs
        raise AssertionError("partial-missing ChatGPT usage snapshots must not keep backend Codex fallback on ChatGPT")

    monkeypatch.setattr(provider_adapters_module, "stream_platform_responses", fake_stream_platform_responses)
    monkeypatch.setattr(proxy_service_module.ProxyService, "stream_http_responses", fail_stream_http_responses)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json={"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True},
    ) as response:
        assert response.status_code == 200
        lines = [line async for line in response.aiter_lines() if line]

    assert lines == [
        'data: {"type":"response.created"}',
        'data: {"type":"response.completed","response":{"id":"resp_backend_http_partial_usage_platform"}}',
    ]

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "openai_platform"
    assert log.routing_subject_id == identity_id
    assert log.route_class == "chatgpt_private"


@pytest.mark.asyncio
async def test_backend_codex_responses_with_sticky_turn_state_keeps_chatgpt_when_usage_drained(
    async_client,
    monkeypatch,
):
    raw_account_id = "acc_backend_http_sticky_turn_state"
    sticky_account_id = await _import_account(
        async_client,
        raw_account_id,
        "backend-http-sticky-turn-state@example.com",
    )
    await _seed_primary_usage(sticky_account_id, 96.0)
    await _seed_secondary_usage(sticky_account_id, 96.0)
    other_account_id = await _import_account(
        async_client,
        "acc_backend_http_sticky_turn_state_other",
        "backend-http-sticky-turn-state-other@example.com",
    )
    await _seed_primary_usage(other_account_id, 10.0)
    await _seed_secondary_usage(other_account_id, 10.0)
    await _create_platform_identity(async_client, monkeypatch, route_families=["backend_codex_http"])

    turn_state = "turn_state_chatgpt_sticky"
    async with SessionLocal() as session:
        session.add(
            StickySession(
                key=turn_state,
                kind=StickySessionKind.CODEX_SESSION,
                provider_kind="chatgpt_web",
                routing_subject_id=sticky_account_id,
                account_id=sticky_account_id,
            )
        )
        await session.commit()

    async def fail_stream_platform_responses(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, payload, api_key, organization, project
        raise AssertionError("sticky backend Codex continuity must keep selectable ChatGPT owner")

    async def fake_core_stream_responses(payload, headers, access_token, account_id, base_url=None, **kwargs):
        del headers, access_token, base_url, kwargs
        assert payload.model == "gpt-5.1"
        assert account_id == raw_account_id
        yield 'data: {"type":"response.created"}\n\n'
        yield 'data: {"type":"response.completed","response":{"id":"resp_backend_http_sticky_chatgpt"}}\n\n'

    monkeypatch.setattr(provider_adapters_module, "stream_platform_responses", fail_stream_platform_responses)
    monkeypatch.setattr(provider_adapters_module, "core_stream_responses", fake_core_stream_responses)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        headers={"x-codex-turn-state": turn_state},
        json={"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True},
    ) as response:
        assert response.status_code == 200
        lines = [line async for line in response.aiter_lines() if line]

    events = [json.loads(line[6:]) for line in lines if line.startswith("data: ")]
    assert [event.get("type") for event in events] == ["response.created", "response.completed"]
    assert events[-1]["response"]["id"] == "resp_backend_http_sticky_chatgpt"

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "chatgpt_web"
    assert log.account_id == sticky_account_id


@pytest.mark.asyncio
async def test_backend_codex_responses_force_fallback_overrides_sticky_turn_state(
    async_client,
    monkeypatch,
):
    settings = proxy_service_module.get_settings()
    settings.platform_fallback_force_enabled = True
    sticky_account_id = await _import_account(
        async_client,
        "acc_backend_http_force_sticky_turn_state",
        "backend-http-force-sticky-turn-state@example.com",
    )
    await _seed_primary_usage(sticky_account_id, 96.0)
    await _seed_secondary_usage(sticky_account_id, 96.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch, route_families=["backend_codex_http"])

    turn_state = "turn_state_chatgpt_force_sticky"
    async with SessionLocal() as session:
        session.add(
            StickySession(
                key=turn_state,
                kind=StickySessionKind.CODEX_SESSION,
                provider_kind="chatgpt_web",
                routing_subject_id=sticky_account_id,
                account_id=sticky_account_id,
            )
        )
        await session.commit()

    async def fake_stream_platform_responses(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        assert payload["model"] == "gpt-5.1"
        return PlatformStreamResponse(
            event_stream=_stream_lines(
                [
                    'data: {"type":"response.created"}\n\n',
                    'data: {"type":"response.completed","response":{"id":"resp_backend_http_force_platform"}}\n\n',
                ]
            ),
            upstream_request_id="up_req_backend_http_force_sticky_stream",
        )

    def fail_stream_http_responses(self, *args, **kwargs):
        del self, args, kwargs
        raise AssertionError("force fallback must override sticky backend Codex continuity")

    monkeypatch.setattr(provider_adapters_module, "stream_platform_responses", fake_stream_platform_responses)
    monkeypatch.setattr(proxy_service_module.ProxyService, "stream_http_responses", fail_stream_http_responses)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        headers={"x-codex-turn-state": turn_state},
        json={"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True},
    ) as response:
        assert response.status_code == 200
        lines = [line async for line in response.aiter_lines() if line]

    assert lines == [
        'data: {"type":"response.created"}',
        'data: {"type":"response.completed","response":{"id":"resp_backend_http_force_platform"}}',
    ]

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "openai_platform"
    assert log.routing_subject_id == identity_id
    assert log.route_class == "chatgpt_private"


@pytest.mark.asyncio
async def test_backend_codex_responses_with_turn_state_header_uses_platform_fallback(async_client, monkeypatch):
    account_id = await _import_account(
        async_client,
        "acc_backend_http_turn_state",
        "backend-http-turn-state@example.com",
    )
    await _seed_primary_usage(account_id, 95.0)
    await _seed_secondary_usage(account_id, 95.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch, route_families=["backend_codex_http"])

    async def fake_stream_platform_responses(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        assert payload["model"] == "gpt-5.1"
        return PlatformStreamResponse(
            event_stream=_stream_lines(
                [
                    'data: {"type":"response.created"}\n\n',
                    'data: {"type":"response.completed","response":{"id":"resp_backend_http_turn_state_platform"}}\n\n',
                ]
            ),
            upstream_request_id="up_req_backend_http_turn_state_stream",
        )

    def fail_stream_http_responses(self, *args, **kwargs):
        del self, args, kwargs
        raise AssertionError("backend codex session headers should not suppress eligible platform fallback")

    monkeypatch.setattr(provider_adapters_module, "stream_platform_responses", fake_stream_platform_responses)
    monkeypatch.setattr(proxy_service_module.ProxyService, "stream_http_responses", fail_stream_http_responses)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        headers={"x-codex-turn-state": "turn_state_chatgpt"},
        json={"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True},
    ) as response:
        assert response.status_code == 200
        lines = [line async for line in response.aiter_lines() if line]

    assert lines == [
        'data: {"type":"response.created"}',
        'data: {"type":"response.completed","response":{"id":"resp_backend_http_turn_state_platform"}}',
    ]

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "openai_platform"
    assert log.routing_subject_id == identity_id


@pytest.mark.asyncio
async def test_v1_responses_platform_logs_enforced_api_key_service_tier(async_client, monkeypatch):
    account_id = await _import_account(async_client, "acc_platform_enforced_tier", "platform-enforced-tier@example.com")
    await _seed_primary_usage(account_id, 95.0)
    await _seed_secondary_usage(account_id, 95.0)
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
            "name": "platform-enforced-service-tier",
            "allowedModels": ["gpt-5.1"],
            "enforcedModel": "gpt-5.1",
            "enforcedServiceTier": "fast",
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]
    assert created.json()["enforcedServiceTier"] == "priority"

    async def fake_create_platform_response(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, api_key, organization, project
        assert payload["model"] == "gpt-5.1"
        assert payload["service_tier"] == "priority"
        _assert_platform_text_input(payload, "hi")
        return PlatformResponseResult(
            payload=OpenAIResponsePayload.model_validate(
                {
                    "id": "resp_platform_enforced_tier",
                    "status": "completed",
                    "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
                }
            ),
            upstream_request_id="up_req_platform_enforced_tier",
        )

    monkeypatch.setattr(provider_adapters_module, "create_platform_response", fake_create_platform_response)

    response = await async_client.post(
        "/v1/responses",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": "gpt-5.1", "input": "hi", "service_tier": "default"},
    )
    assert response.status_code == 200
    assert response.json()["id"] == "resp_platform_enforced_tier"

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "openai_platform"
    assert log.requested_service_tier == "priority"
    assert log.actual_service_tier is None
    assert log.service_tier == "priority"


@pytest.mark.asyncio
async def test_backend_codex_responses_rejects_continuity_when_only_platform(async_client, monkeypatch):
    await _insert_platform_identity_direct(route_families=["backend_codex_http"])

    async def fail_create_platform_response(self, *args, **kwargs):
        del self, args, kwargs
        raise AssertionError("continuity rejection must not start platform transport")

    monkeypatch.setattr(proxy_service_module.ProxyService, "create_platform_response", fail_create_platform_response)

    response = await async_client.post(
        "/backend-api/codex/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [],
            "previous_response_id": "resp_prev_1",
        },
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "provider_continuity_unsupported"
    assert payload["error"]["param"] == "previous_response_id"


@pytest.mark.asyncio
async def test_v1_responses_invalid_payload_persists_request_log(async_client):
    response = await async_client.post(
        "/v1/responses",
        json={"model": "gpt-5.1", "messages": [{"role": "tool", "content": "hi"}]},
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "invalid_request_error"

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind is None
    assert log.route_class == "openai_public_http"
    assert log.rejection_reason == "invalid_responses_payload"
    assert log.error_code == "invalid_request_error"


@pytest.mark.asyncio
async def test_v1_compact_invalid_payload_persists_request_log(async_client):
    response = await async_client.post(
        "/v1/responses/compact",
        json={"model": "gpt-5.1", "messages": [{"role": "tool", "content": "hi"}]},
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["code"] == "invalid_request_error"

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind is None
    assert log.route_class == "openai_public_http"
    assert log.rejection_reason == "invalid_compact_payload"
    assert log.error_code == "invalid_request_error"


@pytest.mark.asyncio
async def test_v1_responses_platform_auth_failure_persists_request_log(async_client, monkeypatch):
    account_id = await _import_account(async_client, "acc_resp_auth_fail", "resp-auth-fail@example.com")
    await _seed_primary_usage(account_id, 95.0)
    identity_id = await _create_platform_identity(async_client, monkeypatch, route_families=["public_responses_http"])

    async def fail_create_platform_response(*, base_url, payload, api_key, organization=None, project=None):
        del base_url, payload, api_key, organization, project
        raise OpenAIPlatformError(
            401,
            {
                "error": {
                    "code": "invalid_api_key",
                    "message": "Invalid API key",
                }
            },
        )

    monkeypatch.setattr(provider_adapters_module, "create_platform_response", fail_create_platform_response)

    response = await async_client.post(
        "/v1/responses",
        json={"model": "gpt-5.1", "input": "hi"},
    )
    assert response.status_code == 401

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "openai_platform"
    assert log.routing_subject_id == identity_id
    assert log.route_class == "openai_public_http"
    assert log.rejection_reason == "platform_response_request_failed"
    assert log.error_code == "invalid_api_key"


@pytest.mark.asyncio
async def test_v1_compact_not_implemented_persists_a_single_request_log(async_client, monkeypatch):
    account_id = await _import_account(async_client, "acc_v1_compact_not_impl", "v1-compact-not-impl@example.com")
    await _seed_primary_usage(account_id, 10.0)
    await _seed_secondary_usage(account_id, 10.0)
    before_count = await _request_log_count()

    async def fail_chatgpt_compact(_payload, _headers, _access_token, _account_id):
        raise NotImplementedError("compact transport unavailable in test")

    monkeypatch.setattr(provider_adapters_module, "core_compact_responses", fail_chatgpt_compact)

    response = await async_client.post("/v1/responses/compact", json={"model": "gpt-5.1", "input": "hi"})
    assert response.status_code == 501
    payload = response.json()
    assert payload["error"]["code"] == "not_implemented"

    after_count = await _request_log_count()
    assert after_count == before_count + 1

    log = await _latest_request_log()
    assert log is not None
    assert log.provider_kind == "chatgpt_web"
    assert log.route_class == "openai_public_http"
    assert log.rejection_reason == "provider_compact_not_implemented"
    assert log.error_code == "not_implemented"

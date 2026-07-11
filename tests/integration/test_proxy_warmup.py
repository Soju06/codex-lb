from __future__ import annotations

import asyncio
import base64
import json
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, update

import app.modules.proxy.service as proxy_module
from app.core.auth import generate_unique_account_id
from app.core.clients.proxy import ProxyResponseError
from app.core.config.settings import get_settings
from app.core.errors import openai_error
from app.core.exceptions import ProxyRateLimitError
from app.core.openai.models import CompactResponsePayload
from app.core.upstream_proxy import ResolvedProxyEndpoint, ResolvedUpstreamRoute, UpstreamProxyRouteError
from app.core.utils.time import utcnow
from app.db.models import ApiKeyLimit, RequestLog
from app.db.session import SessionLocal
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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


async def _enable_api_key_auth(async_client) -> None:
    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert response.status_code == 200


async def _create_api_key(
    async_client,
    *,
    name: str,
    allowed_models: list[str] | None = None,
    enforced_model: str | None = None,
    limits: list[dict[str, object]] | None = None,
) -> tuple[str, str]:
    payload: dict[str, object] = {"name": name}
    if allowed_models is not None:
        payload["allowedModels"] = allowed_models
    if enforced_model is not None:
        payload["enforcedModel"] = enforced_model
    if limits is not None:
        payload["limits"] = limits
    response = await async_client.post("/api/api-keys/", json=payload)
    assert response.status_code == 200
    payload = response.json()
    return payload["id"], payload["key"]


async def _add_primary_usage(account_id: str, *, used_percent: float, window_minutes: int) -> None:
    async with SessionLocal() as session:
        repo = UsageRepository(session)
        await repo.add_entry(
            account_id=account_id,
            used_percent=used_percent,
            window="primary",
            window_minutes=window_minutes,
            reset_at=int((utcnow() + timedelta(hours=5)).timestamp()),
        )


def _install_successful_warmup_stub(monkeypatch: pytest.MonkeyPatch, captured_models: list[str]) -> None:
    async def _fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        return account

    async def _fake_compact(payload, headers, access_token, account_id, session=None):
        del headers, access_token, session
        captured_models.append(payload.model)
        return CompactResponsePayload.model_validate(
            {
                "object": "response.compact",
                "id": f"resp-{account_id or 'none'}",
                "status": "completed",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", _fake_ensure_fresh)
    monkeypatch.setattr(proxy_module, "core_compact_responses", _fake_compact)


def _set_warmup_model_env(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("CODEX_LB_WARMUP_MODEL", value)
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_warmup_normal_mode_uses_configured_model_and_logs_warmup_kind(async_client, monkeypatch):
    _set_warmup_model_env(monkeypatch, "gpt-5.4-env-ignored")
    await _enable_api_key_auth(async_client)
    settings_response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "apiKeyAuthEnabled": True,
            "warmupModel": "gpt-5.4-nano",
        },
    )
    assert settings_response.status_code == 200
    eligible_id = await _import_account(async_client, "acc-warmup-eligible", "warmup-eligible@example.com")
    ineligible_id = await _import_account(async_client, "acc-warmup-ineligible", "warmup-ineligible@example.com")
    await _add_primary_usage(eligible_id, used_percent=0.0, window_minutes=300)
    await _add_primary_usage(ineligible_id, used_percent=11.0, window_minutes=300)

    key_id, key = await _create_api_key(
        async_client,
        name="warmup-normal",
        limits=[{"limitType": "total_tokens", "limitWindow": "daily", "maxValue": 10}],
    )
    captured_models: list[str] = []
    _install_successful_warmup_stub(monkeypatch, captured_models)

    response = await async_client.post(
        "/v1/warmup",
        headers={"Authorization": f"Bearer {key}"},
        json={"mode": "normal"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "normal"
    assert payload["total_accounts"] == 2
    assert len(payload["submitted"]) == 1
    assert payload["submitted"][0]["account_id"] == eligible_id
    assert payload["submitted"][0]["model"] == "gpt-5.4-nano"
    assert payload["skipped"] == [{"account_id": ineligible_id, "reason": "ineligible_primary_usage"}]
    assert payload["failed"] == []
    assert captured_models == ["gpt-5.4-nano"]

    async with SessionLocal() as session:
        rows = (await session.execute(select(RequestLog).order_by(RequestLog.id.asc()))).scalars().all()
        limit = (
            await session.execute(
                select(ApiKeyLimit).where(ApiKeyLimit.api_key_id == key_id, ApiKeyLimit.limit_type == "total_tokens")
            )
        ).scalar_one()
    assert len(rows) == 1
    assert rows[0].request_kind == "warmup"
    assert rows[0].model == "gpt-5.4-nano"
    assert limit.current_value == 0


@pytest.mark.asyncio
async def test_warmup_compact_keeps_local_account_and_upstream_route_separate(async_client, monkeypatch):
    await _enable_api_key_auth(async_client)
    settings_response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "apiKeyAuthEnabled": True,
            "warmupModel": "gpt-5.4-nano",
        },
    )
    assert settings_response.status_code == 200
    upstream_account_id = "acc-warmup-upstream-route"
    eligible_id = await _import_account(async_client, upstream_account_id, "warmup-route@example.com")
    await _add_primary_usage(eligible_id, used_percent=0.0, window_minutes=300)
    _, key = await _create_api_key(async_client, name="warmup-route")
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080),
    )
    captured: dict[str, object] = {}

    async def _fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        return account

    async def _fake_route(self, account, *, operation):
        del self
        captured["route_account_id"] = account.id
        captured["route_operation"] = operation
        return route

    async def _fake_compact(
        payload,
        headers,
        access_token,
        account_id,
        session=None,
        *,
        route=None,
        route_trace=None,
        chatgpt_account_id=None,
        allow_direct_egress=True,
    ):
        del headers, access_token, session
        captured["model"] = payload.model
        captured["account_id"] = account_id
        captured["route"] = route
        captured["chatgpt_account_id"] = chatgpt_account_id
        captured["allow_direct_egress"] = allow_direct_egress
        if route_trace is not None and route is not None:
            route_trace.record(route=route, fallback_used=False)
        return CompactResponsePayload.model_validate(
            {
                "object": "response.compact",
                "id": "resp-warmup-route",
                "status": "completed",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", _fake_ensure_fresh)
    monkeypatch.setattr(proxy_module.ProxyService, "_resolve_upstream_route_for_account", _fake_route)
    monkeypatch.setattr(proxy_module, "core_compact_responses", _fake_compact)

    response = await async_client.post(
        "/v1/warmup",
        headers={"Authorization": f"Bearer {key}"},
        json={"mode": "normal"},
    )

    assert response.status_code == 200
    assert captured["model"] == "gpt-5.4-nano"
    assert captured["account_id"] == upstream_account_id
    assert captured["chatgpt_account_id"] == upstream_account_id
    assert captured["route"] is route
    assert captured["allow_direct_egress"] is False
    assert captured["route_account_id"] == eligible_id
    assert captured["route_operation"] == "warmup"

    async with SessionLocal() as session:
        row = (await session.execute(select(RequestLog).where(RequestLog.request_kind == "warmup"))).scalar_one()

    assert row.upstream_proxy_route_mode == "account_bound"
    assert row.upstream_proxy_pool_id == "pool_1"
    assert row.upstream_proxy_endpoint_id == "ep_1"
    assert row.upstream_proxy_fallback_used is False


@pytest.mark.asyncio
async def test_warmup_compact_omits_filtered_upstream_account_header(async_client, monkeypatch):
    await _enable_api_key_auth(async_client)
    settings_response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "apiKeyAuthEnabled": True,
            "warmupModel": "gpt-5.4-nano",
        },
    )
    assert settings_response.status_code == 200
    eligible_id = await _import_account(async_client, "acc-warmup-local-filter", "warmup-local-filter@example.com")
    async with SessionLocal() as session:
        await session.execute(
            update(proxy_module.Account)
            .where(proxy_module.Account.id == eligible_id)
            .values(chatgpt_account_id="local_warmup_filter")
        )
        await session.commit()
    await _add_primary_usage(eligible_id, used_percent=0.0, window_minutes=300)
    _, key = await _create_api_key(async_client, name="warmup-local-filter")
    route = ResolvedUpstreamRoute(
        mode="account_bound",
        pool_id="pool_1",
        endpoint=ResolvedProxyEndpoint("ep_1", "http", "proxy.test", 8080),
    )
    captured: dict[str, object] = {}

    async def _fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        return account

    async def _fake_route(self, account, *, operation):
        del self
        captured["route_account_id"] = account.id
        captured["route_operation"] = operation
        return route

    async def _fake_compact(
        payload,
        headers,
        access_token,
        account_id,
        session=None,
        *,
        route=None,
        route_trace=None,
        chatgpt_account_id=None,
        allow_direct_egress=True,
    ):
        del payload, headers, access_token, session, allow_direct_egress
        captured["account_id"] = account_id
        captured["route"] = route
        captured["chatgpt_account_id"] = chatgpt_account_id
        if route_trace is not None and route is not None:
            route_trace.record(route=route, fallback_used=False)
        return CompactResponsePayload.model_validate(
            {
                "object": "response.compact",
                "id": "resp-warmup-local-filter",
                "status": "completed",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", _fake_ensure_fresh)
    monkeypatch.setattr(proxy_module.ProxyService, "_resolve_upstream_route_for_account", _fake_route)
    monkeypatch.setattr(proxy_module, "core_compact_responses", _fake_compact)

    response = await async_client.post(
        "/v1/warmup",
        headers={"Authorization": f"Bearer {key}"},
        json={"mode": "normal"},
    )

    assert response.status_code == 200
    assert captured["account_id"] is None
    assert captured["chatgpt_account_id"] is None
    assert captured["route"] is route
    assert captured["route_account_id"] == eligible_id
    assert captured["route_operation"] == "warmup"


@pytest.mark.asyncio
async def test_warmup_route_failure_records_fail_closed_reason(async_client, monkeypatch):
    await _enable_api_key_auth(async_client)
    settings_response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "apiKeyAuthEnabled": True,
            "warmupModel": "gpt-5.4-nano",
        },
    )
    assert settings_response.status_code == 200
    upstream_account_id = "acc-warmup-route-fail"
    eligible_id = await _import_account(async_client, upstream_account_id, "warmup-route-fail@example.com")
    await _add_primary_usage(eligible_id, used_percent=0.0, window_minutes=300)
    _, key = await _create_api_key(async_client, name="warmup-route-fail")

    async def _fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        return account

    async def _fake_route(self, account, *, operation):
        del self, account, operation
        raise UpstreamProxyRouteError("no_healthy_endpoint", account_id=eligible_id)

    compact = AsyncMock()
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", _fake_ensure_fresh)
    monkeypatch.setattr(proxy_module.ProxyService, "_resolve_upstream_route_for_account", _fake_route)
    monkeypatch.setattr(proxy_module, "core_compact_responses", compact)

    response = await async_client.post(
        "/v1/warmup",
        headers={"Authorization": f"Bearer {key}"},
        json={"mode": "normal"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["submitted"] == []
    assert payload["failed"][0]["account_id"] == eligible_id
    assert payload["failed"][0]["error_code"] == "upstream_proxy_unavailable"
    compact.assert_not_awaited()

    async with SessionLocal() as session:
        row = (await session.execute(select(RequestLog).where(RequestLog.request_kind == "warmup"))).scalar_one()

    assert row.status == "error"
    assert row.error_code == "upstream_proxy_unavailable"
    assert row.upstream_proxy_fail_closed_reason == "no_healthy_endpoint"


@pytest.mark.asyncio
async def test_warmup_normalizes_model_alias_before_upstream(async_client, monkeypatch):
    _set_warmup_model_env(monkeypatch, "gpt-5.4-mini-high")
    await _enable_api_key_auth(async_client)
    settings_response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "apiKeyAuthEnabled": True,
            "warmupModel": "gpt-5.4-mini-high",
        },
    )
    assert settings_response.status_code == 200
    eligible_id = await _import_account(async_client, "acc-warmup-alias", "warmup-alias@example.com")
    await _add_primary_usage(eligible_id, used_percent=0.0, window_minutes=300)

    _, key = await _create_api_key(async_client, name="warmup-alias")
    captured_payloads: list[dict[str, object]] = []

    async def _fake_compact(payload, headers, access_token, account_id, session=None):
        del headers, access_token, account_id, session
        captured_payloads.append(payload.model_dump(mode="json", by_alias=True, exclude_none=True))
        return CompactResponsePayload.model_validate(
            {
                "object": "response.compact",
                "id": "resp-warmup-alias",
                "status": "completed",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )

    async def _fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        return account

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", _fake_ensure_fresh)
    monkeypatch.setattr(proxy_module, "core_compact_responses", _fake_compact)

    response = await async_client.post(
        "/v1/warmup",
        headers={"Authorization": f"Bearer {key}"},
        json={"mode": "normal"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["submitted"][0]["model"] == "gpt-5.4-mini-high"
    captured_request_payload = captured_payloads[0]
    assert captured_request_payload["model"] == "gpt-5.4-mini"
    assert captured_request_payload["reasoning"] == {"effort": "high"}


@pytest.mark.asyncio
async def test_warmup_prohibits_fast_model_alias_priority_tier(async_client, monkeypatch):
    await _enable_api_key_auth(async_client)
    settings_response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "apiKeyAuthEnabled": True,
            "warmupModel": "gpt-5.6-sol-xhigh-fast",
            "prohibitFastMode": True,
        },
    )
    assert settings_response.status_code == 200
    eligible_id = await _import_account(async_client, "acc-warmup-prohibit-fast", "warmup-prohibit-fast@example.com")
    await _add_primary_usage(eligible_id, used_percent=0.0, window_minutes=300)
    _, key = await _create_api_key(async_client, name="warmup-prohibit-fast")
    captured_payloads: list[dict[str, object]] = []

    async def _fake_compact(payload, headers, access_token, account_id, session=None):
        del headers, access_token, account_id, session
        captured_payloads.append(payload.model_dump(mode="json", by_alias=True, exclude_none=True))
        return CompactResponsePayload.model_validate(
            {
                "object": "response.compact",
                "id": "resp-warmup-prohibit-fast",
                "status": "completed",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )

    async def _fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        return account

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", _fake_ensure_fresh)
    monkeypatch.setattr(proxy_module, "core_compact_responses", _fake_compact)

    response = await async_client.post(
        "/v1/warmup",
        headers={"Authorization": f"Bearer {key}"},
        json={"mode": "normal"},
    )

    assert response.status_code == 200
    captured_request_payload = captured_payloads[0]
    assert captured_request_payload["model"] == "gpt-5.6-sol"
    assert captured_request_payload["reasoning"] == {"effort": "high"}
    assert "service_tier" not in captured_request_payload


@pytest.mark.asyncio
async def test_warmup_mode_path_route_runs_without_request_body(async_client, monkeypatch):
    _set_warmup_model_env(monkeypatch, "gpt-5.4-nano")
    await _enable_api_key_auth(async_client)
    settings_response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "apiKeyAuthEnabled": True,
            "warmupModel": "gpt-5.4-nano",
        },
    )
    assert settings_response.status_code == 200
    eligible_id = await _import_account(async_client, "acc-warmup-path-mode", "warmup-path-mode@example.com")
    await _add_primary_usage(eligible_id, used_percent=0.0, window_minutes=300)

    _, key = await _create_api_key(async_client, name="warmup-path-mode")
    captured_models: list[str] = []
    _install_successful_warmup_stub(monkeypatch, captured_models)

    response = await async_client.post(
        "/v1/warmup/normal",
        headers={"Authorization": f"Bearer {key}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "normal"
    assert payload["total_accounts"] == 1
    assert [entry["account_id"] for entry in payload["submitted"]] == [eligible_id]
    assert payload["skipped"] == []
    assert payload["failed"] == []
    assert captured_models == ["gpt-5.4-nano"]


@pytest.mark.asyncio
async def test_warmup_uses_api_key_enforced_model_over_dashboard_model(async_client, monkeypatch):
    _set_warmup_model_env(monkeypatch, "gpt-5.4-nano")
    await _enable_api_key_auth(async_client)
    settings_response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "apiKeyAuthEnabled": True,
            "warmupModel": "gpt-5.4-ignored",
        },
    )
    assert settings_response.status_code == 200

    eligible_id = await _import_account(async_client, "acc-warmup-enforced", "warmup-enforced@example.com")
    await _add_primary_usage(eligible_id, used_percent=0.0, window_minutes=300)

    _, key = await _create_api_key(
        async_client,
        name="warmup-enforced-model",
        enforced_model="gpt-4.1-nano",
    )
    captured_payloads: list[dict[str, object]] = []

    async def _fake_compact(payload, headers, access_token, account_id, session=None):
        del headers, access_token, account_id, session
        captured_payloads.append(payload.model_dump(mode="json", by_alias=True, exclude_none=True))
        return CompactResponsePayload.model_validate(
            {
                "object": "response.compact",
                "id": "resp-warmup-enforced",
                "status": "completed",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )

    async def _fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        return account

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", _fake_ensure_fresh)
    monkeypatch.setattr(proxy_module, "core_compact_responses", _fake_compact)

    response = await async_client.post(
        "/v1/warmup",
        headers={"Authorization": f"Bearer {key}"},
        json={"mode": "normal"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["submitted"][0]["model"] == "gpt-4.1-nano"
    captured_request_payload = captured_payloads[0]
    assert captured_request_payload["model"] == "gpt-4.1-nano"
    assert captured_request_payload["instructions"] == "Warmup request."
    assert "warmup_model" not in captured_request_payload


@pytest.mark.asyncio
async def test_warmup_strict_rejects_mixed_eligibility_without_upstream_calls(async_client, monkeypatch):
    await _enable_api_key_auth(async_client)
    eligible_id = await _import_account(async_client, "acc-strict-eligible", "strict-a@example.com")
    ineligible_id = await _import_account(async_client, "acc-strict-ineligible", "strict-b@example.com")
    await _add_primary_usage(eligible_id, used_percent=0.0, window_minutes=300)
    await _add_primary_usage(ineligible_id, used_percent=5.0, window_minutes=300)

    _, key = await _create_api_key(async_client, name="warmup-strict")
    called = False

    async def _fake_compact(*args, **kwargs):
        del args, kwargs
        nonlocal called
        called = True
        return CompactResponsePayload.model_validate({"object": "response.compact", "id": "resp-should-not-run"})

    monkeypatch.setattr(proxy_module, "core_compact_responses", _fake_compact)

    response = await async_client.post(
        "/v1/warmup",
        headers={"Authorization": f"Bearer {key}"},
        json={"mode": "strict"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert called is False


@pytest.mark.asyncio
async def test_warmup_respects_api_key_account_scope(async_client, monkeypatch):
    await _enable_api_key_auth(async_client)
    scoped_id = await _import_account(async_client, "acc-scoped-eligible", "scoped-eligible@example.com")
    other_id = await _import_account(async_client, "acc-scoped-other", "scoped-other@example.com")
    await _add_primary_usage(scoped_id, used_percent=0.0, window_minutes=300)
    await _add_primary_usage(other_id, used_percent=0.0, window_minutes=300)

    key_id, key = await _create_api_key(async_client, name="warmup-scoped")
    assign = await async_client.patch(f"/api/api-keys/{key_id}", json={"assignedAccountIds": [scoped_id]})
    assert assign.status_code == 200

    captured_models: list[str] = []
    _install_successful_warmup_stub(monkeypatch, captured_models)

    response = await async_client.post(
        "/v1/warmup",
        headers={"Authorization": f"Bearer {key}"},
        json={"mode": "force"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_accounts"] == 1
    assert [entry["account_id"] for entry in payload["submitted"]] == [scoped_id]
    assert payload["skipped"] == []
    assert payload["failed"] == []


@pytest.mark.asyncio
async def test_warmup_rejects_disallowed_model_without_upstream_calls(async_client, monkeypatch):
    _set_warmup_model_env(monkeypatch, "gpt-5.4-nano")
    await _enable_api_key_auth(async_client)
    settings_response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "apiKeyAuthEnabled": True,
            "warmupModel": "gpt-5.4-nano",
        },
    )
    assert settings_response.status_code == 200
    eligible_id = await _import_account(async_client, "acc-warmup-disallowed", "warmup-disallowed@example.com")
    await _add_primary_usage(eligible_id, used_percent=0.0, window_minutes=300)

    _, key = await _create_api_key(
        async_client,
        name="warmup-disallowed-model",
        allowed_models=["gpt-5.4-mini"],
    )
    called = False

    async def _fake_compact(*args, **kwargs):
        del args, kwargs
        nonlocal called
        called = True
        return CompactResponsePayload.model_validate({"object": "response.compact", "id": "resp-not-used"})

    monkeypatch.setattr(proxy_module, "core_compact_responses", _fake_compact)

    response = await async_client.post(
        "/v1/warmup",
        headers={"Authorization": f"Bearer {key}"},
        json={"mode": "normal"},
    )

    assert response.status_code == 403
    assert called is False


@pytest.mark.asyncio
async def test_warmup_ignores_api_key_limits_for_accounting(async_client, monkeypatch):
    _set_warmup_model_env(monkeypatch, "gpt-5.4-nano")
    await _enable_api_key_auth(async_client)
    eligible_id = await _import_account(async_client, "acc-warmup-limited", "warmup-limited@example.com")
    await _add_primary_usage(eligible_id, used_percent=0.0, window_minutes=300)

    key_id, key = await _create_api_key(
        async_client,
        name="warmup-token-limited",
        limits=[
            {
                "limitType": "total_tokens",
                "limitWindow": "daily",
                "maxValue": 1,
            }
        ],
    )
    async with SessionLocal() as session:
        await session.execute(update(ApiKeyLimit).where(ApiKeyLimit.api_key_id == key_id).values(current_value=1))
        await session.commit()
    called = False

    async def _fake_compact(*args, **kwargs):
        del args, kwargs
        nonlocal called
        called = True
        return CompactResponsePayload.model_validate(
            {
                "object": "response.compact",
                "id": "resp-warmup-limited",
                "status": "completed",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )

    monkeypatch.setattr(proxy_module, "core_compact_responses", _fake_compact)

    response = await async_client.post(
        "/v1/warmup",
        headers={"Authorization": f"Bearer {key}"},
        json={"mode": "normal"},
    )

    assert response.status_code == 200
    assert called is True
    async with SessionLocal() as session:
        log_row = (
            await session.execute(
                select(RequestLog).where(RequestLog.request_kind == "warmup").order_by(RequestLog.id.desc())
            )
        ).scalar_one()
        limit = (
            await session.execute(
                select(ApiKeyLimit).where(ApiKeyLimit.api_key_id == key_id, ApiKeyLimit.limit_type == "total_tokens")
            )
        ).scalar_one()
    assert log_row.status == "success"
    assert limit.current_value == 1


@pytest.mark.asyncio
async def test_warmup_runs_parallel_with_max_five_accounts(async_client, monkeypatch):
    await _enable_api_key_auth(async_client)

    total_accounts = 8
    for index in range(total_accounts):
        account_id = await _import_account(
            async_client,
            f"acc-warmup-parallel-{index}",
            f"warmup-parallel-{index}@example.com",
        )
        await _add_primary_usage(account_id, used_percent=0.0, window_minutes=300)

    _, key = await _create_api_key(async_client, name="warmup-parallel-limit")

    in_flight_compact_calls = 0
    peak_compact_calls = 0

    async def _fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        return account

    async def _fake_compact(payload, headers, access_token, account_id, session=None):
        del payload, headers, access_token, account_id, session
        nonlocal in_flight_compact_calls, peak_compact_calls

        in_flight_compact_calls += 1
        peak_compact_calls = max(peak_compact_calls, in_flight_compact_calls)
        try:
            await asyncio.sleep(0.05)
            return CompactResponsePayload.model_validate(
                {
                    "object": "response.compact",
                    "id": "resp-warmup-parallel",
                    "status": "completed",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            )
        finally:
            in_flight_compact_calls -= 1

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", _fake_ensure_fresh)
    monkeypatch.setattr(proxy_module, "core_compact_responses", _fake_compact)

    response = await async_client.post(
        "/v1/warmup",
        headers={"Authorization": f"Bearer {key}"},
        json={"mode": "force"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_accounts"] == total_accounts
    assert len(payload["submitted"]) == total_accounts
    assert payload["failed"] == []
    assert peak_compact_calls == 5


@pytest.mark.asyncio
async def test_warmup_account_rate_limit_failure_does_not_abort_summary(async_client, monkeypatch):
    await _enable_api_key_auth(async_client)
    first_raw_id = "acc-warmup-rate-limit-a"
    first_id = await _import_account(async_client, first_raw_id, "warmup-rate-limit-a@example.com")
    second_id = await _import_account(async_client, "acc-warmup-rate-limit-b", "warmup-rate-limit-b@example.com")
    await _add_primary_usage(first_id, used_percent=0.0, window_minutes=300)
    await _add_primary_usage(second_id, used_percent=0.0, window_minutes=300)
    _, key = await _create_api_key(async_client, name="warmup-rate-limit-isolated")

    async def _fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        return account

    async def _fake_compact(payload, headers, access_token, account_id, session=None):
        del payload, headers, access_token, session
        assert account_id not in {first_id, second_id}
        if account_id == first_raw_id:
            raise ProxyRateLimitError("account limited")
        return CompactResponsePayload.model_validate(
            {
                "object": "response.compact",
                "id": "resp-warmup-ok",
                "status": "completed",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", _fake_ensure_fresh)
    monkeypatch.setattr(proxy_module, "core_compact_responses", _fake_compact)

    response = await async_client.post(
        "/v1/warmup",
        headers={"Authorization": f"Bearer {key}"},
        json={"mode": "force"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_accounts"] == 2
    assert len(payload["submitted"]) == 1
    assert payload["submitted"][0]["account_id"] == second_id
    assert payload["failed"] == [
        {
            "account_id": first_id,
            "error_code": "rate_limit_exceeded",
            "error_message": "account limited",
        }
    ]


@pytest.mark.asyncio
async def test_warmup_does_not_reserve_api_key_usage(async_client, monkeypatch):
    await _enable_api_key_auth(async_client)
    first_raw_id = "acc-warmup-key-limit-a"
    first_id = await _import_account(async_client, first_raw_id, "warmup-key-limit-a@example.com")
    second_id = await _import_account(async_client, "acc-warmup-key-limit-b", "warmup-key-limit-b@example.com")
    await _add_primary_usage(first_id, used_percent=0.0, window_minutes=300)
    await _add_primary_usage(second_id, used_percent=0.0, window_minutes=300)
    _, key = await _create_api_key(async_client, name="warmup-key-limit-mid-fanout")

    async def _fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        return account

    reserve_usage = AsyncMock()

    async def _fake_compact(payload, headers, access_token, account_id, session=None):
        del payload, headers, access_token, session
        return CompactResponsePayload.model_validate(
            {
                "object": "response.compact",
                "id": f"resp-{account_id}",
                "status": "completed",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", _fake_ensure_fresh)
    monkeypatch.setattr(proxy_module.ProxyService, "_reserve_websocket_api_key_usage", reserve_usage)
    monkeypatch.setattr(proxy_module, "core_compact_responses", _fake_compact)

    response = await async_client.post(
        "/v1/warmup",
        headers={"Authorization": f"Bearer {key}"},
        json={"mode": "force"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_accounts"] == 2
    assert [entry["account_id"] for entry in payload["submitted"]] == [first_id, second_id]
    assert payload["failed"] == []
    reserve_usage.assert_not_awaited()


@pytest.mark.asyncio
async def test_warmup_uses_unique_request_ids_per_account(async_client, monkeypatch):
    await _enable_api_key_auth(async_client)
    first_id = await _import_account(async_client, "acc-warmup-reqid-a", "warmup-reqid-a@example.com")
    second_id = await _import_account(async_client, "acc-warmup-reqid-b", "warmup-reqid-b@example.com")
    await _add_primary_usage(first_id, used_percent=0.0, window_minutes=300)
    await _add_primary_usage(second_id, used_percent=0.0, window_minutes=300)

    _, key = await _create_api_key(async_client, name="warmup-unique-request-id")
    captured_request_ids: list[str | None] = []

    async def _fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        return account

    async def _fake_compact(payload, headers, access_token, account_id, session=None):
        del payload, access_token, account_id, session
        captured_request_ids.append(headers.get("x-request-id"))
        raise ProxyResponseError(
            500,
            openai_error("upstream_error", "boom"),
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", _fake_ensure_fresh)
    monkeypatch.setattr(proxy_module, "core_compact_responses", _fake_compact)

    response = await async_client.post(
        "/v1/warmup",
        headers={
            "Authorization": f"Bearer {key}",
            "x-request-id": "outer-warmup-request",
        },
        json={"mode": "force"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_accounts"] == 2
    assert payload["submitted"] == []
    assert len(payload["failed"]) == 2
    assert len(captured_request_ids) == 2
    assert all(request_id is not None for request_id in captured_request_ids)
    assert len(set(captured_request_ids)) == 2
    assert "outer-warmup-request" not in set(captured_request_ids)

    async with SessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(RequestLog).where(RequestLog.request_kind == "warmup").order_by(RequestLog.id.asc())
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 2
    logged_request_ids = [row.request_id for row in rows]
    assert len(set(logged_request_ids)) == 2
    assert set(logged_request_ids) == set(captured_request_ids)

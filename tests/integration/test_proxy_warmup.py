from __future__ import annotations

import asyncio
import base64
import json
from datetime import timedelta

import pytest
from sqlalchemy import select

import app.modules.proxy.service as proxy_module
from app.core.auth import generate_unique_account_id
from app.core.config.settings import get_settings
from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.core.openai.models import CompactResponsePayload
from app.core.utils.time import utcnow
from app.db.models import RequestLog
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
) -> tuple[str, str]:
    payload: dict[str, object] = {"name": name}
    if allowed_models is not None:
        payload["allowedModels"] = allowed_models
    if enforced_model is not None:
        payload["enforcedModel"] = enforced_model
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
async def test_warmup_default_mode_uses_configured_model_and_logs_warmup_kind(async_client, monkeypatch):
    _set_warmup_model_env(monkeypatch, "gpt-5.4-nano")
    await _enable_api_key_auth(async_client)
    eligible_id = await _import_account(async_client, "acc-warmup-eligible", "warmup-eligible@example.com")
    ineligible_id = await _import_account(async_client, "acc-warmup-ineligible", "warmup-ineligible@example.com")
    await _add_primary_usage(eligible_id, used_percent=0.0, window_minutes=300)
    await _add_primary_usage(ineligible_id, used_percent=11.0, window_minutes=300)

    _, key = await _create_api_key(async_client, name="warmup-default")
    captured_models: list[str] = []
    _install_successful_warmup_stub(monkeypatch, captured_models)

    response = await async_client.post(
        "/v1/warmup",
        headers={"Authorization": f"Bearer {key}"},
        json={"mode": "default"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "default"
    assert payload["total_accounts"] == 2
    assert len(payload["submitted"]) == 1
    assert payload["submitted"][0]["account_id"] == eligible_id
    assert payload["submitted"][0]["model"] == "gpt-5.4-nano"
    assert payload["skipped"] == [{"account_id": ineligible_id, "reason": "ineligible_primary_usage"}]
    assert payload["failed"] == []
    assert captured_models == ["gpt-5.4-nano"]

    async with SessionLocal() as session:
        rows = (await session.execute(select(RequestLog).order_by(RequestLog.id.asc()))).scalars().all()
    assert len(rows) == 1
    assert rows[0].request_kind == "warmup"
    assert rows[0].model == "gpt-5.4-nano"


@pytest.mark.asyncio
async def test_warmup_all_or_none_rejects_mixed_eligibility_without_upstream_calls(async_client, monkeypatch):
    await _enable_api_key_auth(async_client)
    eligible_id = await _import_account(async_client, "acc-all-or-none-eligible", "all-or-none-a@example.com")
    ineligible_id = await _import_account(async_client, "acc-all-or-none-ineligible", "all-or-none-b@example.com")
    await _add_primary_usage(eligible_id, used_percent=0.0, window_minutes=300)
    await _add_primary_usage(ineligible_id, used_percent=5.0, window_minutes=300)

    _, key = await _create_api_key(async_client, name="warmup-all-or-none")
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
        json={"mode": "all-or-none"},
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
        json={"mode": "force-update"},
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
        json={"mode": "default"},
    )

    assert response.status_code == 403
    assert called is False


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
        json={"mode": "force-update"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_accounts"] == total_accounts
    assert len(payload["submitted"]) == total_accounts
    assert payload["failed"] == []
    assert peak_compact_calls == 5


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
        json={"mode": "force-update"},
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

from __future__ import annotations

import base64
import json
from datetime import timezone

import pytest

import app.modules.proxy.service as proxy_module
from app.core.crypto import TokenEncryptor
from app.core.openai.models import OpenAIResponsePayload
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration


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
    payload = response.json()
    return payload["accountId"]


async def _set_routing_settings(
    async_client,
    *,
    sticky_threads_enabled: bool,
    prefer_earlier_reset_accounts: bool = False,
) -> None:
    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": sticky_threads_enabled,
            "preferEarlierResetAccounts": prefer_earlier_reset_accounts,
        },
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_proxy_sticky_prompt_cache_key_pins_account(async_client, codex_auth_headers, monkeypatch):
    await _set_routing_settings(async_client, sticky_threads_enabled=True)
    acc_a_id = await _import_account(async_client, "acc_a", "a@example.com")
    acc_b_id = await _import_account(async_client, "acc_b", "b@example.com")

    seen: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen.append(account_id)
        yield 'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=acc_a_id,
            used_percent=10.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=acc_b_id,
            used_percent=20.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )

    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "stream": True,
        "prompt_cache_key": "thread_123",
    }

    response = await async_client.post("/backend-api/codex/responses", headers=codex_auth_headers, json=payload)
    assert response.status_code == 200

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=acc_a_id,
            used_percent=95.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=acc_b_id,
            used_percent=5.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )

    response = await async_client.post("/backend-api/codex/responses", headers=codex_auth_headers, json=payload)
    assert response.status_code == 200

    assert seen == ["acc_a", "acc_a"]


@pytest.mark.asyncio
async def test_proxy_sticky_switches_when_pinned_rate_limited(async_client, codex_auth_headers, monkeypatch):
    await _set_routing_settings(async_client, sticky_threads_enabled=True)
    session_response = await async_client.get("/api/dashboard-auth/session")
    assert session_response.status_code == 200
    user = session_response.json().get("user")
    assert isinstance(user, dict)

    encryptor = TokenEncryptor()
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())

    acc_a = Account(
        id="acc_rl_a",
        chatgpt_account_id="acc_rl_a",
        email="rl_a@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-a"),
        refresh_token_encrypted=encryptor.encrypt("refresh-a"),
        id_token_encrypted=encryptor.encrypt("id-a"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
        owner_user_id=user["id"],
    )
    acc_b = Account(
        id="acc_rl_b",
        chatgpt_account_id="acc_rl_b",
        email="rl_b@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-b"),
        refresh_token_encrypted=encryptor.encrypt("refresh-b"),
        id_token_encrypted=encryptor.encrypt("id-b"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
        owner_user_id=user["id"],
    )

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        await accounts_repo.upsert(acc_a)
        await accounts_repo.upsert(acc_b)
        await usage_repo.add_entry(
            account_id=acc_a.id,
            used_percent=10.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=acc_b.id,
            used_percent=20.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )

    seen: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        seen.append(account_id)
        if account_id == acc_a.id:
            yield (
                'data: {"type":"response.failed","response":{"error":{"code":"rate_limit_exceeded",'
                '"message":"slow down"}}}\n\n'
            )
            return
        yield 'data: {"type":"response.completed","response":{"id":"resp_ok"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "stream": True,
        "prompt_cache_key": "thread_rl",
    }
    response = await async_client.post("/backend-api/codex/responses", headers=codex_auth_headers, json=payload)
    assert response.status_code == 200

    # First attempt is pinned acc_a, which rate limits; retry should switch to acc_b and update stickiness.
    assert seen[:2] == [acc_a.id, acc_b.id]


@pytest.mark.asyncio
async def test_proxy_compact_reallocates_sticky_mapping(async_client, codex_auth_headers, monkeypatch):
    await _set_routing_settings(async_client, sticky_threads_enabled=True)
    acc_c1_id = await _import_account(async_client, "acc_c1", "c1@example.com")
    acc_c2_id = await _import_account(async_client, "acc_c2", "c2@example.com")

    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=acc_c1_id,
            used_percent=10.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=acc_c2_id,
            used_percent=20.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )

    stream_seen: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False):
        stream_seen.append(account_id)
        yield 'data: {"type":"response.completed","response":{"id":"resp_1"}}\n\n'

    compact_seen: list[str] = []

    async def fake_compact(payload, headers, access_token, account_id):
        compact_seen.append(account_id)
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    thread_key = "thread_compact_1"
    stream_payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "stream": True,
        "prompt_cache_key": thread_key,
    }
    response = await async_client.post("/backend-api/codex/responses", headers=codex_auth_headers, json=stream_payload)
    assert response.status_code == 200
    assert stream_seen == ["acc_c1"]

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=acc_c1_id,
            used_percent=90.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=acc_c2_id,
            used_percent=5.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )

    compact_payload = {
        "model": "gpt-5.1",
        "instructions": "summarize",
        "input": [],
        "prompt_cache_key": thread_key,  # extra field accepted by ResponsesCompactRequest (extra="allow")
    }
    response = await async_client.post(
        "/backend-api/codex/responses/compact",
        headers=codex_auth_headers,
        json=compact_payload,
    )
    assert response.status_code == 200
    assert compact_seen == ["acc_c2"]

    response = await async_client.post("/backend-api/codex/responses", headers=codex_auth_headers, json=stream_payload)
    assert response.status_code == 200
    assert stream_seen == ["acc_c1", "acc_c2"]

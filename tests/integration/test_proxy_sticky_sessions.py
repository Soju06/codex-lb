from __future__ import annotations

import base64
import json
from datetime import timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import text

import app.modules.proxy.service as proxy_module
from app.core.crypto import TokenEncryptor
from app.core.openai.models import OpenAIResponsePayload
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration


class _SettingsCache:
    def __init__(self, settings: object) -> None:
        self._settings = settings

    async def get(self) -> object:
        return self._settings


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
    routing_strategy: str = "usage_weighted",
) -> None:
    response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": sticky_threads_enabled,
            "preferEarlierResetAccounts": prefer_earlier_reset_accounts,
            "routingStrategy": routing_strategy,
        },
    )
    assert response.status_code == 200


def _install_proxy_settings_cache(
    monkeypatch: pytest.MonkeyPatch,
    *,
    sticky_threads_enabled: bool,
    prefer_earlier_reset_accounts: bool = False,
    openai_cache_affinity_max_age_seconds: int = 300,
    sticky_reallocation_budget_threshold_pct: float = 95.0,
    openai_prompt_cache_key_derivation_enabled: bool = True,
) -> None:
    settings = SimpleNamespace(
        prefer_earlier_reset_accounts=prefer_earlier_reset_accounts,
        sticky_threads_enabled=sticky_threads_enabled,
        openai_cache_affinity_max_age_seconds=openai_cache_affinity_max_age_seconds,
        sticky_reallocation_budget_threshold_pct=sticky_reallocation_budget_threshold_pct,
        openai_prompt_cache_key_derivation_enabled=openai_prompt_cache_key_derivation_enabled,
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
        http_responses_session_bridge_enabled=False,
        http_responses_session_bridge_idle_ttl_seconds=120.0,
        http_responses_session_bridge_max_sessions=128,
        http_responses_session_bridge_queue_limit=8,
    )
    monkeypatch.setattr(proxy_module, "get_settings_cache", lambda: _SettingsCache(settings))
    monkeypatch.setattr(proxy_module, "get_settings", lambda: settings)


@pytest.mark.asyncio
async def test_proxy_stream_sticky_threads_reallocate_by_prompt_cache_key(async_client, monkeypatch):
    await _set_routing_settings(async_client, sticky_threads_enabled=True)
    acc_a_id = await _import_account(async_client, "acc_a", "a@example.com")
    acc_b_id = await _import_account(async_client, "acc_b", "b@example.com")

    seen: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kwargs):
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

    response = await async_client.post("/backend-api/codex/responses", json=payload)
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

    response = await async_client.post("/backend-api/codex/responses", json=payload)
    assert response.status_code == 200

    assert seen == ["acc_a", "acc_a"]


@pytest.mark.asyncio
async def test_proxy_sticky_switches_when_pinned_rate_limited(async_client, monkeypatch):
    await _set_routing_settings(async_client, sticky_threads_enabled=True)
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

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kwargs):
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
    response = await async_client.post("/backend-api/codex/responses", json=payload)
    assert response.status_code == 200

    # First attempt is pinned acc_a, which rate limits; retry should switch to acc_b and update stickiness.
    assert seen[:2] == [acc_a.id, acc_b.id]


@pytest.mark.asyncio
async def test_proxy_compact_reallocates_sticky_mapping(async_client, monkeypatch):
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

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kwargs):
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
    response = await async_client.post("/backend-api/codex/responses", json=stream_payload)
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
    response = await async_client.post("/backend-api/codex/responses/compact", json=compact_payload)
    assert response.status_code == 200
    assert compact_seen == ["acc_c1"]

    response = await async_client.post("/backend-api/codex/responses", json=stream_payload)
    assert response.status_code == 200
    assert stream_seen == ["acc_c1", "acc_c1"]


@pytest.mark.asyncio
async def test_proxy_codex_session_id_pins_responses_and_compact_without_sticky_threads(async_client, monkeypatch):
    await _set_routing_settings(async_client, sticky_threads_enabled=False)
    acc_a_id = await _import_account(async_client, "acc_sid_a", "sid_a@example.com")
    acc_b_id = await _import_account(async_client, "acc_sid_b", "sid_b@example.com")

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

    stream_seen: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kwargs):
        stream_seen.append(account_id)
        yield 'data: {"type":"response.completed","response":{"id":"resp_session"}}\n\n'

    compact_seen: list[str] = []

    async def fake_compact(payload, headers, access_token, account_id):
        compact_seen.append(account_id)
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    headers = {"session_id": "codex-thread-123"}
    stream_payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "stream": True,
    }
    response = await async_client.post("/backend-api/codex/responses", json=stream_payload, headers=headers)
    assert response.status_code == 200
    assert stream_seen == ["acc_sid_a"]

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

    compact_payload = {
        "model": "gpt-5.1",
        "instructions": "summarize",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
    }
    response = await async_client.post(
        "/backend-api/codex/responses/compact",
        json=compact_payload,
        headers=headers,
    )
    assert response.status_code == 200
    assert compact_seen == ["acc_sid_a"]

    response = await async_client.post("/backend-api/codex/responses", json=stream_payload, headers=headers)
    assert response.status_code == 200
    assert stream_seen == ["acc_sid_a", "acc_sid_a"]


@pytest.mark.asyncio
async def test_proxy_codex_session_id_compact_first_pins_followup_stream_without_sticky_threads(
    async_client,
    monkeypatch,
):
    await _set_routing_settings(async_client, sticky_threads_enabled=False)
    acc_a_id = await _import_account(async_client, "acc_sid_compact_a", "sid_compact_a@example.com")
    acc_b_id = await _import_account(async_client, "acc_sid_compact_b", "sid_compact_b@example.com")

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

    compact_seen: list[str] = []

    async def fake_compact(payload, headers, access_token, account_id):
        compact_seen.append(account_id)
        return OpenAIResponsePayload.model_validate({"output": []})

    stream_seen: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kwargs):
        stream_seen.append(account_id)
        yield 'data: {"type":"response.completed","response":{"id":"resp_compact_first"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)
    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    headers = {"session_id": "codex-compact-first-123"}
    compact_payload = {
        "model": "gpt-5.1",
        "instructions": "summarize",
        "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
    }
    response = await async_client.post(
        "/backend-api/codex/responses/compact",
        json=compact_payload,
        headers=headers,
    )
    assert response.status_code == 200
    assert compact_seen == ["acc_sid_compact_a"]

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

    stream_payload = {
        "model": "gpt-5.1",
        "instructions": "continue",
        "input": [],
        "stream": True,
    }
    response = await async_client.post("/backend-api/codex/responses", json=stream_payload, headers=headers)
    assert response.status_code == 200
    assert stream_seen == ["acc_sid_compact_a"]


@pytest.mark.asyncio
async def test_proxy_codex_session_id_switches_when_pinned_rate_limited(async_client, monkeypatch):
    await _set_routing_settings(async_client, sticky_threads_enabled=False)
    acc_a_id = await _import_account(async_client, "acc_sid_retry_a", "sid_retry_a@example.com")
    acc_b_id = await _import_account(async_client, "acc_sid_retry_b", "sid_retry_b@example.com")
    upstream_acc_a = "acc_sid_retry_a"
    upstream_acc_b = "acc_sid_retry_b"

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

    stream_seen: list[str] = []
    fail_pinned = {"enabled": False}

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kwargs):
        stream_seen.append(account_id)
        if account_id == upstream_acc_a and fail_pinned["enabled"]:
            yield (
                'data: {"type":"response.failed","response":{"error":{"code":"rate_limit_exceeded",'
                '"message":"slow down"}}}\n\n'
            )
            return
        yield 'data: {"type":"response.completed","response":{"id":"resp_session_retry"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    headers = {"session_id": "codex-session-retry-123"}
    stream_payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "stream": True,
    }
    response = await async_client.post("/backend-api/codex/responses", json=stream_payload, headers=headers)
    assert response.status_code == 200
    assert stream_seen == [upstream_acc_a]

    fail_pinned["enabled"] = True
    response = await async_client.post("/backend-api/codex/responses", json=stream_payload, headers=headers)
    assert response.status_code == 200

    response = await async_client.post("/backend-api/codex/responses", json=stream_payload, headers=headers)
    assert response.status_code == 200
    assert stream_seen == [upstream_acc_a, upstream_acc_a, upstream_acc_b, upstream_acc_b]


@pytest.mark.asyncio
async def test_v1_session_id_does_not_create_durable_codex_session_affinity(async_client, monkeypatch):
    await _set_routing_settings(async_client, sticky_threads_enabled=False)
    acc_a_id = await _import_account(async_client, "acc_v1_sid_a", "v1_sid_a@example.com")
    acc_b_id = await _import_account(async_client, "acc_v1_sid_b", "v1_sid_b@example.com")

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

    stream_seen: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kwargs):
        stream_seen.append(account_id)
        yield 'data: {"type":"response.completed","response":{"id":"resp_v1_session"}}\n\n'

    compact_seen: list[str] = []

    async def fake_compact(payload, headers, access_token, account_id):
        compact_seen.append(account_id)
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    headers = {"session_id": "v1-thread-123"}
    stream_payload = {
        "model": "gpt-5.1",
        "input": "hello",
        "stream": True,
    }
    response = await async_client.post("/v1/responses", json=stream_payload, headers=headers)
    assert response.status_code == 200
    assert stream_seen == ["acc_v1_sid_a"]

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

    compact_payload = {
        "model": "gpt-5.1",
        "input": "hello",
    }
    response = await async_client.post("/v1/responses/compact", json=compact_payload, headers=headers)
    assert response.status_code == 200
    assert compact_seen == ["acc_v1_sid_a"]

    async with SessionLocal() as session:
        codex_row = (
            await session.execute(
                text("SELECT kind FROM sticky_sessions WHERE key = :key"),
                {"key": "v1-thread-123"},
            )
        ).fetchone()
        assert codex_row is None


@pytest.mark.asyncio
async def test_v1_prompt_cache_key_reuses_recent_responses_and_compact_without_sticky_threads(
    async_client,
    monkeypatch,
):
    await _set_routing_settings(async_client, sticky_threads_enabled=False)
    _install_proxy_settings_cache(
        monkeypatch,
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=60,
    )
    acc_a_id = await _import_account(async_client, "acc_v1_cache_a", "v1_cache_a@example.com")
    acc_b_id = await _import_account(async_client, "acc_v1_cache_b", "v1_cache_b@example.com")

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

    stream_seen: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kwargs):
        stream_seen.append(account_id)
        yield 'data: {"type":"response.completed","response":{"id":"resp_v1_cache"}}\n\n'

    compact_seen: list[str] = []

    async def fake_compact(payload, headers, access_token, account_id):
        compact_seen.append(account_id)
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    thread_key = "v1-cache-thread-123"
    stream_payload = {
        "model": "gpt-5.1",
        "input": "hello",
        "stream": True,
        "prompt_cache_key": thread_key,
    }
    response = await async_client.post("/v1/responses", json=stream_payload)
    assert response.status_code == 200
    assert stream_seen == ["acc_v1_cache_a"]

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

    compact_payload = {
        "model": "gpt-5.1",
        "input": "hello",
        "prompt_cache_key": thread_key,
    }
    response = await async_client.post("/v1/responses/compact", json=compact_payload)
    assert response.status_code == 200
    assert compact_seen == ["acc_v1_cache_a"]

    response = await async_client.post("/v1/responses", json=stream_payload)
    assert response.status_code == 200
    assert stream_seen == ["acc_v1_cache_a", "acc_v1_cache_a"]


@pytest.mark.asyncio
async def test_v1_responses_derives_prompt_cache_key_when_absent(async_client, monkeypatch):
    _install_proxy_settings_cache(
        monkeypatch,
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=60,
    )
    acc_a_id = await _import_account(async_client, "acc_v1_derived_a", "v1_derived_a@example.com")
    acc_b_id = await _import_account(async_client, "acc_v1_derived_b", "v1_derived_b@example.com")

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

    seen_keys: list[str | None] = []
    seen_accounts: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        seen_accounts.append(account_id)
        seen_keys.append(getattr(payload, "prompt_cache_key", None))
        yield 'data: {"type":"response.completed","response":{"id":"resp_v1_derived"}}\\n\\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "input": "hello", "stream": True}
    response = await async_client.post("/v1/responses", json=payload)
    assert response.status_code == 200
    assert seen_accounts == ["acc_v1_derived_a"]
    assert isinstance(seen_keys[0], str)
    assert seen_keys[0]


@pytest.mark.asyncio
async def test_backend_codex_session_affinity_also_forwards_prompt_cache_key_when_missing(async_client, monkeypatch):
    _install_proxy_settings_cache(monkeypatch, sticky_threads_enabled=False)
    acc_id = await _import_account(async_client, "acc_codex_sid_1", "codex_sid_1@example.com")

    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=acc_id,
            used_percent=10.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )

    seen_keys: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        seen_keys.append(getattr(payload, "prompt_cache_key", None))
        yield 'data: {"type":"response.completed","response":{"id":"resp_backend_codex"}}\\n\\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    response = await async_client.post(
        "/backend-api/codex/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "hi",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
            "stream": True,
        },
        headers={"session_id": "backend-thread-123"},
    )
    assert response.status_code == 200
    assert isinstance(seen_keys[0], str)
    assert seen_keys[0]

    async with SessionLocal() as session:
        row = (
            await session.execute(
                text("SELECT kind FROM sticky_sessions WHERE key = :key"),
                {"key": "backend-thread-123"},
            )
        ).fetchone()
        assert row is not None
        assert row[0] == "codex_session"


@pytest.mark.asyncio
async def test_backend_responses_http_forwards_previous_response_id(async_client, monkeypatch):
    _install_proxy_settings_cache(monkeypatch, sticky_threads_enabled=False)
    acc_id = await _import_account(async_client, "acc_prev_http_1", "prev_http_1@example.com")

    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=acc_id,
            used_percent=10.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )

    seen_prev_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        del headers, access_token, account_id, base_url, raise_for_status, _kw
        seen_prev_ids.append(getattr(payload, "previous_response_id", None))
        yield 'data: {"type":"response.completed","response":{"id":"resp_prev_http"}}\\n\\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    response = await async_client.post(
        "/backend-api/codex/responses",
        json={
            "model": "gpt-5.1",
            "instructions": "hi",
            "previous_response_id": "resp_prev_http_123",
            "input": [{"role": "user", "content": [{"type": "input_text", "text": "continue"}]}],
            "stream": True,
        },
        headers={"session_id": "backend-thread-prev-http-123"},
    )
    assert response.status_code == 200
    assert seen_prev_ids == ["resp_prev_http_123"]


@pytest.mark.asyncio
async def test_v1_responses_http_forwards_previous_response_id(async_client, monkeypatch):
    _install_proxy_settings_cache(monkeypatch, sticky_threads_enabled=False)
    acc_id = await _import_account(async_client, "acc_v1_prev_http_1", "v1_prev_http_1@example.com")

    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=acc_id,
            used_percent=10.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )

    seen_prev_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        del headers, access_token, account_id, base_url, raise_for_status, _kw
        seen_prev_ids.append(getattr(payload, "previous_response_id", None))
        yield 'data: {"type":"response.completed","response":{"id":"resp_v1_prev_http"}}\\n\\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    response = await async_client.post(
        "/v1/responses",
        json={
            "model": "gpt-5.1",
            "input": "continue",
            "previous_response_id": "resp_prev_v1_http_123",
            "stream": True,
        },
    )
    assert response.status_code == 200
    assert seen_prev_ids == ["resp_prev_v1_http_123"]


@pytest.mark.asyncio
async def test_v1_prompt_cache_key_rebalances_after_affinity_expires(async_client, monkeypatch):
    await _set_routing_settings(async_client, sticky_threads_enabled=False)
    _install_proxy_settings_cache(
        monkeypatch,
        sticky_threads_enabled=False,
        openai_cache_affinity_max_age_seconds=60,
    )
    acc_a_id = await _import_account(async_client, "acc_v1_expire_a", "v1_expire_a@example.com")
    acc_b_id = await _import_account(async_client, "acc_v1_expire_b", "v1_expire_b@example.com")

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

    stream_seen: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kwargs):
        stream_seen.append(account_id)
        yield 'data: {"type":"response.completed","response":{"id":"resp_v1_cache_expire"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    thread_key = "v1-cache-expire-thread-123"
    stream_payload = {
        "model": "gpt-5.1",
        "input": "hello",
        "stream": True,
        "prompt_cache_key": thread_key,
    }
    response = await async_client.post("/v1/responses", json=stream_payload)
    assert response.status_code == 200
    assert stream_seen == ["acc_v1_expire_a"]

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
        stale_updated_at = utcnow() - timedelta(minutes=10)
        await session.execute(
            text(
                """
                UPDATE sticky_sessions
                SET updated_at = :stale_updated_at
                WHERE key = :sticky_key AND kind = 'prompt_cache'
                """
            ),
            {"sticky_key": thread_key, "stale_updated_at": stale_updated_at},
        )
        await session.commit()

    response = await async_client.post("/v1/responses", json=stream_payload)
    assert response.status_code == 200
    assert stream_seen == ["acc_v1_expire_a", "acc_v1_expire_b"]


@pytest.mark.asyncio
async def test_codex_endpoint_uses_prompt_cache_sticky_kind(async_client, monkeypatch):
    await _set_routing_settings(async_client, sticky_threads_enabled=True)
    acc_id = await _import_account(async_client, "acc_kind_a", "kind_a@example.com")

    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=acc_id,
            used_percent=10.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )

    seen: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        seen.append(account_id)
        yield 'data: {"type":"response.completed","response":{"id":"resp_k"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True, "prompt_cache_key": "pck_abc"}
    await async_client.post("/backend-api/codex/responses", json=payload)
    assert seen == ["acc_kind_a"]

    async with SessionLocal() as session:
        row = (await session.execute(text("SELECT kind FROM sticky_sessions WHERE key = 'pck_abc'"))).fetchone()
        assert row is not None
        assert row[0] == "prompt_cache"


@pytest.mark.asyncio
async def test_v1_auto_derived_key_separates_parallel_sessions(async_client, monkeypatch):
    _install_proxy_settings_cache(monkeypatch, sticky_threads_enabled=False)
    acc_a_id = await _import_account(async_client, "acc_par_a", "par_a@example.com")
    acc_b_id = await _import_account(async_client, "acc_par_b", "par_b@example.com")

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

    seen: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        seen.append(account_id)
        yield 'data: {"type":"response.completed","response":{"id":"resp_p"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    session_a = {"model": "gpt-5.1", "input": "build a server", "stream": True}
    session_b = {"model": "gpt-5.1", "input": "write tests", "stream": True}

    await async_client.post("/v1/responses", json=session_a)
    await async_client.post("/v1/responses", json=session_b)

    assert len(seen) == 2
    assert seen[0] == "acc_par_a"


@pytest.mark.asyncio
async def test_v1_auto_derived_key_stable_across_turns(async_client, monkeypatch):
    _install_proxy_settings_cache(monkeypatch, sticky_threads_enabled=False)
    acc_a_id = await _import_account(async_client, "acc_turn_a", "turn_a@example.com")
    acc_b_id = await _import_account(async_client, "acc_turn_b", "turn_b@example.com")

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

    seen: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        seen.append(account_id)
        yield 'data: {"type":"response.completed","response":{"id":"resp_t"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    turn1 = {
        "model": "gpt-5.1",
        "input": [{"role": "user", "content": "build a server"}],
        "stream": True,
    }
    turn2 = {
        "model": "gpt-5.1",
        "input": [
            {"role": "user", "content": "build a server"},
            {"role": "assistant", "content": "Here is a server..."},
            {"role": "user", "content": "add logging"},
        ],
        "stream": True,
    }

    await async_client.post("/v1/responses", json=turn1)
    assert seen == ["acc_turn_a"]

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=acc_a_id,
            used_percent=90.0,
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

    await async_client.post("/v1/responses", json=turn2)

    assert seen == ["acc_turn_a", "acc_turn_a"]


@pytest.mark.asyncio
async def test_reallocate_sticky_respects_existing_session_then_falls_back(async_client, monkeypatch):
    await _set_routing_settings(async_client, sticky_threads_enabled=True)
    acc_a_id = await _import_account(async_client, "acc_realloc_a", "realloc_a@example.com")
    acc_b_id = await _import_account(async_client, "acc_realloc_b", "realloc_b@example.com")

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

    seen: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **_kw):
        seen.append(account_id)
        yield 'data: {"type":"response.completed","response":{"id":"resp_r"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True, "prompt_cache_key": "realloc_key"}
    await async_client.post("/backend-api/codex/responses", json=payload)
    assert seen == ["acc_realloc_a"]

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

    await async_client.post("/backend-api/codex/responses", json=payload)
    assert seen == ["acc_realloc_a", "acc_realloc_a"]

    async with SessionLocal() as session:
        await session.execute(text("DELETE FROM accounts WHERE chatgpt_account_id = 'acc_realloc_a'"))
        await session.commit()

    await async_client.post("/backend-api/codex/responses", json=payload)
    assert len(seen) == 3
    assert seen[2] == "acc_realloc_b"

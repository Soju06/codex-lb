from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy import text, update

import app.modules.proxy.service as proxy_module
from app.core.crypto import TokenEncryptor
from app.core.openai.models import OpenAIResponsePayload
from app.core.utils.time import naive_utc_to_epoch, utcnow
from app.db.models import Account, AccountStatus, StickySessionKind
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.api_keys.service import ApiKeyCreateData, ApiKeyData, ApiKeysService
from app.modules.proxy._service.realtime_live import (
    _REALTIME_CALL_AFFINITY_MAX_AGE_SECONDS,
    realtime_call_affinity_key,
)
from app.modules.proxy.affinity import _codex_backend_identity, _codex_session_selection_key
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
    proxy_request_budget_seconds: float = 75.0,
) -> None:
    settings = SimpleNamespace(
        prefer_earlier_reset_accounts=prefer_earlier_reset_accounts,
        sticky_threads_enabled=sticky_threads_enabled,
        openai_cache_affinity_max_age_seconds=openai_cache_affinity_max_age_seconds,
        sticky_reallocation_budget_threshold_pct=sticky_reallocation_budget_threshold_pct,
        openai_prompt_cache_key_derivation_enabled=openai_prompt_cache_key_derivation_enabled,
        routing_strategy="usage_weighted",
        proxy_request_budget_seconds=proxy_request_budget_seconds,
        compact_request_budget_seconds=75.0,
        transcription_request_budget_seconds=120.0,
        upstream_compact_timeout_seconds=None,
        upstream_stream_transport="auto",
        trace_channels=frozenset(),
        http_responses_session_bridge_enabled=False,
        http_responses_session_bridge_instance_id="sticky-session-test",
        http_responses_session_bridge_idle_ttl_seconds=120.0,
        http_responses_session_bridge_codex_idle_ttl_seconds=900.0,
        http_responses_session_bridge_max_sessions=128,
        http_responses_session_bridge_queue_limit=8,
        http_responses_session_bridge_prompt_cache_idle_ttl_seconds=3600,
        http_responses_session_bridge_gateway_safe_mode=False,
        proxy_token_refresh_limit=32,
        proxy_upstream_websocket_connect_limit=64,
        proxy_account_stream_recovery_reserve=1,
        proxy_api_key_fair_share_congestion_threshold_pct=0,
        proxy_response_create_limit=64,
        proxy_compact_response_create_limit=16,
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
async def test_proxy_stream_bare_session_spills_under_cap_without_rebinding(async_client, monkeypatch):
    from app.dependencies import get_proxy_service_for_app
    from app.modules.proxy.sticky_repository import StickySessionsRepository

    _install_proxy_settings_cache(monkeypatch, sticky_threads_enabled=False)
    owner_id = await _import_account(async_client, "acc_session_cap_owner", "session-cap-owner@example.com")
    await _import_account(async_client, "acc_session_cap_alternate", "session-cap-alternate@example.com")
    raw_session = "session-cap-spill"
    selection_key = _codex_session_selection_key(raw_session)

    async with SessionLocal() as session:
        await StickySessionsRepository(session).upsert(
            selection_key,
            owner_id,
            kind=StickySessionKind.CODEX_SESSION,
        )

    service = get_proxy_service_for_app(async_client._transport.app)
    saturated_leases = [await service._load_balancer.acquire_account_lease(owner_id, kind="stream") for _ in range(8)]
    assert all(lease is not None for lease in saturated_leases)
    seen: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, base_url, raise_for_status, kwargs
        seen.append(account_id)
        yield 'data: {"type":"response.completed","response":{"id":"resp_spill"}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    try:
        response = await async_client.post(
            "/backend-api/codex/responses",
            headers={"x-codex-session-id": raw_session},
            json={"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True},
        )
    finally:
        for lease in saturated_leases:
            await service._load_balancer.release_account_lease(lease)

    assert response.status_code == 200
    assert seen == ["acc_session_cap_alternate"]
    async with SessionLocal() as session:
        mapped_account_id = await StickySessionsRepository(session).get_account_id(
            selection_key,
            kind=StickySessionKind.CODEX_SESSION,
        )
    assert mapped_account_id == owner_id


@pytest.mark.asyncio
async def test_codex_goal_restart_retires_unavailable_legacy_owner_and_stays_on_replacement(
    async_client,
    monkeypatch,
):
    from sqlalchemy import select

    from app.db.models import StickySession
    from app.modules.proxy.sticky_repository import StickySessionsRepository

    _install_proxy_settings_cache(monkeypatch, sticky_threads_enabled=False)
    owner_id = await _import_account(async_client, "acc_goal_restart_owner", "goal-restart-owner@example.com")
    replacement_id = await _import_account(
        async_client,
        "acc_goal_restart_replacement",
        "goal-restart-replacement@example.com",
    )
    raw_session = "goal-restart-session"
    selection_key = _codex_session_selection_key(raw_session)

    now_epoch = int(utcnow().replace(tzinfo=timezone.utc).timestamp())
    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=owner_id,
            used_percent=10.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=replacement_id,
            used_percent=20.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )
        await StickySessionsRepository(session).upsert(
            raw_session,
            owner_id,
            kind=StickySessionKind.CODEX_SESSION,
        )

    seen: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, base_url, raise_for_status, kwargs
        seen.append(account_id)
        yield f'data: {{"type":"response.completed","response":{{"id":"resp_goal_{len(seen)}"}}}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    headers = {"session_id": raw_session}
    restart_payload = {
        "model": "gpt-5.1",
        "instructions": "Continue the existing task.",
        "input": [
            {
                "role": "developer",
                "content": ('<codex_internal_context source="goal">\nContinue working toward the active thread goal.'),
            },
            {"role": "user", "content": [{"type": "input_text", "text": "continue"}]},
        ],
        "stream": True,
    }

    # The restart marker is not enough to move a healthy owner.
    healthy_response = await async_client.post(
        "/backend-api/codex/responses",
        json=restart_payload,
        headers=headers,
    )
    assert healthy_response.status_code == 200
    assert seen == ["acc_goal_restart_owner"]

    async with SessionLocal() as session:
        await session.execute(update(Account).where(Account.id == owner_id).values(status=AccountStatus.QUOTA_EXCEEDED))
        await session.commit()

    restart_response = await async_client.post(
        "/backend-api/codex/responses",
        json=restart_payload,
        headers=headers,
    )
    assert restart_response.status_code == 200
    assert seen == ["acc_goal_restart_owner", "acc_goal_restart_replacement"]

    # A later ordinary turn uses the replacement's namespaced session affinity.
    follow_up_response = await async_client.post(
        "/backend-api/codex/responses",
        json={"model": "gpt-5.1", "instructions": "continue", "input": [], "stream": True},
        headers=headers,
    )
    assert follow_up_response.status_code == 200
    assert seen == [
        "acc_goal_restart_owner",
        "acc_goal_restart_replacement",
        "acc_goal_restart_replacement",
    ]

    # The raw compatibility text can also be a different client's explicit
    # turn state. Session-header abandonment must not erase that hard owner or
    # dispatch the turn-state continuation on the replacement account.
    turn_state_response = await async_client.post(
        "/backend-api/codex/responses",
        json={"model": "gpt-5.1", "instructions": "continue", "input": [], "stream": True},
        headers={"x-codex-turn-state": raw_session},
    )
    assert turn_state_response.status_code == 502
    assert turn_state_response.json()["error"]["code"] == "turn_state_owner_unavailable"
    assert seen == [
        "acc_goal_restart_owner",
        "acc_goal_restart_replacement",
        "acc_goal_restart_replacement",
    ]

    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        rows = {
            row.key: row
            for row in (
                await session.execute(
                    select(StickySession).where(
                        StickySession.key.in_((raw_session, selection_key)),
                        StickySession.kind == StickySessionKind.CODEX_SESSION,
                    )
                )
            ).scalars()
        }
        session_header_lookup = await repo.get_account_id_and_abandonment(
            raw_session,
            kind=StickySessionKind.CODEX_SESSION,
            continuity_source="session_header",
        )
        turn_state_owner = await repo.get_account_id(
            raw_session,
            kind=StickySessionKind.CODEX_SESSION,
            continuity_source="turn_state",
        )
    assert rows[raw_session].account_id == owner_id
    assert rows[raw_session].continuity_abandoned_at is None
    assert rows[raw_session].continuity_abandonment_scope == "session_header"
    assert rows[selection_key].account_id == replacement_id
    assert rows[selection_key].continuity_abandoned_at is None
    assert rows[selection_key].continuity_abandonment_scope is None
    assert session_header_lookup.account_id is None
    assert session_header_lookup.continuity_abandoned is True
    assert session_header_lookup.abandoned_account_id == owner_id
    assert turn_state_owner == owner_id
    # Parent-version readers know only the timestamp tombstone. Keeping it
    # NULL makes them fail closed on the retained owner during rollout.
    legacy_replica_owner = (
        None if rows[raw_session].continuity_abandoned_at is not None else rows[raw_session].account_id
    )
    assert legacy_replica_owner == owner_id


@pytest.mark.asyncio
async def test_codex_goal_restart_with_thread_id_retires_unavailable_legacy_owner_and_stays_on_replacement(
    async_client,
    monkeypatch,
):
    from sqlalchemy import select

    from app.db.models import StickySession
    from app.modules.proxy.sticky_repository import StickySessionsRepository

    _install_proxy_settings_cache(monkeypatch, sticky_threads_enabled=False)
    owner_id = await _import_account(
        async_client,
        "acc_goal_restart_thread_owner",
        "goal-restart-thread-owner@example.com",
    )
    replacement_id = await _import_account(
        async_client,
        "acc_goal_restart_thread_replacement",
        "goal-restart-thread-replacement@example.com",
    )
    raw_session = "goal-restart-thread-session"
    thread_id = "goal-restart-thread"
    headers = {"session_id": raw_session, "thread-id": thread_id}
    thread_key = _codex_backend_identity(headers).thread_selection_key
    assert thread_key is not None

    now_epoch = int(utcnow().replace(tzinfo=timezone.utc).timestamp())
    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=owner_id,
            used_percent=10.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=replacement_id,
            used_percent=20.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )
        await StickySessionsRepository(session).upsert(
            raw_session,
            owner_id,
            kind=StickySessionKind.CODEX_SESSION,
        )

    seen: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, base_url, raise_for_status, kwargs
        seen.append(account_id)
        yield f'data: {{"type":"response.completed","response":{{"id":"resp_goal_thread_{len(seen)}"}}}}\n\n'

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    restart_payload = {
        "model": "gpt-5.1",
        "instructions": "Continue the existing task.",
        "input": [
            {
                "role": "developer",
                "content": ('<codex_internal_context source="goal">\nContinue working toward the active thread goal.'),
            },
            {"role": "user", "content": [{"type": "input_text", "text": "continue"}]},
        ],
        "stream": True,
    }

    healthy_response = await async_client.post(
        "/backend-api/codex/responses",
        json=restart_payload,
        headers=headers,
    )
    assert healthy_response.status_code == 200
    assert seen == ["acc_goal_restart_thread_owner"]

    async with SessionLocal() as session:
        await session.execute(update(Account).where(Account.id == owner_id).values(status=AccountStatus.QUOTA_EXCEEDED))
        await session.commit()

    restart_response = await async_client.post(
        "/backend-api/codex/responses",
        json=restart_payload,
        headers=headers,
    )
    assert restart_response.status_code == 200
    assert seen == ["acc_goal_restart_thread_owner", "acc_goal_restart_thread_replacement"]

    follow_up_response = await async_client.post(
        "/backend-api/codex/responses",
        json={"model": "gpt-5.1", "instructions": "continue", "input": [], "stream": True},
        headers=headers,
    )
    assert follow_up_response.status_code == 200
    assert seen == [
        "acc_goal_restart_thread_owner",
        "acc_goal_restart_thread_replacement",
        "acc_goal_restart_thread_replacement",
    ]

    turn_state_response = await async_client.post(
        "/backend-api/codex/responses",
        json={"model": "gpt-5.1", "instructions": "continue", "input": [], "stream": True},
        headers={"x-codex-turn-state": raw_session},
    )
    assert turn_state_response.status_code == 502
    assert turn_state_response.json()["error"]["code"] == "turn_state_owner_unavailable"
    assert seen == [
        "acc_goal_restart_thread_owner",
        "acc_goal_restart_thread_replacement",
        "acc_goal_restart_thread_replacement",
    ]

    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        raw_row = (
            await session.execute(
                select(StickySession).where(
                    StickySession.key == raw_session,
                    StickySession.kind == StickySessionKind.CODEX_SESSION,
                )
            )
        ).scalar_one()
        thread_row = (
            await session.execute(
                select(StickySession).where(
                    StickySession.key == thread_key,
                    StickySession.kind == StickySessionKind.PROMPT_CACHE,
                )
            )
        ).scalar_one()
        session_header_lookup = await repo.get_account_id_and_abandonment(
            raw_session,
            kind=StickySessionKind.CODEX_SESSION,
            continuity_source="session_header",
        )
        thread_legacy_lookup = await repo.get_account_id_and_abandonment(
            raw_session,
            kind=StickySessionKind.CODEX_SESSION,
            continuity_source="thread_header",
        )
        turn_state_owner = await repo.get_account_id(
            raw_session,
            kind=StickySessionKind.CODEX_SESSION,
            continuity_source="turn_state",
        )
    assert raw_row.account_id == owner_id
    assert raw_row.continuity_abandonment_scope == "session_header"
    assert thread_row.account_id == replacement_id
    assert session_header_lookup.account_id is None
    assert session_header_lookup.continuity_abandoned is True
    assert thread_legacy_lookup.account_id == owner_id
    assert turn_state_owner == owner_id


@pytest.mark.asyncio
async def test_codex_goal_restart_cas_miss_reloads_concurrently_rebound_raw_owner(
    async_client,
    monkeypatch,
):
    from sqlalchemy import select

    from app.db.models import StickySession
    from app.modules.proxy.sticky_repository import StickySessionsRepository

    _install_proxy_settings_cache(monkeypatch, sticky_threads_enabled=False)
    stale_owner_id = await _import_account(
        async_client,
        "acc_goal_restart_stale_owner",
        "goal-restart-stale-owner@example.com",
    )
    rebound_owner_id = await _import_account(
        async_client,
        "acc_goal_restart_rebound_owner",
        "goal-restart-rebound-owner@example.com",
    )
    raw_session = "goal-restart-cas-reread"
    now_epoch = int(utcnow().replace(tzinfo=timezone.utc).timestamp())
    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        for account_id, used_percent in (
            (stale_owner_id, 10.0),
            (rebound_owner_id, 20.0),
        ):
            await usage_repo.add_entry(
                account_id=account_id,
                used_percent=used_percent,
                window="primary",
                reset_at=now_epoch + 3600,
                window_minutes=300,
            )
        await StickySessionsRepository(session).upsert(
            raw_session,
            stale_owner_id,
            kind=StickySessionKind.CODEX_SESSION,
        )
        await session.execute(
            update(Account).where(Account.id == stale_owner_id).values(status=AccountStatus.QUOTA_EXCEEDED)
        )
        await session.commit()

    original_tombstone = StickySessionsRepository.abandon_legacy_session_header_owner_if_unavailable
    race_count = 0

    async def rebind_before_tombstone(
        self,
        key: str,
        *,
        kind: StickySessionKind,
        expected_account_id: str,
    ) -> bool:
        nonlocal race_count
        if key == raw_session and race_count == 0:
            race_count += 1
            # Simulate another selector establishing a newer raw owner after
            # this request cached the stale owner but before its CAS executes.
            await self.upsert(key, rebound_owner_id, kind=kind)
        return await original_tombstone(
            self,
            key,
            kind=kind,
            expected_account_id=expected_account_id,
        )

    seen: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, base_url, raise_for_status, kwargs
        seen.append(account_id)
        yield 'data: {"type":"response.completed","response":{"id":"resp_goal_cas_reread"}}\n\n'

    monkeypatch.setattr(
        StickySessionsRepository,
        "abandon_legacy_session_header_owner_if_unavailable",
        rebind_before_tombstone,
    )
    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    response = await async_client.post(
        "/backend-api/codex/responses",
        headers={"session_id": raw_session},
        json={
            "model": "gpt-5.1",
            "instructions": "Continue the existing task.",
            "input": [
                {
                    "role": "developer",
                    "content": (
                        '<codex_internal_context source="goal">\nContinue working toward the active thread goal.'
                    ),
                },
                {"role": "user", "content": "continue"},
            ],
            "stream": True,
        },
    )

    assert response.status_code == 200
    assert race_count == 1
    assert seen == ["acc_goal_restart_rebound_owner"]
    async with SessionLocal() as session:
        raw_row = await session.scalar(
            select(StickySession).where(
                StickySession.key == raw_session,
                StickySession.kind == StickySessionKind.CODEX_SESSION,
            )
        )
    assert raw_row is not None
    assert raw_row.account_id == rebound_owner_id
    assert raw_row.continuity_abandoned_at is None


@pytest.mark.asyncio
async def test_codex_goal_restart_cannot_retire_owner_outside_api_key_scope(
    async_client,
    monkeypatch,
):
    from sqlalchemy import select

    from app.db.models import StickySession
    from app.modules.proxy.sticky_repository import StickySessionsRepository

    settings_response = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert settings_response.status_code == 200
    owner_id = await _import_account(
        async_client,
        "acc_goal_restart_out_of_scope_owner",
        "goal-restart-out-of-scope-owner@example.com",
    )
    replacement_id = await _import_account(
        async_client,
        "acc_goal_restart_scoped_replacement",
        "goal-restart-scoped-replacement@example.com",
    )
    raw_session = "goal-restart-scoped-session"
    now_epoch = int(utcnow().replace(tzinfo=timezone.utc).timestamp())
    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=replacement_id,
            used_percent=10.0,
            window="primary",
            reset_at=now_epoch + 3600,
            window_minutes=300,
        )
        await StickySessionsRepository(session).upsert(
            raw_session,
            owner_id,
            kind=StickySessionKind.CODEX_SESSION,
        )
        await session.execute(update(Account).where(Account.id == owner_id).values(status=AccountStatus.QUOTA_EXCEEDED))
        await session.commit()
    async with SessionLocal() as session:
        created_key = await ApiKeysService(ApiKeysRepository(session)).create_key(
            ApiKeyCreateData(
                name="goal restart scoped replacement",
                allowed_models=None,
                assigned_account_ids=[replacement_id],
            )
        )

    _install_proxy_settings_cache(
        monkeypatch,
        sticky_threads_enabled=False,
    )

    async def fail_stream(*args, **kwargs):
        del args, kwargs
        raise AssertionError("an out-of-scope owner must fail closed before upstream dispatch")
        if False:
            yield ""

    monkeypatch.setattr(proxy_module, "core_stream_responses", fail_stream)
    response = await async_client.post(
        "/backend-api/codex/responses",
        headers={
            "Authorization": f"Bearer {created_key.key}",
            "session_id": raw_session,
        },
        json={
            "model": "gpt-5.1",
            "instructions": "Continue the existing task.",
            "input": [
                {
                    "role": "developer",
                    "content": (
                        '<codex_internal_context source="goal">\nContinue working toward the active thread goal.'
                    ),
                },
                {"role": "user", "content": "continue"},
            ],
            "stream": True,
        },
    )

    assert response.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ") and line != "data: [DONE]"
    ]
    failed_event = next(event for event in events if event.get("type") == "response.failed")
    assert failed_event["response"]["error"]["code"] == "hard_affinity_saturated"
    async with SessionLocal() as session:
        raw_row = await session.scalar(
            select(StickySession).where(
                StickySession.key == raw_session,
                StickySession.kind == StickySessionKind.CODEX_SESSION,
            )
        )
    assert raw_row is not None
    assert raw_row.account_id == owner_id
    assert raw_row.continuity_abandoned_at is None


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

    headers = {"session-id": "codex-session-123", "thread-id": "codex-thread-123"}
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
async def test_backend_thread_rows_route_sibling_responses_and_compact_independently(
    async_client,
    monkeypatch,
):
    from app.modules.proxy.affinity import _codex_backend_identity
    from app.modules.proxy.sticky_repository import StickySessionsRepository

    await _set_routing_settings(async_client, sticky_threads_enabled=False)
    account_a_id = await _import_account(async_client, "acc_thread_route_a", "thread-route-a@example.com")
    account_b_id = await _import_account(async_client, "acc_thread_route_b", "thread-route-b@example.com")
    process_session = "process-thread-route-shared"
    root_headers = {"session-id": process_session, "thread-id": "thread-route-root"}
    child_headers = {"session-id": process_session, "thread-id": "thread-route-child"}
    root_key = _codex_backend_identity(root_headers).thread_selection_key
    child_key = _codex_backend_identity(child_headers).thread_selection_key
    assert root_key is not None
    assert child_key is not None

    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        await repo.upsert(root_key, account_a_id, kind=StickySessionKind.PROMPT_CACHE)
        await repo.upsert(child_key, account_b_id, kind=StickySessionKind.PROMPT_CACHE)

    observed: list[tuple[str, str, str | None]] = []

    async def fake_stream(payload, headers, access_token, account_id, **kwargs):
        del headers, access_token, kwargs
        observed.append(("responses", account_id, payload.prompt_cache_key))
        yield 'data: {"type":"response.completed","response":{"id":"resp_thread_route"}}\n\n'

    async def fake_compact(payload, headers, access_token, account_id):
        del headers, access_token
        observed.append(("compact", account_id, payload.prompt_cache_key))
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)
    payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "prompt_cache_key": process_session,
    }

    responses_response = await async_client.post(
        "/backend-api/codex/responses",
        json={**payload, "stream": True},
        headers=root_headers,
    )
    compact_response = await async_client.post(
        "/backend-api/codex/responses/compact",
        json=payload,
        headers=child_headers,
    )

    assert responses_response.status_code == 200
    assert compact_response.status_code == 200
    assert observed == [
        ("responses", "acc_thread_route_a", process_session),
        ("compact", "acc_thread_route_b", process_session),
    ]


@pytest.mark.asyncio
async def test_proxy_unregistered_turn_state_fails_closed_for_stream_and_compact(
    async_client,
    monkeypatch,
):
    await _set_routing_settings(async_client, sticky_threads_enabled=False)
    acc_a_id = await _import_account(async_client, "acc_turn_state_a", "turn_state_a@example.com")
    acc_b_id = await _import_account(async_client, "acc_turn_state_b", "turn_state_b@example.com")

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
        yield 'data: {"type":"response.completed","response":{"id":"resp_turn_state"}}\n\n'

    compact_seen: list[str] = []

    async def fake_compact(payload, headers, access_token, account_id):
        compact_seen.append(account_id)
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    headers = {"x-codex-turn-state": "codex-turn-state-123"}
    stream_payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "stream": True,
    }
    response = await async_client.post("/backend-api/codex/responses", json=stream_payload, headers=headers)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "turn_state_owner_unavailable"
    assert stream_seen == []

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
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "turn_state_owner_unavailable"
    assert compact_seen == []


@pytest.mark.asyncio
async def test_proxy_codex_session_id_reallocates_when_pinned_budget_exhausted(async_client, monkeypatch):
    await _set_routing_settings(async_client, sticky_threads_enabled=False)
    acc_a_id = await _import_account(async_client, "acc_sid_budget_a", "sid_budget_a@example.com")
    acc_b_id = await _import_account(async_client, "acc_sid_budget_b", "sid_budget_b@example.com")

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
        yield 'data: {"type":"response.completed","response":{"id":"resp_session_budget"}}\n\n'

    compact_seen: list[str] = []

    async def fake_compact(payload, headers, access_token, account_id):
        compact_seen.append(account_id)
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)
    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    headers = {"session_id": "codex-thread-budget"}
    stream_payload = {
        "model": "gpt-5.1",
        "instructions": "hi",
        "input": [],
        "stream": True,
    }
    response = await async_client.post("/backend-api/codex/responses", json=stream_payload, headers=headers)
    assert response.status_code == 200
    assert stream_seen == ["acc_sid_budget_a"]

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=acc_a_id,
            used_percent=99.0,
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
    assert compact_seen == ["acc_sid_budget_b"]

    response = await async_client.post("/backend-api/codex/responses", json=stream_payload, headers=headers)
    assert response.status_code == 200
    assert stream_seen == ["acc_sid_budget_a", "acc_sid_budget_b"]


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
                {"key": _codex_session_selection_key("backend-thread-123")},
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


@pytest.mark.asyncio
async def test_sticky_upsert_single_statement_insert_and_update(db_setup):
    """The upsert must persist and return the row with one data statement
    (RETURNING), for both the insert and the conflict-update arms."""
    from sqlalchemy import event

    from app.db.models import StickySessionKind
    from app.db.session import engine
    from app.modules.proxy.sticky_repository import StickySessionsRepository

    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(
            Account(
                id="acc_sticky_rt",
                email="sticky-rt@example.com",
                plan_type="plus",
                access_token_encrypted=encryptor.encrypt("access"),
                refresh_token_encrypted=encryptor.encrypt("refresh"),
                id_token_encrypted=encryptor.encrypt("id"),
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
                deactivation_reason=None,
            )
        )

    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        if "sticky_sessions" in statement:
            statements.append(statement)

    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        event.listen(engine.sync_engine, "before_cursor_execute", _capture)
        try:
            inserted = await repo.upsert("key_rt", "acc_sticky_rt", kind=StickySessionKind.PROMPT_CACHE)
            updated = await repo.upsert("key_rt", "acc_sticky_rt", kind=StickySessionKind.PROMPT_CACHE)
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", _capture)

    assert inserted.key == "key_rt"
    assert inserted.account_id == "acc_sticky_rt"
    assert updated.key == "key_rt"
    assert updated.account_id == "acc_sticky_rt"
    assert updated.updated_at >= inserted.updated_at
    # One INSERT ... RETURNING per upsert; no follow-up SELECT/refresh.
    assert len(statements) == 2
    assert all("INSERT" in stmt.upper() and "RETURNING" in stmt.upper() for stmt in statements)


@pytest.mark.asyncio
async def test_sticky_upsert_returning_refreshes_identity_map_instance(db_setup):
    """Rebinding a key within one session must return the NEW account even
    when the session's identity map already holds the row from an earlier
    upsert (populate_existing on the RETURNING execute)."""
    from app.db.models import StickySessionKind
    from app.modules.proxy.sticky_repository import StickySessionsRepository

    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        repo_accounts = AccountsRepository(session)
        for account_id in ("acc_rebind_a", "acc_rebind_b"):
            await repo_accounts.upsert(
                Account(
                    id=account_id,
                    email=f"{account_id}@example.com",
                    plan_type="plus",
                    access_token_encrypted=encryptor.encrypt("access"),
                    refresh_token_encrypted=encryptor.encrypt("refresh"),
                    id_token_encrypted=encryptor.encrypt("id"),
                    last_refresh=utcnow(),
                    status=AccountStatus.ACTIVE,
                    deactivation_reason=None,
                )
            )

    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        first = await repo.upsert("key_rebind", "acc_rebind_a", kind=StickySessionKind.PROMPT_CACHE)
        assert first.account_id == "acc_rebind_a"
        # Same session, same identity-map row: rebinding must not return the
        # stale in-memory account_id.
        second = await repo.upsert("key_rebind", "acc_rebind_b", kind=StickySessionKind.PROMPT_CACHE)
        assert second.account_id == "acc_rebind_b"


@pytest.mark.asyncio
async def test_sticky_prefix_purge_is_bounded_and_reserved_namespace_only(db_setup):
    from sqlalchemy import select, update

    from app.db.models import StickySession
    from app.modules.proxy.sticky_repository import StickySessionsRepository

    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(
            Account(
                id="acc_live_prefix",
                email="live-prefix@example.com",
                plan_type="plus",
                access_token_encrypted=encryptor.encrypt("access"),
                refresh_token_encrypted=encryptor.encrypt("refresh"),
                id_token_encrypted=encryptor.encrypt("id"),
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
                deactivation_reason=None,
            )
        )

    prefix = "\ncodex_live_call:"
    old_keys = {f"{prefix}old-a", f"{prefix}old-b"}
    new_key = f"{prefix}new"
    wildcard_lookalike_key = "codexXliveXcall:old"
    unrelated_key = "other_namespace:expired"
    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        for key in (*old_keys, new_key, wildcard_lookalike_key, unrelated_key):
            await repo.upsert(key, "acc_live_prefix", kind=StickySessionKind.CODEX_SESSION)
        old_time = utcnow() - timedelta(hours=3)
        await session.execute(
            update(StickySession)
            .where(StickySession.key.in_((*old_keys, wildcard_lookalike_key, unrelated_key)))
            .values(updated_at=old_time)
        )
        await session.commit()

        deleted = await repo.purge_before_for_key_prefix(
            utcnow() - timedelta(hours=2),
            kind=StickySessionKind.CODEX_SESSION,
            key_prefix=prefix,
            limit=1,
        )
        remaining = set(
            (
                await session.execute(
                    select(StickySession.key).where(StickySession.kind == StickySessionKind.CODEX_SESSION)
                )
            ).scalars()
        )

    assert deleted == 1
    assert len(old_keys.intersection(remaining)) == 1
    assert unrelated_key in remaining
    assert new_key in remaining
    assert wildcard_lookalike_key in remaining


@pytest.mark.asyncio
async def test_sticky_insert_if_absent_never_rebinds_existing_owner(db_setup):
    from app.modules.proxy.sticky_repository import StickySessionsRepository

    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        accounts = AccountsRepository(session)
        for account_id in ("acc_live_immutable_a", "acc_live_immutable_b"):
            await accounts.upsert(
                Account(
                    id=account_id,
                    email=f"{account_id}@example.com",
                    plan_type="plus",
                    access_token_encrypted=encryptor.encrypt("access"),
                    refresh_token_encrypted=encryptor.encrypt("refresh"),
                    id_token_encrypted=encryptor.encrypt("id"),
                    last_refresh=utcnow(),
                    status=AccountStatus.ACTIVE,
                    deactivation_reason=None,
                )
            )

    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        first_owner = await repo.insert_if_absent(
            "\ncodex_live_call:immutable",
            "acc_live_immutable_a",
            kind=StickySessionKind.CODEX_SESSION,
        )
        second_owner = await repo.insert_if_absent(
            "\ncodex_live_call:immutable",
            "acc_live_immutable_b",
            kind=StickySessionKind.CODEX_SESSION,
        )
        persisted_owner = await repo.get_account_id(
            "\ncodex_live_call:immutable",
            kind=StickySessionKind.CODEX_SESSION,
        )

    assert first_owner == "acc_live_immutable_a"
    assert second_owner == "acc_live_immutable_a"
    assert persisted_owner == "acc_live_immutable_a"


@pytest.mark.asyncio
async def test_seeded_sticky_upsert_is_atomic_and_preserves_first_seed_owner(db_setup, monkeypatch):
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.sql import Insert

    from app.modules.proxy.sticky_repository import StickySessionsRepository

    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        accounts = AccountsRepository(session)
        for account_id in ("acc_seeded_a", "acc_seeded_b"):
            await accounts.upsert(
                Account(
                    id=account_id,
                    email=f"{account_id}@example.com",
                    plan_type="plus",
                    access_token_encrypted=encryptor.encrypt("access"),
                    refresh_token_encrypted=encryptor.encrypt("refresh"),
                    id_token_encrypted=encryptor.encrypt("id"),
                    last_refresh=utcnow(),
                    status=AccountStatus.ACTIVE,
                    deactivation_reason=None,
                )
            )

    seed_key = "seeded-process"
    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        first_thread = await repo.upsert_with_seed_if_absent(
            "seeded-thread-a",
            "acc_seeded_a",
            kind=StickySessionKind.PROMPT_CACHE,
            seed_key=seed_key,
            seed_kind=StickySessionKind.CODEX_SESSION,
        )
        second_thread = await repo.upsert_with_seed_if_absent(
            "seeded-thread-b",
            "acc_seeded_b",
            kind=StickySessionKind.PROMPT_CACHE,
            seed_key=seed_key,
            seed_kind=StickySessionKind.CODEX_SESSION,
        )

        assert first_thread.account_id == "acc_seeded_a"
        assert second_thread.account_id == "acc_seeded_b"
        assert await repo.get_account_id(seed_key, kind=StickySessionKind.CODEX_SESSION) == "acc_seeded_a"

        original_build_upsert = repo._build_upsert_statement

        def _build_failing_upsert(key: str, account_id: str, kind: StickySessionKind) -> Insert:
            del account_id
            return original_build_upsert(key, "missing-account", kind)

        monkeypatch.setattr(repo, "_build_upsert_statement", _build_failing_upsert)
        with pytest.raises(IntegrityError):
            await repo.upsert_with_seed_if_absent(
                "seeded-thread-failing",
                "acc_seeded_a",
                kind=StickySessionKind.PROMPT_CACHE,
                seed_key="seeded-process-failing",
                seed_kind=StickySessionKind.CODEX_SESSION,
            )

        # The repository rolls back both statements itself, so even a caller
        # that catches the failure cannot accidentally commit the seed later.
        assert (
            await repo.get_account_id(
                "seeded-process-failing",
                kind=StickySessionKind.CODEX_SESSION,
            )
            is None
        )


@pytest.mark.asyncio
async def test_unavailable_owner_tombstone_compare_and_set_preserves_concurrent_rebind(db_setup):
    from sqlalchemy import select

    from app.db.models import StickySession
    from app.modules.proxy.sticky_repository import StickySessionsRepository

    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        accounts = AccountsRepository(session)
        for account_id, status in (
            ("acc_restart_cas_old", AccountStatus.QUOTA_EXCEEDED),
            ("acc_restart_cas_new", AccountStatus.ACTIVE),
        ):
            await accounts.upsert(
                Account(
                    id=account_id,
                    email=f"{account_id}@example.com",
                    plan_type="plus",
                    access_token_encrypted=encryptor.encrypt("access"),
                    refresh_token_encrypted=encryptor.encrypt("refresh"),
                    id_token_encrypted=encryptor.encrypt("id"),
                    last_refresh=utcnow(),
                    status=status,
                    deactivation_reason=None,
                )
            )

    key = "restart-owner-cas"
    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        await repo.upsert(key, "acc_restart_cas_old", kind=StickySessionKind.CODEX_SESSION)
        # Simulate a newer claim landing after selection read the old owner.
        await repo.upsert(key, "acc_restart_cas_new", kind=StickySessionKind.CODEX_SESSION)
        retired = await repo.abandon_legacy_session_header_owner_if_unavailable(
            key,
            kind=StickySessionKind.CODEX_SESSION,
            expected_account_id="acc_restart_cas_old",
        )
        row = await session.scalar(
            select(StickySession).where(
                StickySession.key == key,
                StickySession.kind == StickySessionKind.CODEX_SESSION,
            )
        )

    assert retired is False
    assert row is not None
    assert row.account_id == "acc_restart_cas_new"
    assert row.continuity_abandoned_at is None


@pytest.mark.asyncio
async def test_stale_expiry_cleanup_cannot_delete_fresh_rebound_owner(async_client) -> None:
    from sqlalchemy import select, update

    from app.db.models import StickySession
    from app.modules.proxy.sticky_repository import StickySessionsRepository

    old_owner = await _import_account(async_client, "acc_live_expiry_old", "live-expiry-old@example.com")
    new_owner = await _import_account(async_client, "acc_live_expiry_new", "live-expiry-new@example.com")
    key = "\ncodex_live_call:expiry-race"
    stale_read = asyncio.Event()
    rebound = asyncio.Event()

    async with SessionLocal() as setup_session:
        repo = StickySessionsRepository(setup_session)
        await repo.upsert(key, old_owner, kind=StickySessionKind.CODEX_SESSION)
        await setup_session.execute(
            update(StickySession)
            .where(StickySession.key == key, StickySession.kind == StickySessionKind.CODEX_SESSION)
            .values(updated_at=utcnow() - timedelta(hours=3))
        )
        await setup_session.commit()

    class PausedAfterStaleReadRepository(StickySessionsRepository):
        async def get_entry(self, key: str, *, kind: StickySessionKind) -> StickySession | None:
            row = await super().get_entry(key, kind=kind)
            await self._session.commit()
            stale_read.set()
            await rebound.wait()
            return row

    async with SessionLocal() as stale_session, SessionLocal() as rebinding_session:
        stale_repo = PausedAfterStaleReadRepository(stale_session)
        resolution = asyncio.create_task(
            stale_repo.get_account_id(
                key,
                kind=StickySessionKind.CODEX_SESSION,
                max_age_seconds=60,
            )
        )
        await asyncio.wait_for(stale_read.wait(), timeout=1)
        await StickySessionsRepository(rebinding_session).upsert(
            key,
            new_owner,
            kind=StickySessionKind.CODEX_SESSION,
        )
        rebound.set()
        resolved_owner = await asyncio.wait_for(resolution, timeout=1)

    async with SessionLocal() as verification_session:
        persisted_owner = await verification_session.scalar(
            select(StickySession.account_id).where(
                StickySession.key == key,
                StickySession.kind == StickySessionKind.CODEX_SESSION,
            )
        )

    assert resolved_owner == new_owner
    assert persisted_owner == new_owner


@pytest.mark.asyncio
async def test_stale_expiry_race_reread_reports_concurrent_tombstone() -> None:
    """The race-safe max-age expiry path (see get_account_id_and_abandonment)
    re-reads the row when its delete-on-expiry predicate misses a concurrent
    write, and must apply the abandonment check to that re-read row, not just
    the initial snapshot — otherwise a mapping tombstoned by the purge job in
    the gap between the stale read and the delete attempt would be reported
    as a plain miss (never-seen key) instead of authorized-to-reselect."""
    from sqlalchemy import update

    from app.db.models import StickySession
    from app.modules.proxy.sticky_repository import StickySessionsRepository

    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(
            Account(
                id="acc_race_tombstone_owner",
                email="race-tombstone@example.com",
                plan_type="plus",
                access_token_encrypted=encryptor.encrypt("access"),
                refresh_token_encrypted=encryptor.encrypt("refresh"),
                id_token_encrypted=encryptor.encrypt("id"),
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
                deactivation_reason=None,
            )
        )

    key = "\ncodex_live_call:tombstone-race"
    stale_read = asyncio.Event()
    tombstoned = asyncio.Event()

    async with SessionLocal() as setup_session:
        repo = StickySessionsRepository(setup_session)
        await repo.upsert(key, "acc_race_tombstone_owner", kind=StickySessionKind.CODEX_SESSION)
        await setup_session.execute(
            update(StickySession)
            .where(StickySession.key == key, StickySession.kind == StickySessionKind.CODEX_SESSION)
            .values(updated_at=utcnow() - timedelta(hours=3))
        )
        await setup_session.commit()

    class PausedAfterStaleReadRepository(StickySessionsRepository):
        async def get_entry(self, key: str, *, kind: StickySessionKind) -> StickySession | None:
            row = await super().get_entry(key, kind=kind)
            await self._session.commit()
            stale_read.set()
            await tombstoned.wait()
            return row

    async with SessionLocal() as stale_session, SessionLocal() as tombstone_session:
        stale_repo = PausedAfterStaleReadRepository(stale_session)
        resolution = asyncio.create_task(
            stale_repo.get_account_id_and_abandonment(
                key,
                kind=StickySessionKind.CODEX_SESSION,
                max_age_seconds=60,
            )
        )
        await asyncio.wait_for(stale_read.wait(), timeout=1)
        # Same account_id, but updated_at moves — this is exactly what the
        # purge job's tombstone step does, and it's enough to miss the
        # delete-on-expiry predicate (which pins on the originally observed
        # updated_at), forcing the race-safe re-read.
        await tombstone_session.execute(
            update(StickySession)
            .where(StickySession.key == key, StickySession.kind == StickySessionKind.CODEX_SESSION)
            .values(updated_at=utcnow(), continuity_abandoned_at=utcnow())
        )
        await tombstone_session.commit()
        tombstoned.set()
        resolved = await asyncio.wait_for(resolution, timeout=1)

    async with SessionLocal() as verification_session:
        persisted_row = await StickySessionsRepository(verification_session).get_entry(
            key, kind=StickySessionKind.CODEX_SESSION
        )

    assert resolved.account_id is None
    assert resolved.continuity_abandoned is True
    assert resolved.abandoned_account_id is None
    assert persisted_row is not None
    assert persisted_row.continuity_abandoned_at is not None


def test_realtime_call_affinity_key_is_scoped_and_opaque() -> None:
    api_key_a = cast(ApiKeyData, SimpleNamespace(id="api-key-a"))
    api_key_b = cast(ApiKeyData, SimpleNamespace(id="api-key-b"))

    key_a = realtime_call_affinity_key("rtc_secret", api_key_a)
    key_b = realtime_call_affinity_key("rtc_secret", api_key_b)

    assert key_a != key_b
    assert "rtc_secret" not in key_a
    assert "api-key-a" not in key_a


@pytest.mark.asyncio
async def test_realtime_call_owner_binding_is_immutable_and_persists_only_digest(async_client):
    from app.dependencies import get_proxy_service_for_app
    from app.modules.proxy.sticky_repository import StickySessionsRepository

    account_a = await _import_account(async_client, "acc_live_binding_a", "live-binding-a@example.com")
    account_b = await _import_account(async_client, "acc_live_binding_b", "live-binding-b@example.com")
    api_key = cast(ApiKeyData, SimpleNamespace(id="api-key-live-binding"))
    service = get_proxy_service_for_app(async_client._transport.app)

    call_id = await service.bind_realtime_call_owner(
        response_headers={"Location": "/v1/realtime/calls/rtc_secret"},
        account_id=account_a,
        api_key=api_key,
    )

    affinity_key = realtime_call_affinity_key("rtc_secret", api_key)
    async with SessionLocal() as session:
        persisted_owner = await StickySessionsRepository(session).get_account_id(
            affinity_key,
            kind=StickySessionKind.CODEX_SESSION,
        )

    assert call_id == "rtc_secret"
    assert persisted_owner == account_a
    assert "rtc_secret" not in affinity_key
    assert "api-key-live-binding" not in affinity_key

    with pytest.raises(RuntimeError, match="already bound"):
        await service.bind_realtime_call_owner(
            response_headers={"Location": "/v1/realtime/calls/rtc_secret"},
            account_id=account_b,
            api_key=api_key,
        )

    async with SessionLocal() as session:
        persisted_owner = await StickySessionsRepository(session).get_account_id(
            affinity_key,
            kind=StickySessionKind.CODEX_SESSION,
        )
    assert persisted_owner == account_a


@pytest.mark.asyncio
async def test_resolve_missing_realtime_call_does_not_purge_other_bindings(async_client):
    from sqlalchemy import select, update

    from app.db.models import StickySession
    from app.dependencies import get_proxy_service_for_app
    from app.modules.proxy.sticky_repository import StickySessionsRepository

    account_id = await _import_account(async_client, "acc_live_expired", "live-expired@example.com")
    api_key = cast(ApiKeyData, SimpleNamespace(id="api-key-live-expired"))
    affinity_key = realtime_call_affinity_key("rtc_retained", api_key)

    async with SessionLocal() as session:
        await StickySessionsRepository(session).upsert(
            affinity_key,
            account_id,
            kind=StickySessionKind.CODEX_SESSION,
        )
        await session.execute(
            update(StickySession)
            .where(StickySession.key == affinity_key)
            .values(updated_at=utcnow() - timedelta(seconds=_REALTIME_CALL_AFFINITY_MAX_AGE_SECONDS + 1))
        )
        await session.commit()

    service = get_proxy_service_for_app(async_client._transport.app)
    resolved_owner = await service._resolve_realtime_call_owner("rtc_missing", api_key=api_key)

    async with SessionLocal() as session:
        retained_row = (
            await session.execute(select(StickySession).where(StickySession.key == affinity_key))
        ).scalar_one()

    assert resolved_owner is None
    assert retained_row.account_id == account_id


@pytest.mark.asyncio
async def test_purge_stale_hard_codex_session_mappings_only_drops_durably_unavailable_owners(db_setup):
    """A hard codex_session mapping must survive a merely-transient owner
    blip (rate-limited/paused, but recently used) and only be dropped once
    BOTH the owner is non-active AND the mapping itself hasn't been used
    since well before the cutoff. Mappings on a healthy owner, and mappings
    still recently used despite an unavailable owner, must never be touched.
    A known future reset_at overrides the flat cutoff entirely: the owner's
    own stated recovery point must pass before its mapping is eligible."""
    from app.db.models import StickySession
    from app.modules.proxy.sticky_repository import StickySessionsRepository

    encryptor = TokenEncryptor()
    now = utcnow()
    cutoff = now - timedelta(hours=6)
    future_reset_at = naive_utc_to_epoch(now + timedelta(days=3))
    accounts = [
        ("acc_purge_active", AccountStatus.ACTIVE, None),
        ("acc_purge_recent_transition_rate_limited", AccountStatus.ACTIVE, None),
        ("acc_purge_recent_transition_paused", AccountStatus.ACTIVE, None),
        ("acc_purge_fresh_rate_limited", AccountStatus.RATE_LIMITED, None),
        ("acc_purge_stale_rate_limited", AccountStatus.RATE_LIMITED, None),
        ("acc_purge_fresh_paused", AccountStatus.PAUSED, None),
        ("acc_purge_stale_paused", AccountStatus.PAUSED, None),
        ("acc_purge_stale_future_reset", AccountStatus.RATE_LIMITED, future_reset_at),
    ]
    # Only the "stale_*" mappings are backdated to before the cutoff; the
    # rest keep their real upsert-time timestamp (well after the cutoff).
    stale_account_ids = {"acc_purge_stale_rate_limited", "acc_purge_stale_paused", "acc_purge_stale_future_reset"}

    async with SessionLocal() as session:
        repo_accounts = AccountsRepository(session)
        for account_id, status, reset_at in accounts:
            await repo_accounts.upsert(
                Account(
                    id=account_id,
                    email=f"{account_id}@example.com",
                    plan_type="plus",
                    access_token_encrypted=encryptor.encrypt("access"),
                    refresh_token_encrypted=encryptor.encrypt("refresh"),
                    id_token_encrypted=encryptor.encrypt("id"),
                    last_refresh=utcnow(),
                    status=status,
                    deactivation_reason=None,
                    reset_at=reset_at,
                )
            )

    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        for account_id, _status, _reset_at in accounts:
            await repo.upsert(f"turn_{account_id}", account_id, kind=StickySessionKind.CODEX_SESSION)

    async with SessionLocal() as session:
        for account_id in stale_account_ids:
            await session.execute(
                update(StickySession)
                .where(StickySession.key == f"turn_{account_id}", StickySession.kind == StickySessionKind.CODEX_SESSION)
                .values(updated_at=cutoff - timedelta(hours=1))
            )
        for account_id in ("acc_purge_recent_transition_rate_limited", "acc_purge_recent_transition_paused"):
            await session.execute(
                update(StickySession)
                .where(StickySession.key == f"turn_{account_id}", StickySession.kind == StickySessionKind.CODEX_SESSION)
                .values(updated_at=cutoff - timedelta(hours=1))
            )
        await session.commit()

    async with SessionLocal() as session:
        repo_accounts = AccountsRepository(session)
        assert await repo_accounts.update_status(
            "acc_purge_recent_transition_rate_limited",
            AccountStatus.RATE_LIMITED,
        )
        assert await repo_accounts.update_status_if_current(
            "acc_purge_recent_transition_paused",
            AccountStatus.PAUSED,
            expected_status=AccountStatus.ACTIVE,
        )
        # Rewriting an already-unavailable state must not extend the grace
        # period, and a failed compare-and-set must not touch it either.
        assert await repo_accounts.update_status(
            "acc_purge_stale_rate_limited",
            AccountStatus.RATE_LIMITED,
        )
        assert not await repo_accounts.update_status_if_current(
            "acc_purge_stale_paused",
            AccountStatus.PAUSED,
            expected_status=AccountStatus.ACTIVE,
        )

    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        purged_count = await repo.purge_stale_hard_codex_session_mappings(cutoff, now=now)

    # First pass only tombstones (continuity_abandoned_at set); nothing is
    # actually deleted yet, so a `conversation`-continuity request against
    # one of these keys can still recognize "abandoned, pick a fresh owner"
    # instead of being told the key was never seen.
    assert purged_count == 2
    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        entries = {
            account_id: await repo.get_entry(f"turn_{account_id}", kind=StickySessionKind.CODEX_SESSION)
            for account_id, _status, _reset_at in accounts
        }
    present = {account_id for account_id, entry in entries.items() if entry is not None}
    tombstoned = {
        account_id
        for account_id, entry in entries.items()
        if entry is not None and entry.continuity_abandoned_at is not None
    }
    assert present == {account_id for account_id, _status, _reset_at in accounts}
    assert tombstoned == {"acc_purge_stale_rate_limited", "acc_purge_stale_paused"}


@pytest.mark.asyncio
async def test_purge_stale_hard_codex_session_mappings_deletes_unclaimed_tombstones(db_setup):
    """Once a tombstone (continuity_abandoned_at set — see the first purge
    pass) has sat unclaimed past a further grace window, it's dropped
    outright: by then a fresh request for that key is fine falling back to
    the same conservative fail-closed default as a key that was never seen.
    A tombstone still inside its grace window, and a live (never-purged)
    mapping, must both be left alone."""
    from app.db.models import StickySession
    from app.modules.proxy.sticky_repository import StickySessionsRepository

    encryptor = TokenEncryptor()
    now = utcnow()
    cutoff = now - timedelta(hours=6)
    accounts = [
        ("acc_tombstone_old", AccountStatus.RATE_LIMITED, None),
        ("acc_tombstone_recent", AccountStatus.RATE_LIMITED, None),
        ("acc_tombstone_live", AccountStatus.RATE_LIMITED, None),
    ]

    async with SessionLocal() as session:
        repo_accounts = AccountsRepository(session)
        for account_id, status, reset_at in accounts:
            await repo_accounts.upsert(
                Account(
                    id=account_id,
                    email=f"{account_id}@example.com",
                    plan_type="plus",
                    access_token_encrypted=encryptor.encrypt("access"),
                    refresh_token_encrypted=encryptor.encrypt("refresh"),
                    id_token_encrypted=encryptor.encrypt("id"),
                    last_refresh=utcnow(),
                    status=status,
                    deactivation_reason=None,
                    reset_at=reset_at,
                )
            )

    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        for account_id, _status, _reset_at in accounts:
            await repo.upsert(f"turn_{account_id}", account_id, kind=StickySessionKind.CODEX_SESSION)

    async with SessionLocal() as session:
        # Already a tombstone, past its own grace window: must be deleted.
        await session.execute(
            update(StickySession)
            .where(StickySession.key == "turn_acc_tombstone_old", StickySession.kind == StickySessionKind.CODEX_SESSION)
            .values(continuity_abandoned_at=cutoff - timedelta(hours=1))
        )
        # Already a tombstone, but still inside its grace window: must survive.
        await session.execute(
            update(StickySession)
            .where(
                StickySession.key == "turn_acc_tombstone_recent", StickySession.kind == StickySessionKind.CODEX_SESSION
            )
            .values(continuity_abandoned_at=cutoff + timedelta(hours=1))
        )
        # Never tombstoned at all, and its updated_at is recent: must survive
        # untouched by either phase.
        await session.commit()

    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        purged_count = await repo.purge_stale_hard_codex_session_mappings(cutoff, now=now)

    assert purged_count == 1
    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        entries = {
            account_id: await repo.get_entry(f"turn_{account_id}", kind=StickySessionKind.CODEX_SESSION)
            for account_id, _status, _reset_at in accounts
        }
    assert entries["acc_tombstone_old"] is None
    assert entries["acc_tombstone_recent"] is not None
    assert entries["acc_tombstone_recent"].continuity_abandoned_at is not None
    assert entries["acc_tombstone_live"] is not None
    assert entries["acc_tombstone_live"].continuity_abandoned_at is None


@pytest.mark.asyncio
async def test_seed_hard_sticky_outage_grace_on_startup_refreshes_only_unavailable_accounts(db_setup):
    """A hard codex_session mapping whose owner was already unavailable
    before this process started (no live status transition to hook) must
    still get a fresh grace window once, at startup — otherwise the very
    first cleanup cycle could purge an outage that began moments before
    deploy. A healthy owner's mapping must be left untouched."""
    from app.db.models import StickySession
    from app.modules.proxy.sticky_repository import StickySessionsRepository

    encryptor = TokenEncryptor()
    long_ago = utcnow() - timedelta(days=30)
    accounts = [
        ("acc_seed_active", AccountStatus.ACTIVE),
        ("acc_seed_rate_limited", AccountStatus.RATE_LIMITED),
        ("acc_seed_paused", AccountStatus.PAUSED),
        ("acc_seed_quota_exceeded", AccountStatus.QUOTA_EXCEEDED),
    ]

    async with SessionLocal() as session:
        repo_accounts = AccountsRepository(session)
        for account_id, status in accounts:
            await repo_accounts.upsert(
                Account(
                    id=account_id,
                    email=f"{account_id}@example.com",
                    plan_type="plus",
                    access_token_encrypted=encryptor.encrypt("access"),
                    refresh_token_encrypted=encryptor.encrypt("refresh"),
                    id_token_encrypted=encryptor.encrypt("id"),
                    last_refresh=utcnow(),
                    status=status,
                    deactivation_reason=None,
                )
            )

    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        for account_id, _status in accounts:
            await repo.upsert(f"turn_{account_id}", account_id, kind=StickySessionKind.CODEX_SESSION)

    # Backdate every mapping's updated_at, simulating rows that predate this
    # process's startup entirely (no status transition ever touched them).
    async with SessionLocal() as session:
        for account_id, _status in accounts:
            await session.execute(
                update(StickySession)
                .where(StickySession.key == f"turn_{account_id}", StickySession.kind == StickySessionKind.CODEX_SESSION)
                .values(updated_at=long_ago)
            )
        await session.commit()

    async with SessionLocal() as session:
        seeded_count = await AccountsRepository(session).seed_hard_sticky_outage_grace_on_startup()

    assert seeded_count == 3
    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        refreshed_at = {}
        for account_id, _status in accounts:
            entry = await repo.get_entry(f"turn_{account_id}", kind=StickySessionKind.CODEX_SESSION)
            assert entry is not None
            refreshed_at[account_id] = entry.updated_at

    assert refreshed_at["acc_seed_active"] == long_ago
    for account_id in ("acc_seed_rate_limited", "acc_seed_paused", "acc_seed_quota_exceeded"):
        assert refreshed_at[account_id] > long_ago + timedelta(days=1)

    # A later boot — same or a different replica, same shared database —
    # must not reseed: the durable runtime_sentinels marker means this only
    # ever runs once per database, so a fast redeploy/autoscaling cadence
    # can't keep resetting the grace clock for a durably-dead mapping.
    async with SessionLocal() as session:
        for account_id, _status in accounts:
            await session.execute(
                update(StickySession)
                .where(StickySession.key == f"turn_{account_id}", StickySession.kind == StickySessionKind.CODEX_SESSION)
                .values(updated_at=long_ago)
            )
        await session.commit()

    async with SessionLocal() as session:
        second_boot_seeded_count = await AccountsRepository(session).seed_hard_sticky_outage_grace_on_startup()
    assert second_boot_seeded_count == 0

    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        for account_id, _status in accounts:
            entry = await repo.get_entry(f"turn_{account_id}", kind=StickySessionKind.CODEX_SESSION)
            assert entry is not None
            assert entry.updated_at == long_ago


async def _create_account(account_id: str) -> None:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(
            Account(
                id=account_id,
                email=f"{account_id}@example.com",
                plan_type="plus",
                access_token_encrypted=encryptor.encrypt("access"),
                refresh_token_encrypted=encryptor.encrypt("refresh"),
                id_token_encrypted=encryptor.encrypt("id"),
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
                deactivation_reason=None,
            )
        )


async def _backdate_sticky_row(key: str, kind: StickySessionKind, *, age_seconds: float) -> None:
    from app.db.models import StickySession

    async with SessionLocal() as session:
        await session.execute(
            update(StickySession)
            .where(StickySession.key == key, StickySession.kind == kind)
            .values(updated_at=utcnow() - timedelta(seconds=age_seconds))
        )
        await session.commit()


@pytest.mark.asyncio
async def test_sticky_lookup_refresh_skippable_only_for_fresh_unmarked_rows(db_setup):
    """refresh_skip_deadline is set only when a same-owner upsert would be a
    pure updated_at rewrite: fresh within min(15s, 1% of TTL), not stamped in
    the future, and free of any abandonment marker."""
    from app.db.models import StickySession
    from app.modules.proxy.sticky_repository import StickySessionsRepository

    await _create_account("acc_refresh_skip")
    key = "key_refresh_skip"

    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        await repo.upsert(key, "acc_refresh_skip", kind=StickySessionKind.PROMPT_CACHE)

        fresh = await repo.get_account_id_and_abandonment(
            key,
            kind=StickySessionKind.PROMPT_CACHE,
            max_age_seconds=1800,
        )
        assert fresh.account_id == "acc_refresh_skip"
        assert isinstance(fresh.refresh_skip_deadline, datetime)
        # The deadline is observed_updated_at + window: never further out
        # than the full window from now.
        assert fresh.refresh_skip_deadline <= utcnow() + timedelta(seconds=15.0)

        # Without a TTL there is no refresh write to skip.
        durable = await repo.get_account_id_and_abandonment(key, kind=StickySessionKind.PROMPT_CACHE)
        assert durable.account_id == "acc_refresh_skip"
        assert durable.refresh_skip_deadline is None

    # 10s old: inside the 15s cap for an 1800s TTL, but outside 1% of a 600s
    # TTL (6s) — the window scales with the TTL it protects.
    await _backdate_sticky_row(key, StickySessionKind.PROMPT_CACHE, age_seconds=10.0)
    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        within_cap = await repo.get_account_id_and_abandonment(
            key,
            kind=StickySessionKind.PROMPT_CACHE,
            max_age_seconds=1800,
        )
        assert within_cap.account_id == "acc_refresh_skip"
        assert isinstance(within_cap.refresh_skip_deadline, datetime)
        beyond_fraction = await repo.get_account_id_and_abandonment(
            key,
            kind=StickySessionKind.PROMPT_CACHE,
            max_age_seconds=600,
        )
        assert beyond_fraction.account_id == "acc_refresh_skip"
        assert beyond_fraction.refresh_skip_deadline is None

    # A future updated_at (database clock ahead of the application, or a
    # restored row) is never skippable: an upper-bound-only age check would
    # otherwise satisfy the window for longer than the documented bound.
    await _backdate_sticky_row(key, StickySessionKind.PROMPT_CACHE, age_seconds=-30.0)
    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        future_stamped = await repo.get_account_id_and_abandonment(
            key,
            kind=StickySessionKind.PROMPT_CACHE,
            max_age_seconds=1800,
        )
        assert future_stamped.account_id == "acc_refresh_skip"
        assert future_stamped.refresh_skip_deadline is None

    # An abandonment marker disqualifies the skip even on a fresh row: the
    # upsert that would be skipped also clears the marker columns.
    async with SessionLocal() as session:
        await session.execute(
            update(StickySession)
            .where(StickySession.key == key, StickySession.kind == StickySessionKind.PROMPT_CACHE)
            .values(updated_at=utcnow(), continuity_abandonment_scope="session_header")
        )
        await session.commit()
    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        marked = await repo.get_account_id_and_abandonment(
            key,
            kind=StickySessionKind.PROMPT_CACHE,
            max_age_seconds=1800,
            continuity_source="turn_state",
        )
        # Non-matching source keeps the owner, but the marker still makes a
        # same-owner upsert semantic (it would clear the scope).
        assert marked.account_id == "acc_refresh_skip"
        assert marked.refresh_skip_deadline is None


@pytest.mark.asyncio
async def test_sticky_upsert_concurrent_same_key_semantics(db_setup):
    """Concurrent upserts on one (key, kind) must each observe their own
    write in RETURNING, keep exactly one row, and settle on one of the
    written owners."""
    from sqlalchemy import func as sa_func
    from sqlalchemy import select

    from app.db.models import StickySession
    from app.modules.proxy.sticky_repository import StickySessionsRepository

    await _create_account("acc_conc_a")
    await _create_account("acc_conc_b")
    key = "key_concurrent_upsert"
    started_at = utcnow()

    async def _one_upsert(index: int) -> str:
        account_id = "acc_conc_a" if index % 2 == 0 else "acc_conc_b"
        async with SessionLocal() as session:
            repo = StickySessionsRepository(session)
            row = await repo.upsert(key, account_id, kind=StickySessionKind.PROMPT_CACHE)
            assert row.key == key
            # RETURNING must reflect this statement's own write, not a
            # concurrent winner's row.
            assert row.account_id == account_id
            assert row.continuity_abandoned_at is None
            return row.account_id

    results = await asyncio.gather(*(_one_upsert(index) for index in range(12)))
    assert set(results) == {"acc_conc_a", "acc_conc_b"}

    async with SessionLocal() as session:
        row_count = await session.scalar(
            select(sa_func.count())
            .select_from(StickySession)
            .where(StickySession.key == key, StickySession.kind == StickySessionKind.PROMPT_CACHE)
        )
        assert row_count == 1
        final = await StickySessionsRepository(session).get_entry(key, kind=StickySessionKind.PROMPT_CACHE)
        assert final is not None
        assert final.account_id in {"acc_conc_a", "acc_conc_b"}
        # Backend timestamps may carry second precision only.
        assert final.updated_at >= started_at.replace(microsecond=0)


@pytest.mark.asyncio
async def test_sticky_refresh_skip_never_clobbers_concurrent_rebind(db_setup):
    """A request that observed a fresh same-owner row and skipped its refresh
    write must leave a concurrent rebind to another account intact."""
    from app.modules.proxy.sticky_repository import StickySessionsRepository

    await _create_account("acc_skip_old")
    await _create_account("acc_skip_new")
    key = "key_skip_vs_rebind"

    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        await repo.upsert(key, "acc_skip_old", kind=StickySessionKind.PROMPT_CACHE)
        lookup = await repo.get_account_id_and_abandonment(
            key,
            kind=StickySessionKind.PROMPT_CACHE,
            max_age_seconds=1800,
        )
        assert lookup.account_id == "acc_skip_old"
        # The selection layer would skip its same-owner refresh here.
        assert isinstance(lookup.refresh_skip_deadline, datetime)

    # Concurrent request rebinds the mapping while the first request is still
    # in flight; the first request performs no compensating write.
    async with SessionLocal() as session:
        await StickySessionsRepository(session).upsert(key, "acc_skip_new", kind=StickySessionKind.PROMPT_CACHE)

    async with SessionLocal() as session:
        final = await StickySessionsRepository(session).get_account_id(key, kind=StickySessionKind.PROMPT_CACHE)
        assert final == "acc_skip_new"

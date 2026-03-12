from __future__ import annotations

from datetime import timedelta
from urllib.parse import quote

import pytest
from sqlalchemy import text

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, StickySessionKind
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.sticky_sessions.cleanup_scheduler import StickySessionCleanupScheduler

pytestmark = pytest.mark.integration


async def _create_accounts() -> list[Account]:
    encryptor = TokenEncryptor()
    accounts = [
        Account(
            id="sticky-api-a",
            chatgpt_account_id="sticky-api-a",
            email="sticky-a@example.com",
            plan_type="plus",
            access_token_encrypted=encryptor.encrypt("access-a"),
            refresh_token_encrypted=encryptor.encrypt("refresh-a"),
            id_token_encrypted=encryptor.encrypt("id-a"),
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
            deactivation_reason=None,
        ),
        Account(
            id="sticky-api-b",
            chatgpt_account_id="sticky-api-b",
            email="sticky-b@example.com",
            plan_type="plus",
            access_token_encrypted=encryptor.encrypt("access-b"),
            refresh_token_encrypted=encryptor.encrypt("refresh-b"),
            id_token_encrypted=encryptor.encrypt("id-b"),
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
            deactivation_reason=None,
        ),
    ]
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        for account in accounts:
            await repo.upsert(account)
    return accounts


async def _set_affinity_ttl(seconds: int) -> None:
    async with SessionLocal() as session:
        await session.execute(
            text(
                """
                UPDATE dashboard_settings
                SET openai_cache_affinity_max_age_seconds = :seconds
                WHERE id = 1
                """
            ),
            {"seconds": seconds},
        )
        await session.commit()


async def _insert_sticky_session(
    *,
    key: str,
    account_id: str,
    kind: StickySessionKind,
    updated_at_offset_seconds: int,
) -> None:
    timestamp = utcnow() - timedelta(seconds=updated_at_offset_seconds)
    async with SessionLocal() as session:
        await session.execute(
            text(
                """
                INSERT INTO sticky_sessions (key, account_id, kind, created_at, updated_at)
                VALUES (:key, :account_id, :kind, :timestamp, :timestamp)
                """
            ),
            {
                "key": key,
                "account_id": account_id,
                "kind": kind.value,
                "timestamp": timestamp,
            },
        )
        await session.commit()


@pytest.mark.asyncio
async def test_sticky_sessions_api_lists_metadata_and_purges_stale(async_client):
    accounts = await _create_accounts()
    await _set_affinity_ttl(60)
    await _insert_sticky_session(
        key="prompt-cache-stale",
        account_id=accounts[0].id,
        kind=StickySessionKind.PROMPT_CACHE,
        updated_at_offset_seconds=600,
    )
    await _insert_sticky_session(
        key="prompt-cache-fresh",
        account_id=accounts[0].id,
        kind=StickySessionKind.PROMPT_CACHE,
        updated_at_offset_seconds=10,
    )
    await _insert_sticky_session(
        key="codex-session-old",
        account_id=accounts[1].id,
        kind=StickySessionKind.CODEX_SESSION,
        updated_at_offset_seconds=600,
    )

    response = await async_client.get("/api/sticky-sessions")
    assert response.status_code == 200
    payload = response.json()
    entries = {entry["key"]: entry for entry in payload["entries"]}

    assert entries["prompt-cache-stale"]["kind"] == "prompt_cache"
    assert entries["prompt-cache-stale"]["isStale"] is True
    assert entries["prompt-cache-stale"]["expiresAt"] is not None
    assert entries["prompt-cache-fresh"]["isStale"] is False
    assert entries["codex-session-old"]["kind"] == "codex_session"
    assert entries["codex-session-old"]["isStale"] is False
    assert entries["codex-session-old"]["expiresAt"] is None

    response = await async_client.get("/api/sticky-sessions", params={"staleOnly": "true"})
    assert response.status_code == 200
    stale_payload = response.json()
    assert [entry["key"] for entry in stale_payload["entries"]] == ["prompt-cache-stale"]

    response = await async_client.post("/api/sticky-sessions/purge", json={"staleOnly": True})
    assert response.status_code == 200
    assert response.json()["deletedCount"] == 1

    response = await async_client.get("/api/sticky-sessions")
    assert response.status_code == 200
    remaining_keys = {entry["key"] for entry in response.json()["entries"]}
    assert remaining_keys == {"prompt-cache-fresh", "codex-session-old"}


@pytest.mark.asyncio
async def test_sticky_sessions_api_rejects_non_stale_purge_requests(async_client):
    accounts = await _create_accounts()
    await _set_affinity_ttl(60)
    await _insert_sticky_session(
        key="prompt-cache-stale",
        account_id=accounts[0].id,
        kind=StickySessionKind.PROMPT_CACHE,
        updated_at_offset_seconds=600,
    )
    await _insert_sticky_session(
        key="codex-session-old",
        account_id=accounts[1].id,
        kind=StickySessionKind.CODEX_SESSION,
        updated_at_offset_seconds=600,
    )

    response = await async_client.post("/api/sticky-sessions/purge", json={"staleOnly": False})
    assert response.status_code == 422

    response = await async_client.get("/api/sticky-sessions")
    assert response.status_code == 200
    payload = response.json()
    assert payload["stalePromptCacheCount"] == 1
    assert {entry["key"] for entry in payload["entries"]} == {"prompt-cache-stale", "codex-session-old"}


@pytest.mark.asyncio
async def test_sticky_sessions_api_counts_hidden_stale_rows_and_deletes_by_kind(async_client):
    accounts = await _create_accounts()
    await _set_affinity_ttl(60)

    for index in range(101):
        await _insert_sticky_session(
            key=f"fresh-session-{index:03d}",
            account_id=accounts[index % len(accounts)].id,
            kind=StickySessionKind.STICKY_THREAD,
            updated_at_offset_seconds=index + 1,
        )

    await _insert_sticky_session(
        key="shared-key",
        account_id=accounts[0].id,
        kind=StickySessionKind.PROMPT_CACHE,
        updated_at_offset_seconds=600,
    )
    await _insert_sticky_session(
        key="shared-key",
        account_id=accounts[1].id,
        kind=StickySessionKind.CODEX_SESSION,
        updated_at_offset_seconds=5,
    )

    response = await async_client.get("/api/sticky-sessions")
    assert response.status_code == 200
    payload = response.json()

    assert payload["stalePromptCacheCount"] == 1
    assert len(payload["entries"]) == 100
    assert not any(entry["key"] == "shared-key" and entry["kind"] == "prompt_cache" for entry in payload["entries"])
    assert any(entry["key"] == "shared-key" and entry["kind"] == "codex_session" for entry in payload["entries"])

    response = await async_client.get("/api/sticky-sessions", params={"staleOnly": "true"})
    assert response.status_code == 200
    stale_payload = response.json()
    assert stale_payload["stalePromptCacheCount"] == 1
    assert [(entry["key"], entry["kind"]) for entry in stale_payload["entries"]] == [("shared-key", "prompt_cache")]

    response = await async_client.delete("/api/sticky-sessions/prompt_cache/shared-key")
    assert response.status_code == 200

    response = await async_client.get("/api/sticky-sessions")
    assert response.status_code == 200
    after_delete = response.json()
    assert after_delete["stalePromptCacheCount"] == 0
    assert any(entry["key"] == "shared-key" and entry["kind"] == "codex_session" for entry in after_delete["entries"])


@pytest.mark.asyncio
async def test_sticky_sessions_api_deletes_slash_containing_keys(async_client):
    accounts = await _create_accounts()
    await _set_affinity_ttl(60)
    sticky_key = "folder/session"

    await _insert_sticky_session(
        key=sticky_key,
        account_id=accounts[0].id,
        kind=StickySessionKind.PROMPT_CACHE,
        updated_at_offset_seconds=10,
    )

    response = await async_client.get("/api/sticky-sessions")
    assert response.status_code == 200
    assert any(entry["key"] == sticky_key for entry in response.json()["entries"])

    response = await async_client.delete(f"/api/sticky-sessions/prompt_cache/{quote(sticky_key, safe='')}")
    assert response.status_code == 200

    response = await async_client.get("/api/sticky-sessions")
    assert response.status_code == 200
    assert all(entry["key"] != sticky_key for entry in response.json()["entries"])


@pytest.mark.asyncio
async def test_sticky_sessions_cleanup_scheduler_removes_only_stale_prompt_cache(db_setup):
    accounts = await _create_accounts()
    await _set_affinity_ttl(60)
    await _insert_sticky_session(
        key="cleanup-stale",
        account_id=accounts[0].id,
        kind=StickySessionKind.PROMPT_CACHE,
        updated_at_offset_seconds=600,
    )
    await _insert_sticky_session(
        key="cleanup-durable",
        account_id=accounts[1].id,
        kind=StickySessionKind.STICKY_THREAD,
        updated_at_offset_seconds=600,
    )

    scheduler = StickySessionCleanupScheduler(interval_seconds=300, enabled=True)
    await scheduler._cleanup_once()

    async with SessionLocal() as session:
        remaining = {
            row[0] for row in (await session.execute(text("SELECT key FROM sticky_sessions ORDER BY key"))).fetchall()
        }

    assert remaining == {"cleanup-durable"}

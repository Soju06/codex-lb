from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import update

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, StickySession, StickySessionKind
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy.affinity import _response_bound_thread_marker_key
from app.modules.proxy.sticky_repository import StickySessionsRepository

_HARD_KEY = "turn_lineage_neighbor"


async def _seed_unavailable_owner_with_marker_and_hard_row(account_id: str, marker_key: str) -> None:
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
                status=AccountStatus.RATE_LIMITED,
                deactivation_reason=None,
            )
        )
    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        await repo.upsert(marker_key, account_id, kind=StickySessionKind.CODEX_SESSION)
        await repo.upsert(_HARD_KEY, account_id, kind=StickySessionKind.CODEX_SESSION)
    async with SessionLocal() as session:
        await session.execute(
            update(StickySession)
            .where(StickySession.key.in_((marker_key, _HARD_KEY)))
            .values(updated_at=utcnow() - timedelta(days=2))
        )
        await session.commit()


@pytest.mark.asyncio
async def test_lineage_markers_stay_out_of_listing_and_hard_session_cleanup(async_client):
    marker_key = _response_bound_thread_marker_key("\ncodex-lb-affinity-v1:thread:parent")
    await _seed_unavailable_owner_with_marker_and_hard_row("acc_lineage_owner", marker_key)

    listing = await async_client.get("/api/sticky-sessions")
    assert listing.status_code == 200
    assert [entry["key"] for entry in listing.json()["entries"]] == [_HARD_KEY]

    filtered_delete = await async_client.post("/api/sticky-sessions/delete-filtered", json={})
    assert filtered_delete.status_code == 200
    assert filtered_delete.json()["deletedCount"] == 1

    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        assert await repo.get_entry(_HARD_KEY, kind=StickySessionKind.CODEX_SESSION) is None
        await repo.upsert(_HARD_KEY, "acc_lineage_owner", kind=StickySessionKind.CODEX_SESSION)
    async with SessionLocal() as session:
        await session.execute(
            update(StickySession).where(StickySession.key == _HARD_KEY).values(updated_at=utcnow() - timedelta(days=2))
        )
        await session.commit()

    now = utcnow()
    async with SessionLocal() as session:
        tombstoned = await StickySessionsRepository(session).purge_stale_hard_codex_session_mappings(
            now - timedelta(hours=6), now=now
        )
    assert tombstoned == 1

    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        marker = await repo.get_entry(marker_key, kind=StickySessionKind.CODEX_SESSION)
        hard = await repo.get_entry(_HARD_KEY, kind=StickySessionKind.CODEX_SESSION)
    assert marker is not None and marker.continuity_abandoned_at is None
    assert hard is not None and hard.continuity_abandoned_at is not None


@pytest.mark.asyncio
async def test_lineage_markers_expire_on_their_own_sweep(db_setup):
    marker_key = _response_bound_thread_marker_key("\ncodex-lb-affinity-v1:thread:expired-parent")
    await _seed_unavailable_owner_with_marker_and_hard_row("acc_lineage_expiry", marker_key)

    async with SessionLocal() as session:
        deleted = await StickySessionsRepository(session).purge_subagent_lineage_markers_before(
            utcnow() - timedelta(days=1)
        )
    assert deleted == 1

    async with SessionLocal() as session:
        repo = StickySessionsRepository(session)
        assert await repo.get_entry(marker_key, kind=StickySessionKind.CODEX_SESSION) is None
        assert await repo.get_entry(_HARD_KEY, kind=StickySessionKind.CODEX_SESSION) is not None

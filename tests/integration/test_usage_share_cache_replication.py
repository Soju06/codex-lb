from __future__ import annotations

import pytest

from app.core.auth.api_key_cache import ApiKeyCache
from app.core.cache.invalidation import NAMESPACE_ACCOUNT_ROUTING, NAMESPACE_API_KEY, CacheInvalidationPoller
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


async def test_account_pause_evicts_peer_allocation_policy_through_api_key_namespace(async_client) -> None:
    encryptor = TokenEncryptor()
    account_id = "allocation-peer"
    async with SessionLocal() as session:
        session.add(
            Account(
                id=account_id,
                chatgpt_account_id=account_id,
                email="allocation-peer@example.com",
                plan_type="plus",
                access_token_encrypted=encryptor.encrypt("access"),
                refresh_token_encrypted=encryptor.encrypt("refresh"),
                id_token_encrypted=encryptor.encrypt("id"),
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
            )
        )
        await session.commit()

    peer_cache: ApiKeyCache[str] = ApiKeyCache(ttl_seconds=60)
    peer = CacheInvalidationPoller(SessionLocal)
    peer.on_invalidation(NAMESPACE_API_KEY, peer_cache.clear)
    await peer._poll_once()
    await peer_cache.set("key", "old-allocation")

    # An unrelated routing observation is not an API-key invalidation.
    source = CacheInvalidationPoller(SessionLocal)
    assert await source.bump(NAMESPACE_ACCOUNT_ROUTING)
    await peer._poll_once()
    assert await peer_cache.get("key") == "old-allocation"

    paused = await async_client.post(f"/api/accounts/{account_id}/pause")
    assert paused.status_code == 200, paused.text
    await peer._poll_once()
    assert await peer_cache.get("key") is None

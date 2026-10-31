from __future__ import annotations

from copy import deepcopy

import pytest

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.dependencies import get_proxy_service_for_app
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy._load_balancer.types import RuntimeState

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_dashboard_observes_replica_health_without_changing_selection(async_client, app_instance):
    encryptor = TokenEncryptor()
    account = Account(
        id="observed-account",
        email="observation@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
    )
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(account)
    balancer = get_proxy_service_for_app(app_instance)._load_balancer
    now = balancer._clock.time()
    balancer._runtime[account.id] = RuntimeState(
        health_tier=1,
        error_count=3,
        last_selected_at=now - 60,
        cooldown_until=now + 60,
        overload_backoff_until=now + 90,
        burst_backoff_until=now + 10,
        inflight_streams=2,
    )
    before = deepcopy(balancer._runtime)
    response = await async_client.get(f"/api/accounts/{account.id}/routing-observation")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scope"] == "replica_local"
    assert body["accountId"] == account.id
    assert body["healthTier"] == 1
    assert body["recentErrors"] == 3
    assert body["inflightStreams"] == 2
    assert body["cooldownUntil"].endswith("Z") or body["cooldownUntil"].endswith("+00:00")
    assert balancer._runtime == before
    assert "access" not in response.text
    assert (await async_client.get("/api/accounts/missing/routing-observation")).status_code == 404

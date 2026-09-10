from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, update
from sqlalchemy.engine import Engine

from app.core.auth.refresh import TokenRefreshResult
from app.db.models import Account
from app.db.session import SessionLocal
from app.modules.rate_limit_reset_credits.store import get_rate_limit_reset_credits_store
from tests.integration.desktop_reset_support import HEADERS, seed

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.mark.parametrize("waiter_times_out", [False, True])
async def test_inventory_releases_connections_while_refreshing_expired_token(
    async_client, db_setup, monkeypatch, waiter_times_out
):
    await get_rate_limit_reset_credits_store().invalidate()
    await seed(monkeypatch, pooled=False)
    async with SessionLocal() as session:
        await session.execute(
            update(Account)
            .where(Account.id == "primary")
            .values(last_refresh=datetime.now(timezone.utc) - timedelta(days=20))
        )
        await session.commit()
    checked_out = set()
    refreshes = []
    refresh_started = asyncio.Event()
    resume_refresh = asyncio.Event()

    def checkout(connection, record, proxy):
        checked_out.add(record)

    def checkin(connection, record):
        checked_out.discard(record)

    async def refresh_token(token, **kwargs):
        refreshes.append(token)
        assert not checked_out, "Inventory kept a database connection during OAuth HTTP"
        refresh_started.set()
        if waiter_times_out:
            await resume_refresh.wait()
        return TokenRefreshResult(
            access_token="token:primary",
            refresh_token="rotated-refresh",
            id_token="new-id",
            account_id="original-account",
            plan_type="plus",
            email=None,
        )

    event.listen(Engine, "checkout", checkout)
    event.listen(Engine, "checkin", checkin)
    monkeypatch.setattr("app.modules.accounts.auth_manager.refresh_access_token", refresh_token)
    try:
        if waiter_times_out:
            monkeypatch.setattr("app.modules.desktop_resets.inventory._REFRESH_TIMEOUT_SECONDS", 0.1)
            first = asyncio.create_task(async_client.get("/api/codex/desktop/reset-credits", headers=HEADERS))
            await asyncio.wait_for(refresh_started.wait(), timeout=2)
            assert (await first).status_code == 503
            resume_refresh.set()
            monkeypatch.setattr("app.modules.desktop_resets.inventory._REFRESH_TIMEOUT_SECONDS", 2.0)
        response = await async_client.get("/api/codex/desktop/reset-credits", headers=HEADERS)
        assert response.status_code == 200, response.text
        assert response.json()["available_count"] == 1
        assert refreshes == ["unused-refresh"]
    finally:
        resume_refresh.set()
        event.remove(Engine, "checkout", checkout)
        event.remove(Engine, "checkin", checkin)
        await get_rate_limit_reset_credits_store().invalidate()

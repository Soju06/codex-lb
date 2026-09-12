from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import event

from app.core.crypto import TokenEncryptor
from app.core.utils.time import naive_utc_to_epoch
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal, engine
from app.modules.accounts.repository import AccountsRepository
from app.modules.dashboard import repository as dashboard_repository_module
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration

_FIXED_NOW = datetime(2026, 8, 17, 12, 0, 0)


async def _seed_weekly_account(account_id: str) -> None:
    encryptor = TokenEncryptor()
    reset_at = int(naive_utc_to_epoch(_FIXED_NOW + timedelta(hours=4)))
    async with SessionLocal() as session:
        await AccountsRepository(session).upsert(
            Account(
                id=account_id,
                email=f"{account_id}@example.com",
                plan_type="pro",
                access_token_encrypted=encryptor.encrypt("access"),
                refresh_token_encrypted=encryptor.encrypt("refresh"),
                id_token_encrypted=encryptor.encrypt("id"),
                last_refresh=_FIXED_NOW,
                status=AccountStatus.ACTIVE,
                deactivation_reason=None,
            )
        )
        usage_repo = UsageRepository(session)
        # Two climbs (5 -> 95, 5 -> 100) give 185% of trailing positive demand
        # against 100% of fleet capacity, and the saturated latest sample lets
        # the pace card surface that surplus as ``addProAccounts`` — the only
        # response field derived from the memoized aggregate.
        for minutes_ago, used_percent in ((170, 5.0), (120, 95.0), (70, 5.0), (1, 100.0)):
            await usage_repo.add_entry(
                account_id,
                used_percent,
                window="secondary",
                window_minutes=10_080,
                reset_at=reset_at,
                recorded_at=_FIXED_NOW - timedelta(minutes=minutes_ago),
            )


async def _weekly_demand_statements(async_client, paths: list[str]) -> tuple[list[str], list[dict]]:
    statements: list[str] = []

    def _capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    payloads: list[dict] = []
    event.listen(engine.sync_engine, "before_cursor_execute", _capture)
    try:
        for path in paths:
            response = await async_client.get(path)
            assert response.status_code == 200
            payloads.append(response.json())
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _capture)
    return [statement for statement in statements if "weekly_demand_samples" in statement], payloads


@pytest.mark.asyncio
async def test_overview_and_projections_share_one_trailing_demand_query(async_client, db_setup, monkeypatch):
    monkeypatch.setattr("app.modules.dashboard.service.utcnow", lambda: _FIXED_NOW)
    monkeypatch.setattr(dashboard_repository_module, "_TRAILING_DEMAND_TTL_SECONDS", 60.0)
    await _seed_weekly_account("acc-memo")

    demand_statements, payloads = await _weekly_demand_statements(
        async_client,
        ["/api/dashboard/overview", "/api/dashboard/projections", "/api/dashboard/overview"],
    )

    assert len(demand_statements) == 1
    cached_entries = list(dashboard_repository_module._trailing_demand_cache.values())
    assert len(cached_entries) == 1
    assert cached_entries[0][0] == {"acc-memo": pytest.approx(185.0)}
    paces = [payload["weeklyCreditPace"] for payload in payloads]
    assert all(pace is not None for pace in paces)
    assert paces[0]["addProAccounts"] == 1
    assert paces[0] == paces[1] == paces[2]


@pytest.mark.asyncio
async def test_trailing_demand_query_reruns_when_ttl_is_zero(async_client, db_setup, monkeypatch):
    monkeypatch.setattr("app.modules.dashboard.service.utcnow", lambda: _FIXED_NOW)
    await _seed_weekly_account("acc-exact")

    demand_statements, _ = await _weekly_demand_statements(
        async_client,
        ["/api/dashboard/overview", "/api/dashboard/projections"],
    )

    assert len(demand_statements) == 2
    assert dashboard_repository_module._trailing_demand_cache == {}

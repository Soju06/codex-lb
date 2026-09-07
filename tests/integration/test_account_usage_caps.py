from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import update

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, StickySessionKind, UsageHistory
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.proxy.account_cache import get_account_selection_cache, get_routing_availability_cache
from app.modules.proxy.load_balancer import LoadBalancer
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository

pytestmark = pytest.mark.integration


@asynccontextmanager
async def _repos() -> AsyncIterator[ProxyRepositories]:
    async with SessionLocal() as session:
        yield ProxyRepositories(
            accounts=AccountsRepository(session),
            usage=UsageRepository(session),
            request_logs=RequestLogsRepository(session),
            sticky_sessions=StickySessionsRepository(session),
            api_keys=ApiKeysRepository(session),
            additional_usage=AdditionalUsageRepository(session),
        )


async def _seed(*, primary: float = 79, weekly: float = 49, primary_minutes: int = 300) -> None:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        for account_id in ("capped", "fallback"):
            session.add(
                Account(
                    id=account_id,
                    email=f"{account_id}@example.com",
                    plan_type="plus",
                    access_token_encrypted=encryptor.encrypt("access"),
                    refresh_token_encrypted=encryptor.encrypt("refresh"),
                    id_token_encrypted=encryptor.encrypt("id"),
                    last_refresh=utcnow(),
                    status=AccountStatus.ACTIVE,
                    usage_cap_5h_percent=80 if account_id == "capped" else None,
                    usage_cap_weekly_percent=50 if account_id == "capped" else None,
                )
            )
        await session.commit()
        usage = UsageRepository(session)
        await usage.add_entry(
            account_id="capped",
            used_percent=primary,
            window="primary",
            reset_at=int(time.time()) + 3600,
            window_minutes=primary_minutes,
        )
        if primary_minutes == 300:
            await usage.add_entry(
                account_id="capped",
                used_percent=weekly,
                window="secondary",
                reset_at=int(time.time()) + 7200,
                window_minutes=10080,
            )
    get_account_selection_cache().invalidate()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("primary", "weekly", "blocked"), [(79, 49, False), (80, 49, True), (79, 50, True), (90, 60, True)]
)
async def test_caps_gate_selection_and_preserve_pinned_ownership(db_setup, primary, weekly, blocked):
    await _seed(primary=primary, weekly=weekly)
    balancer = LoadBalancer(_repos)
    selection = await balancer.select_account(
        required_account_id="capped",
        required_account_is_ownership_constraint=True,
    )
    assert (selection.account is None) == blocked
    if blocked:
        fallback = await balancer.select_account()
        assert fallback.account is not None and fallback.account.id == "fallback"
    async with SessionLocal() as session:
        account = await session.get(Account, "capped")
        assert account is not None
        assert account.status == AccountStatus.ACTIVE


@pytest.mark.asyncio
async def test_caps_recover_only_after_both_windows_reset(db_setup):
    await _seed(primary=80, weekly=50)
    balancer = LoadBalancer(_repos)
    cache = get_routing_availability_cache()
    try:
        await cache.refresh_usage_caps_from_db()
        assert cache.is_usage_capped("capped")
        for window, blocked in (("primary", True), ("secondary", False)):
            async with SessionLocal() as session:
                await session.execute(
                    update(UsageHistory)
                    .where(
                        UsageHistory.account_id == "capped",
                        UsageHistory.window == window,
                    )
                    .values(reset_at=int(time.time()) - 1)
                )
                await session.commit()
            get_account_selection_cache().invalidate()
            await cache.refresh_usage_caps_from_db()
            assert cache.is_usage_capped("capped") == blocked
            selection = await balancer.select_account(required_account_id="capped")
            assert (selection.account is None) == blocked
    finally:
        cache.reset()


@pytest.mark.asyncio
@pytest.mark.parametrize(("minutes", "used", "blocked"), [(10080, 50, True), (10080, 49, False), (43200, 99, False)])
async def test_caps_identify_weekly_primary_and_ignore_monthly(db_setup, minutes, used, blocked):
    await _seed(primary=used, primary_minutes=minutes)
    selection = await LoadBalancer(_repos).select_account(required_account_id="capped")
    assert (selection.account is None) == blocked


@pytest.mark.asyncio
async def test_sticky_account_over_cap_falls_back_without_pausing(db_setup):
    await _seed(primary=80)
    async with _repos() as repos:
        await repos.sticky_sessions.upsert("cap-test", "capped", kind=StickySessionKind.PROMPT_CACHE)
    selection = await LoadBalancer(_repos).select_account(
        sticky_key="cap-test",
        sticky_kind=StickySessionKind.PROMPT_CACHE,
    )
    assert selection.account is not None and selection.account.id == "fallback"


@pytest.mark.asyncio
async def test_caps_api_round_trip_and_removal(async_client):
    await _seed(primary=80, weekly=50)
    cache = get_routing_availability_cache()
    try:
        response = await async_client.put(
            "/api/accounts/capped/usage-caps",
            json={
                "usageCap5HPercent": 80,
                "usageCapWeeklyPercent": 50,
            },
        )
        assert response.status_code == 200
        assert cache.is_usage_capped("capped")
        accounts = (await async_client.get("/api/accounts")).json()["accounts"]
        account = next(row for row in accounts if row["accountId"] == "capped")
        assert account["usageCap5HPercent"] == 80
        assert account["usageCapWeeklyPercent"] == 50
        assert account["usage"]["primaryRemainingPercent"] == 20
        uncapped = next(row for row in accounts if row["accountId"] == "fallback")
        assert uncapped["usageCap5HPercent"] is None
        assert uncapped["usageCapWeeklyPercent"] is None
        response = await async_client.put(
            "/api/accounts/capped/usage-caps",
            json={
                "usageCap5HPercent": None,
                "usageCapWeeklyPercent": 50,
            },
        )
        assert response.status_code == 200 and cache.is_usage_capped("capped")
        response = await async_client.put(
            "/api/accounts/capped/usage-caps",
            json={
                "usageCap5HPercent": None,
                "usageCapWeeklyPercent": None,
            },
        )
        assert response.status_code == 200 and not cache.is_usage_capped("capped")
        selection = await LoadBalancer(_repos).select_account(required_account_id="capped")
        assert selection.account is not None
    finally:
        cache.reset()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [0, -1, 101, "80", True])
async def test_caps_api_rejects_invalid_values_atomically(async_client, value):
    await _seed()
    response = await async_client.put(
        "/api/accounts/capped/usage-caps",
        json={
            "usageCap5HPercent": 70,
            "usageCapWeeklyPercent": value,
        },
    )
    assert response.status_code == 422
    async with SessionLocal() as session:
        account = await session.get(Account, "capped")
        assert account is not None
        assert account.usage_cap_5h_percent == 80
        assert account.usage_cap_weekly_percent == 50


@pytest.mark.asyncio
async def test_caps_api_missing_and_deleted_accounts(async_client):
    await _seed()
    async with SessionLocal() as session:
        await session.execute(update(Account).where(Account.id == "capped").values(delete_requested_at=utcnow()))
        await session.commit()
    for account_id in ("missing", "capped"):
        response = await async_client.put(
            f"/api/accounts/{account_id}/usage-caps",
            json={
                "usageCap5HPercent": None,
                "usageCapWeeklyPercent": None,
            },
        )
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_additional_quota_cannot_bypass_standard_usage_caps(db_setup):
    await _seed(primary=80)
    async with _repos() as repos:
        await repos.additional_usage.add_entry(
            account_id="capped",
            limit_name="codex_spark",
            metered_feature="codex_bengalfox",
            window="primary",
            used_percent=1,
            reset_at=int(time.time()) + 3600,
            window_minutes=300,
        )
    selection = await LoadBalancer(_repos).select_account(
        required_account_id="capped",
        additional_limit_name="codex_spark",
    )
    assert selection.account is None
    async with _repos() as repos:
        await repos.accounts.update_usage_caps("capped", cap_5h=None, cap_weekly=None)
    get_account_selection_cache().invalidate()
    selection = await LoadBalancer(_repos).select_account(
        required_account_id="capped",
        additional_limit_name="codex_spark",
    )
    assert selection.account is not None


@pytest.mark.asyncio
async def test_missing_and_removed_short_window_does_not_block(db_setup):
    await _seed(primary=80, weekly=49)
    async with SessionLocal() as session:
        await session.execute(
            update(UsageHistory)
            .where(
                UsageHistory.account_id == "capped",
                UsageHistory.window == "primary",
            )
            .values(reset_at=int(time.time()) - 1)
        )
        await session.commit()
    selection = await LoadBalancer(_repos).select_account(required_account_id="capped")
    assert selection.account is not None

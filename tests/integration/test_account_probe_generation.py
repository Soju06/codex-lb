from datetime import datetime

import pytest

from app.db.models import Account, AccountStatus
from app.db.session import get_background_session
from app.modules.accounts.repository import AccountsRepository

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_same_second_rejection_advances_generation(async_client):
    async with get_background_session() as session:
        session.add(
            Account(
                id="generation",
                email="generation@example.invalid",
                plan_type="pro",
                access_token_encrypted=b"access",
                refresh_token_encrypted=b"refresh",
                id_token_encrypted=b"id",
                last_refresh=datetime(2026, 9, 10),
            )
        )
        await session.commit()
        repo = AccountsRepository(session)
        for _ in range(2):
            assert await repo.update_status(
                "generation",
                AccountStatus.RATE_LIMITED,
                reset_at=2000000000,
                blocked_at=1900000000,
                rejected_model="gpt-6-astra",
                rejected_service_tier="default",
            )
        row = await repo.get_by_id("generation")
        assert row is not None
        assert row.block_generation == 2
        assert row.rejected_model == "gpt-6-astra"
        assert not await repo.update_status_if_current(
            "generation",
            AccountStatus.ACTIVE,
            blocked_at=None,
            expected_status=AccountStatus.RATE_LIMITED,
            expected_reset_at=2000000000,
            expected_blocked_at=1900000000,
            expected_block_generation=1,
        )


@pytest.mark.asyncio
async def test_reimport_advances_generation_past_a_cached_account(async_client):
    from app.db.session import SessionLocal

    def account():
        return Account(
            id="cached-generation",
            email="cached-generation@example.invalid",
            plan_type="pro",
            status=AccountStatus.ACTIVE,
            access_token_encrypted=b"access",
            refresh_token_encrypted=b"refresh",
            id_token_encrypted=b"id",
            last_refresh=datetime(2026, 9, 10),
        )

    async with SessionLocal() as cached_session:
        cached = account()
        cached_session.add(cached)
        await cached_session.commit()
        assert cached.block_generation == 0
        async with SessionLocal() as rejection_session:
            rejected = AccountsRepository(rejection_session)
            for _ in range(2):
                await rejected.update_status(
                    cached.id,
                    AccountStatus.RATE_LIMITED,
                    reset_at=2000000000,
                    blocked_at=1900000000,
                    rejected_model="gpt-6-astra",
                )
        assert cached.block_generation == 0
        replacement = await AccountsRepository(cached_session).upsert(account(), merge_by_email=True)
        assert replacement.block_generation == 3


@pytest.mark.asyncio
async def test_recovery_after_rejection_commit_clears_older_runtime_hold(async_client, app_instance, monkeypatch):
    import time

    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.db.session import SessionLocal
    from tests.integration.test_proxy_transient_retry import _import_account

    account_id = await _import_account(async_client, "commit-race", "commit-race@example.invalid")
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
    from app.dependencies import get_proxy_service_for_app

    balancer = get_proxy_service_for_app(app_instance)._load_balancer
    original_commit = AsyncSession.commit
    recovered = False

    async def commit_then_recover(session):
        nonlocal recovered
        await original_commit(session)
        if recovered:
            return
        recovered = True
        async with SessionLocal() as recovery_session:
            await recovery_session.execute(
                update(Account)
                .where(Account.id == account_id)
                .values(
                    status=AccountStatus.ACTIVE,
                    blocked_at=None,
                    reset_at=None,
                    block_generation=Account.block_generation + 1,
                    rejected_model=None,
                    rejected_service_tier=None,
                )
            )
            await original_commit(recovery_session)

    monkeypatch.setattr(AsyncSession, "commit", commit_then_recover)
    await balancer.mark_rate_limit(
        account,
        {"code": "usage_limit_reached", "resets_at": int(time.time()) + 3600},
        rejected_model="gpt-5.1",
    )
    assert recovered
    selection = await balancer.select_account(model="gpt-5.1")
    assert selection.account is not None
    assert selection.account.id == account_id
    runtime = balancer._runtime[account_id]
    assert runtime.blocked_at is None
    assert runtime.reset_at is None

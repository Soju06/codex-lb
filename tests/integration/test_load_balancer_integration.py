from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timezone

import pytest

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.proxy.load_balancer import LoadBalancer
from app.modules.proxy.repo_bundle import ProxyRepositories
from app.modules.proxy.sticky_repository import StickySessionsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration


@asynccontextmanager
async def _repo_factory() -> AsyncIterator[ProxyRepositories]:
    async with SessionLocal() as session:
        yield ProxyRepositories(
            accounts=AccountsRepository(session),
            usage=UsageRepository(session),
            request_logs=RequestLogsRepository(session),
            sticky_sessions=StickySessionsRepository(session),
            api_keys=ApiKeysRepository(session),
        )


@pytest.mark.asyncio
async def test_load_balancer_skips_secondary_quota(db_setup):
    encryptor = TokenEncryptor()
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    primary_reset = now_epoch + 3600
    secondary_reset = now_epoch + 7200

    account_a = Account(
        id="acc_secondary_full",
        email="secondary_full@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-a"),
        refresh_token_encrypted=encryptor.encrypt("refresh-a"),
        id_token_encrypted=encryptor.encrypt("id-a"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    account_b = Account(
        id="acc_secondary_ok",
        email="secondary_ok@example.com",
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
        await accounts_repo.upsert(account_a)
        await accounts_repo.upsert(account_b)

        await usage_repo.add_entry(
            account_id=account_a.id,
            used_percent=10.0,
            window="primary",
            reset_at=primary_reset,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=account_a.id,
            used_percent=100.0,
            window="secondary",
            reset_at=secondary_reset,
            window_minutes=10080,
        )
        await usage_repo.add_entry(
            account_id=account_b.id,
            used_percent=20.0,
            window="primary",
            reset_at=primary_reset,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=account_b.id,
            used_percent=50.0,
            window="secondary",
            reset_at=secondary_reset,
            window_minutes=10080,
        )

        balancer = LoadBalancer(_repo_factory)
        selection = await balancer.select_account()

        assert selection.account is not None
        assert selection.account.id == account_b.id

        refreshed = await session.get(Account, account_a.id)
        assert refreshed is not None
        await session.refresh(refreshed)
        assert refreshed.status == AccountStatus.QUOTA_EXCEEDED


@pytest.mark.asyncio
async def test_load_balancer_reactivates_after_secondary_reset(db_setup):
    encryptor = TokenEncryptor()
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    primary_reset = now_epoch + 3600
    secondary_reset = now_epoch + 7200

    account = Account(
        id="acc_secondary_reset",
        email="secondary_reset@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-reset"),
        refresh_token_encrypted=encryptor.encrypt("refresh-reset"),
        id_token_encrypted=encryptor.encrypt("id-reset"),
        last_refresh=now,
        status=AccountStatus.QUOTA_EXCEEDED,
        deactivation_reason=None,
    )

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        await accounts_repo.upsert(account)

        await usage_repo.add_entry(
            account_id=account.id,
            used_percent=5.0,
            window="primary",
            reset_at=primary_reset,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=account.id,
            used_percent=0.0,
            window="secondary",
            reset_at=secondary_reset,
            window_minutes=10080,
        )

        balancer = LoadBalancer(_repo_factory)
        selection = await balancer.select_account()

        assert selection.account is not None
        assert selection.account.id == account.id

        refreshed = await session.get(Account, account.id)
        assert refreshed is not None
        await session.refresh(refreshed)
        assert refreshed.status == AccountStatus.ACTIVE


@pytest.mark.asyncio
async def test_load_balancer_treats_weekly_only_primary_as_quota_window(db_setup):
    encryptor = TokenEncryptor()
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    weekly_reset = now_epoch + 7200
    plus_primary_reset = now_epoch + 3600
    plus_secondary_reset = now_epoch + 7200

    free_account = Account(
        id="acc_free_weekly_full",
        email="free_weekly_full@example.com",
        plan_type="free",
        access_token_encrypted=encryptor.encrypt("free-access"),
        refresh_token_encrypted=encryptor.encrypt("free-refresh"),
        id_token_encrypted=encryptor.encrypt("free-id"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    plus_account = Account(
        id="acc_plus_available",
        email="plus_available@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("plus-access"),
        refresh_token_encrypted=encryptor.encrypt("plus-refresh"),
        id_token_encrypted=encryptor.encrypt("plus-id"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        await accounts_repo.upsert(free_account)
        await accounts_repo.upsert(plus_account)

        await usage_repo.add_entry(
            account_id=free_account.id,
            used_percent=100.0,
            window="primary",
            reset_at=weekly_reset,
            window_minutes=10080,
        )
        await usage_repo.add_entry(
            account_id=plus_account.id,
            used_percent=20.0,
            window="primary",
            reset_at=plus_primary_reset,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=plus_account.id,
            used_percent=20.0,
            window="secondary",
            reset_at=plus_secondary_reset,
            window_minutes=10080,
        )

        balancer = LoadBalancer(_repo_factory)
        selection = await balancer.select_account()

        assert selection.account is not None
        assert selection.account.id == plus_account.id

        refreshed_free = await session.get(Account, free_account.id)
        assert refreshed_free is not None
        await session.refresh(refreshed_free)
        assert refreshed_free.status == AccountStatus.QUOTA_EXCEEDED

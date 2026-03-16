from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta, timezone

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
from app.modules.usage.repository import AdditionalUsageRepository, UsageRepository

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
            additional_usage=AdditionalUsageRepository(session),
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


@pytest.mark.asyncio
async def test_load_balancer_select_account_uses_cached_rows_for_detached_accounts(db_setup):
    encryptor = TokenEncryptor()
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    account = Account(
        id="acc_detached_refresh",
        email="detached-refresh@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-detached"),
        refresh_token_encrypted=encryptor.encrypt("refresh-detached"),
        id_token_encrypted=encryptor.encrypt("id-detached"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        await accounts_repo.upsert(account)
        await usage_repo.add_entry(
            account_id=account.id,
            used_percent=10.0,
            window="primary",
            reset_at=now_epoch + 300,
            window_minutes=5,
        )

    balancer = LoadBalancer(_repo_factory)
    selection = await balancer.select_account()

    assert selection.account is not None
    assert selection.account.id == account.id
    assert selection.account.plan_type == "plus"


@pytest.mark.asyncio
async def test_load_balancer_prefers_newer_weekly_primary_over_stale_secondary(db_setup):
    encryptor = TokenEncryptor()
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    stale_reset = now_epoch + 1800
    weekly_reset = now_epoch + 7200
    plus_primary_reset = now_epoch + 3600
    plus_secondary_reset = now_epoch + 7200

    free_account = Account(
        id="acc_free_weekly_stale_secondary",
        email="free_weekly_stale_secondary@example.com",
        plan_type="free",
        access_token_encrypted=encryptor.encrypt("free-stale-access"),
        refresh_token_encrypted=encryptor.encrypt("free-stale-refresh"),
        id_token_encrypted=encryptor.encrypt("free-stale-id"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    plus_account = Account(
        id="acc_plus_weekly_control",
        email="plus_weekly_control@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("plus-control-access"),
        refresh_token_encrypted=encryptor.encrypt("plus-control-refresh"),
        id_token_encrypted=encryptor.encrypt("plus-control-id"),
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
            used_percent=15.0,
            window="secondary",
            reset_at=stale_reset,
            window_minutes=10080,
            recorded_at=now - timedelta(days=2),
        )
        await usage_repo.add_entry(
            account_id=free_account.id,
            used_percent=100.0,
            window="primary",
            reset_at=weekly_reset,
            window_minutes=10080,
            recorded_at=now,
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


@pytest.mark.asyncio
async def test_load_balancer_filters_accounts_by_persisted_additional_usage(db_setup):
    encryptor = TokenEncryptor()
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())

    exhausted_account = Account(
        id="acc_additional_full",
        email="additional_full@example.com",
        plan_type="pro",
        access_token_encrypted=encryptor.encrypt("access-full"),
        refresh_token_encrypted=encryptor.encrypt("refresh-full"),
        id_token_encrypted=encryptor.encrypt("id-full"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    eligible_account = Account(
        id="acc_additional_ok",
        email="additional_ok@example.com",
        plan_type="pro",
        access_token_encrypted=encryptor.encrypt("access-ok"),
        refresh_token_encrypted=encryptor.encrypt("refresh-ok"),
        id_token_encrypted=encryptor.encrypt("id-ok"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        additional_repo = AdditionalUsageRepository(session)
        await accounts_repo.upsert(exhausted_account)
        await accounts_repo.upsert(eligible_account)

        for account, used_percent in ((exhausted_account, 40.0), (eligible_account, 20.0)):
            await usage_repo.add_entry(
                account_id=account.id,
                used_percent=used_percent,
                window="primary",
                reset_at=now_epoch + 300,
                window_minutes=5,
                recorded_at=now,
            )

        await additional_repo.add_entry(
            account_id=exhausted_account.id,
            limit_name="codex_other",
            metered_feature="codex_bengalfox",
            window="primary",
            used_percent=100.0,
            reset_at=now_epoch + 300,
            window_minutes=5,
            recorded_at=now,
        )
        await additional_repo.add_entry(
            account_id=eligible_account.id,
            limit_name="codex_other",
            metered_feature="codex_bengalfox",
            window="primary",
            used_percent=25.0,
            reset_at=now_epoch + 300,
            window_minutes=5,
            recorded_at=now,
        )

    balancer = LoadBalancer(_repo_factory)
    selection = await balancer.select_account(additional_limit_name="codex_spark")

    assert selection.account is not None
    assert selection.account.id == eligible_account.id


@pytest.mark.asyncio
async def test_load_balancer_prefers_requested_account_when_eligible(db_setup):
    encryptor = TokenEncryptor()
    now = utcnow()
    reset_at = int(now.replace(tzinfo=timezone.utc).timestamp()) + 3600

    preferred = Account(
        id="acc_preferred_eligible",
        email="preferred-eligible@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("preferred-access"),
        refresh_token_encrypted=encryptor.encrypt("preferred-refresh"),
        id_token_encrypted=encryptor.encrypt("preferred-id"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    other = Account(
        id="acc_preferred_other",
        email="preferred-other@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("other-access"),
        refresh_token_encrypted=encryptor.encrypt("other-refresh"),
        id_token_encrypted=encryptor.encrypt("other-id"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        await accounts_repo.upsert(preferred)
        await accounts_repo.upsert(other)
        await usage_repo.add_entry(
            account_id=preferred.id,
            used_percent=80.0,
            window="primary",
            reset_at=reset_at,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=other.id,
            used_percent=5.0,
            window="primary",
            reset_at=reset_at,
            window_minutes=300,
        )

    balancer = LoadBalancer(_repo_factory)
    selection = await balancer.select_account(preferred_account_id=preferred.id)

    assert selection.account is not None
    assert selection.account.id == preferred.id


@pytest.mark.asyncio
async def test_load_balancer_falls_back_when_preferred_account_unavailable(db_setup):
    encryptor = TokenEncryptor()
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    reset_at = now_epoch + 3600

    preferred = Account(
        id="acc_preferred_blocked",
        email="preferred-blocked@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("preferred-blocked-access"),
        refresh_token_encrypted=encryptor.encrypt("preferred-blocked-refresh"),
        id_token_encrypted=encryptor.encrypt("preferred-blocked-id"),
        last_refresh=now,
        status=AccountStatus.RATE_LIMITED,
        deactivation_reason=None,
        reset_at=reset_at,
    )
    fallback = Account(
        id="acc_preferred_fallback",
        email="preferred-fallback@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("fallback-access"),
        refresh_token_encrypted=encryptor.encrypt("fallback-refresh"),
        id_token_encrypted=encryptor.encrypt("fallback-id"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        await accounts_repo.upsert(preferred)
        await accounts_repo.upsert(fallback)
        await usage_repo.add_entry(
            account_id=preferred.id,
            used_percent=10.0,
            window="primary",
            reset_at=reset_at,
            window_minutes=300,
        )
        await usage_repo.add_entry(
            account_id=fallback.id,
            used_percent=40.0,
            window="primary",
            reset_at=reset_at,
            window_minutes=300,
        )

    balancer = LoadBalancer(_repo_factory)
    selection = await balancer.select_account(preferred_account_id=preferred.id)

    assert selection.account is not None
    assert selection.account.id == fallback.id

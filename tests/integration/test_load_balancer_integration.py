from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.balancer import HEALTH_TIER_DRAINING
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus, StickySessionKind
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.proxy._load_balancer.tunables import RUNTIME_PRESSURE_USED_PERCENT_CEILING
from app.modules.proxy.account_cache import get_account_selection_cache
from app.modules.proxy.affinity import _codex_session_selection_key
from app.modules.proxy.load_balancer import (
    MAX_LEASE_ESTIMATE_TOKENS,
    LoadBalancer,
    _state_from_account,
)
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
async def test_load_balancer_deprioritizes_secondary_usage_without_persisting_quota_exceeded(db_setup):
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
        assert refreshed.status == AccountStatus.ACTIVE


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
@pytest.mark.parametrize("has_reset_metadata", [True, False])
@pytest.mark.parametrize("has_block_marker", [True, False])
async def test_load_balancer_does_not_reactivate_explicit_quota_from_fresh_exhausted_secondary_usage(
    db_setup, has_reset_metadata, has_block_marker
):
    encryptor = TokenEncryptor()
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    fallback_reset = now_epoch - 1
    secondary_reset = now_epoch + 5 * 24 * 3600 if has_reset_metadata else None

    exhausted = Account(
        id="acc_explicit_quota_still_full",
        email="explicit_quota_still_full@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-full"),
        refresh_token_encrypted=encryptor.encrypt("refresh-full"),
        id_token_encrypted=encryptor.encrypt("id-full"),
        last_refresh=now,
        status=AccountStatus.QUOTA_EXCEEDED,
        deactivation_reason=None,
        reset_at=fallback_reset,
        blocked_at=now_epoch - 3601 if has_block_marker else None,
    )
    available = Account(
        id="acc_explicit_quota_replacement",
        email="explicit_quota_replacement@example.com",
        plan_type="plus",
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
        await accounts_repo.upsert(exhausted)
        await accounts_repo.upsert(available)

        for account, primary_used, secondary_used in (
            (exhausted, 15.0, 100.0),
            (available, 20.0, 50.0),
        ):
            await usage_repo.add_entry(
                account_id=account.id,
                used_percent=primary_used,
                window="primary",
                reset_at=now_epoch + 300,
                window_minutes=300,
                recorded_at=now,
                credits_has=False,
                credits_unlimited=False,
                credits_balance=0.0,
            )
            await usage_repo.add_entry(
                account_id=account.id,
                used_percent=secondary_used,
                window="secondary",
                reset_at=secondary_reset,
                window_minutes=10080,
                recorded_at=now,
            )

        for _ in range(2):
            selection = await LoadBalancer(_repo_factory).select_account()
            assert selection.account is not None
            assert selection.account.id == available.id

        refreshed = await session.get(Account, exhausted.id)
        assert refreshed is not None
        await session.refresh(refreshed)
        assert refreshed.status == AccountStatus.QUOTA_EXCEEDED
        assert refreshed.reset_at == secondary_reset

        await usage_repo.add_entry(
            account_id=exhausted.id,
            used_percent=25.0,
            window="secondary",
            reset_at=secondary_reset + 1 if secondary_reset is not None else None,
            window_minutes=10080,
            recorded_at=utcnow(),
        )
        recovered = await LoadBalancer(_repo_factory).select_account(account_ids={exhausted.id})
        assert recovered.account is not None
        assert recovered.account.id == exhausted.id
        await session.refresh(refreshed)
        assert refreshed.status == AccountStatus.ACTIVE
        assert refreshed.blocked_at is None


@pytest.mark.asyncio
async def test_load_balancer_treats_weekly_only_primary_as_advisory_quota_window(db_setup):
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
        assert refreshed_free.status == AccountStatus.ACTIVE


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
        assert refreshed_free.status == AccountStatus.ACTIVE


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
async def test_load_balancer_burn_first_additional_quota_ignores_standard_quota_exhaustion(db_setup):
    get_account_selection_cache().invalidate()
    encryptor = TokenEncryptor()
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())

    spark_account = Account(
        id="acc_spark_standard_full",
        email="spark_standard_full@example.com",
        plan_type="pro",
        access_token_encrypted=encryptor.encrypt("access-spark"),
        refresh_token_encrypted=encryptor.encrypt("refresh-spark"),
        id_token_encrypted=encryptor.encrypt("id-spark"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        additional_repo = AdditionalUsageRepository(session)
        await accounts_repo.upsert(spark_account)

        for window, window_minutes in (("primary", 300), ("secondary", 10080)):
            await usage_repo.add_entry(
                account_id=spark_account.id,
                used_percent=100.0,
                window=window,
                reset_at=now_epoch + 3600,
                window_minutes=window_minutes,
                recorded_at=now,
            )
            await additional_repo.add_entry(
                account_id=spark_account.id,
                limit_name="codex_other",
                metered_feature="codex_bengalfox",
                window=window,
                used_percent=20.0,
                reset_at=now_epoch + 3600,
                window_minutes=window_minutes,
                recorded_at=now,
            )

    balancer = LoadBalancer(_repo_factory)
    selection = await balancer.select_account(additional_limit_name="codex_spark")

    assert selection.account is not None
    assert selection.account.id == spark_account.id


@pytest.mark.asyncio
async def test_load_balancer_selects_best_draining_account_when_all_are_draining(db_setup):
    encryptor = TokenEncryptor()
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    primary_reset = now_epoch + 3600
    secondary_reset = now_epoch + 7200

    account_a = Account(
        id="acc_all_draining_a",
        email="all_draining_a@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-drain-a"),
        refresh_token_encrypted=encryptor.encrypt("refresh-drain-a"),
        id_token_encrypted=encryptor.encrypt("id-drain-a"),
        last_refresh=now,
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )
    account_b = Account(
        id="acc_all_draining_b",
        email="all_draining_b@example.com",
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access-drain-b"),
        refresh_token_encrypted=encryptor.encrypt("refresh-drain-b"),
        id_token_encrypted=encryptor.encrypt("id-drain-b"),
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
            used_percent=94.0,
            window="primary",
            reset_at=primary_reset,
            window_minutes=300,
            recorded_at=now,
        )
        await usage_repo.add_entry(
            account_id=account_a.id,
            used_percent=96.0,
            window="secondary",
            reset_at=secondary_reset,
            window_minutes=10080,
            recorded_at=now,
        )
        await usage_repo.add_entry(
            account_id=account_b.id,
            used_percent=88.0,
            window="primary",
            reset_at=primary_reset,
            window_minutes=300,
            recorded_at=now,
        )
        await usage_repo.add_entry(
            account_id=account_b.id,
            used_percent=93.0,
            window="secondary",
            reset_at=secondary_reset,
            window_minutes=10080,
            recorded_at=now,
        )

    balancer = LoadBalancer(_repo_factory)
    selection = await balancer.select_account(routing_strategy="usage_weighted")

    assert selection.account is not None
    assert selection.account.id == account_b.id
    assert balancer._runtime[account_a.id].health_tier == HEALTH_TIER_DRAINING
    assert balancer._runtime[account_b.id].health_tier == HEALTH_TIER_DRAINING


@pytest.mark.asyncio
async def test_load_balancer_fill_first_cycles_through_accounts(db_setup):
    encryptor = TokenEncryptor()
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    primary_reset = now_epoch + 3600
    secondary_reset = now_epoch + 7 * 24 * 3600

    accounts: list[Account] = []
    for suffix in ("a", "b", "c"):
        accounts.append(
            Account(
                id=f"acc_fill_first_{suffix}",
                email=f"fill_first_{suffix}@example.com",
                plan_type="plus",
                access_token_encrypted=encryptor.encrypt(f"access-{suffix}"),
                refresh_token_encrypted=encryptor.encrypt(f"refresh-{suffix}"),
                id_token_encrypted=encryptor.encrypt(f"id-{suffix}"),
                last_refresh=now,
                status=AccountStatus.ACTIVE,
                deactivation_reason=None,
            )
        )

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        for account in accounts:
            await accounts_repo.upsert(account)
        for account, primary, secondary in (
            (accounts[0], 0.0, 0.0),
            (accounts[1], 0.0, 0.0),
            (accounts[2], 0.0, 0.0),
        ):
            await usage_repo.add_entry(
                account_id=account.id,
                used_percent=primary,
                window="primary",
                reset_at=primary_reset,
                window_minutes=300,
                recorded_at=now,
            )
            await usage_repo.add_entry(
                account_id=account.id,
                used_percent=secondary,
                window="secondary",
                reset_at=secondary_reset,
                window_minutes=10080,
                recorded_at=now,
            )

    balancer = LoadBalancer(_repo_factory)

    first = await balancer.select_account(routing_strategy="fill_first")
    assert first.account is not None
    assert first.account.id == accounts[0].id

    again = await balancer.select_account(routing_strategy="fill_first")
    assert again.account is not None
    assert again.account.id == accounts[0].id

    async with SessionLocal() as session:
        usage_repo = UsageRepository(session)
        await usage_repo.add_entry(
            account_id=accounts[0].id,
            used_percent=60.0,
            window="primary",
            reset_at=primary_reset,
            window_minutes=300,
            recorded_at=now,
        )

    second = await balancer.select_account(routing_strategy="fill_first")
    assert second.account is not None
    assert second.account.id == accounts[0].id

    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        accounts[0].status = AccountStatus.RATE_LIMITED
        accounts[0].reset_at = primary_reset
        await accounts_repo.upsert(accounts[0])
        await usage_repo.add_entry(
            account_id=accounts[1].id,
            used_percent=70.0,
            window="primary",
            reset_at=primary_reset,
            window_minutes=300,
            recorded_at=now,
        )
    balancer._runtime.clear()

    third = await balancer.select_account(routing_strategy="fill_first")
    assert third.account is not None
    assert third.account.id == accounts[1].id


# Health tiers read persisted usage only; turning soft drain off isolates the lease-pressure path.
_NO_SOFT_DRAIN = SimpleNamespace(soft_drain_enabled=False)


async def _seed_plus_accounts_with_weekly_usage(
    weekly_used: dict[str, float],
) -> list[Account]:
    """Persist Plus accounts with low 5h usage and the given weekly usage, similar resets."""
    encryptor = TokenEncryptor()
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    accounts: list[Account] = []
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        for index, (account_id, secondary_used) in enumerate(weekly_used.items()):
            account = Account(
                id=account_id,
                email=f"{account_id}@example.com",
                plan_type="plus",
                access_token_encrypted=encryptor.encrypt(f"access-{account_id}"),
                refresh_token_encrypted=encryptor.encrypt(f"refresh-{account_id}"),
                id_token_encrypted=encryptor.encrypt(f"id-{account_id}"),
                last_refresh=now,
                status=AccountStatus.ACTIVE,
                deactivation_reason=None,
            )
            await accounts_repo.upsert(account)
            accounts.append(account)
            await usage_repo.add_entry(
                account_id=account_id,
                used_percent=10.0,
                window="primary",
                reset_at=now_epoch + 3600,
                window_minutes=300,
                recorded_at=now,
            )
            await usage_repo.add_entry(
                account_id=account_id,
                used_percent=secondary_used,
                window="secondary",
                reset_at=now_epoch + 3 * 24 * 3600 + index * 60,
                window_minutes=10080,
                recorded_at=now,
            )
    return accounts


@pytest.mark.asyncio
async def test_open_stream_leases_keep_relative_availability_ranked_by_persisted_usage(db_setup):
    await _seed_plus_accounts_with_weekly_usage({"acc_lease_heavy": 95.0, "acc_lease_light": 38.0})
    balancer = LoadBalancer(_repo_factory)

    for account_id in ("acc_lease_heavy", "acc_lease_light"):
        lease = await balancer.acquire_account_lease(
            account_id, kind="stream", estimated_tokens=float(MAX_LEASE_ESTIMATE_TOKENS)
        )
        assert lease is not None

    for _ in range(5):
        selection = await balancer.select_account(
            routing_strategy="relative_availability", dashboard_settings=_NO_SOFT_DRAIN
        )
        assert selection.account is not None
        assert selection.account.id == "acc_lease_light"


@pytest.mark.asyncio
async def test_open_stream_leases_let_sticky_reallocation_leave_exhausted_account(db_setup):
    await _seed_plus_accounts_with_weekly_usage({"acc_sticky_heavy": 95.0, "acc_sticky_light": 38.0})
    raw_session = "thread-lease-pressure"
    sticky_key = _codex_session_selection_key(raw_session)
    async with SessionLocal() as session:
        await StickySessionsRepository(session).upsert(
            sticky_key, "acc_sticky_heavy", kind=StickySessionKind.CODEX_SESSION
        )
    balancer = LoadBalancer(_repo_factory)

    for account_id in ("acc_sticky_heavy", "acc_sticky_light"):
        lease = await balancer.acquire_account_lease(
            account_id, kind="stream", estimated_tokens=float(MAX_LEASE_ESTIMATE_TOKENS)
        )
        assert lease is not None

    selection = await balancer.select_account(
        sticky_key,
        sticky_kind=StickySessionKind.CODEX_SESSION,
        sticky_source="session_header",
        legacy_sticky_key=raw_session,
        reallocate_sticky=False,
        routing_strategy="relative_availability",
        secondary_budget_threshold_pct=95.0,
        dashboard_settings=_NO_SOFT_DRAIN,
    )

    assert selection.account is not None
    assert selection.account.id == "acc_sticky_light"
    async with SessionLocal() as session:
        entry = await StickySessionsRepository(session).get_entry(sticky_key, kind=StickySessionKind.CODEX_SESSION)
    assert entry is not None
    assert entry.account_id == "acc_sticky_light"


@pytest.mark.asyncio
async def test_max_lease_adds_weight_points_and_never_crosses_ceiling(db_setup):
    await _seed_plus_accounts_with_weekly_usage(
        {"acc_pressure_mid": 38.0, "acc_pressure_high": 98.9, "acc_pressure_full": 100.0}
    )
    balancer = LoadBalancer(_repo_factory)
    async with SessionLocal() as session:
        accounts = {a.id: a for a in await AccountsRepository(session).list_accounts()}
        secondary = await UsageRepository(session).latest_by_account("secondary")
        primary = await UsageRepository(session).latest_by_account("primary")

    for account_id in accounts:
        lease = await balancer.acquire_account_lease(
            account_id, kind="response_create", estimated_tokens=float(MAX_LEASE_ESTIMATE_TOKENS)
        )
        assert lease is not None

    def state_for(account_id: str):
        return _state_from_account(
            account=accounts[account_id],
            primary_entry=primary[account_id],
            secondary_entry=secondary[account_id],
            runtime=balancer._runtime[account_id],
        )

    tunables = balancer.current_routing_tunables()
    expected_points = tunables.lease_token_weight + tunables.inflight_penalty_pct
    mid = state_for("acc_pressure_mid")
    assert mid.persisted_secondary_used_percent == 38.0
    assert mid.secondary_used_percent == pytest.approx(38.0 + expected_points)
    # Isolate the lease term: weight 1.0 default -> exactly one point for a max lease.
    assert tunables.lease_token_weight == 1.0
    assert state_for("acc_pressure_high").secondary_used_percent == RUNTIME_PRESSURE_USED_PERCENT_CEILING
    assert RUNTIME_PRESSURE_USED_PERCENT_CEILING == 99.0
    assert state_for("acc_pressure_full").secondary_used_percent == 100.0

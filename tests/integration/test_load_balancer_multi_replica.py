"""Two-replica regression tests for cross-replica rate-limit cooldown enforcement.

Each test simulates two replicas as two independently constructed
``LoadBalancer`` instances sharing one database (each with its own runtime
state and repository sessions). Before this change, a peer replica that never
observed a 429 recomputed the account as ``ACTIVE`` from usage below 100% and
CAS-wrote the flip back, erasing the marking replica's rate-limit state.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timezone

import pytest

from app.core.balancer import RATE_LIMITED_MIN_COOLDOWN_SECONDS
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository
from app.modules.api_keys.repository import ApiKeysRepository
from app.modules.proxy.load_balancer import LoadBalancer, RuntimeState
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


def _make_account(
    suffix: str,
    *,
    status: AccountStatus = AccountStatus.ACTIVE,
    blocked_at: int | None = None,
    reset_at: int | None = None,
    plan_type: str = "plus",
) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=f"acc_mr_{suffix}",
        email=f"mr_{suffix}@example.com",
        plan_type=plan_type,
        access_token_encrypted=encryptor.encrypt(f"access-{suffix}"),
        refresh_token_encrypted=encryptor.encrypt(f"refresh-{suffix}"),
        id_token_encrypted=encryptor.encrypt(f"id-{suffix}"),
        last_refresh=utcnow(),
        status=status,
        deactivation_reason=None,
        blocked_at=blocked_at,
        reset_at=reset_at,
    )


async def _seed_accounts_with_usage(*accounts_with_usage: tuple[Account, float, float]) -> None:
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        for account, primary_used, secondary_used in accounts_with_usage:
            await accounts_repo.upsert(account)
            await usage_repo.add_entry(
                account_id=account.id,
                used_percent=primary_used,
                window="primary",
                reset_at=now_epoch + 3600,
                window_minutes=300,
                recorded_at=now,
            )
            await usage_repo.add_entry(
                account_id=account.id,
                used_percent=secondary_used,
                window="secondary",
                reset_at=now_epoch + 7 * 24 * 3600,
                window_minutes=10080,
                recorded_at=now,
            )


async def _fetch_account(account_id: str) -> Account:
    async with SessionLocal() as session:
        account = await session.get(Account, account_id)
        assert account is not None
        await session.refresh(account)
        return account


@pytest.mark.asyncio
async def test_peer_replica_honors_metadata_free_rate_limit_cooldown(db_setup):
    """Regression: on main the peer flips the row back to ACTIVE and selects it.

    The 429 carries no resets metadata and no Retry-After hint, so before this
    change nothing durable recorded the cooldown; replica B recomputed the
    account as ACTIVE from sub-100% usage and CAS-wrote the flip back.
    """
    limited = _make_account("limited")
    healthy = _make_account("healthy")
    # fill_first prefers the higher primary usage, so replica B would pick the
    # rate-limited account if it (incorrectly) considered it selectable.
    await _seed_accounts_with_usage((limited, 50.0, 40.0), (healthy, 20.0, 10.0))

    balancer_a = LoadBalancer(_repo_factory)
    before_mark = time.time()
    await balancer_a.mark_rate_limit(limited, {"message": "Rate limit exceeded."})

    row = await _fetch_account(limited.id)
    assert row.status == AccountStatus.RATE_LIMITED
    assert row.blocked_at is not None
    assert row.reset_at is not None
    assert row.reset_at >= int(before_mark + RATE_LIMITED_MIN_COOLDOWN_SECONDS) - 1
    persisted_reset_at = row.reset_at

    # Replica B: fresh instance, empty runtime state, same database.
    balancer_b = LoadBalancer(_repo_factory)
    selection = await balancer_b.select_account(routing_strategy="fill_first")

    assert selection.account is not None
    assert selection.account.id == healthy.id

    row = await _fetch_account(limited.id)
    assert row.status == AccountStatus.RATE_LIMITED
    assert row.reset_at == persisted_reset_at
    assert row.blocked_at is not None


@pytest.mark.asyncio
async def test_peer_replica_honors_retry_after_hint_cooldown(db_setup):
    """Regression: Retry-After cooldowns were invisible to peer replicas."""
    limited = _make_account("hint_limited")
    healthy = _make_account("hint_healthy")
    await _seed_accounts_with_usage((limited, 50.0, 40.0), (healthy, 20.0, 10.0))

    balancer_a = LoadBalancer(_repo_factory)
    before_mark = time.time()
    await balancer_a.mark_rate_limit(limited, {"message": "Rate limit exceeded. Try again in 20m."})

    row = await _fetch_account(limited.id)
    assert row.status == AccountStatus.RATE_LIMITED
    assert row.reset_at is not None
    assert row.reset_at == pytest.approx(before_mark + 1200.0, abs=5.0)

    balancer_b = LoadBalancer(_repo_factory)
    selection = await balancer_b.select_account(routing_strategy="fill_first")

    assert selection.account is not None
    assert selection.account.id == healthy.id

    row = await _fetch_account(limited.id)
    assert row.status == AccountStatus.RATE_LIMITED


async def _seed_free_accounts_with_monthly_usage(*accounts_with_usage: tuple[Account, float]) -> None:
    now = utcnow()
    now_epoch = int(now.replace(tzinfo=timezone.utc).timestamp())
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        usage_repo = UsageRepository(session)
        for account, monthly_used in accounts_with_usage:
            await accounts_repo.upsert(account)
            await usage_repo.add_entry(
                account_id=account.id,
                used_percent=monthly_used,
                window="monthly",
                reset_at=now_epoch + 30 * 24 * 3600,
                window_minutes=43200,
                recorded_at=now,
            )


@pytest.mark.asyncio
async def test_peer_replica_holds_free_plan_rate_limited_account_despite_fresh_monthly_quota(db_setup):
    """Regression (codex P1): on a zero-primary-capacity plan (free) the
    recovery rewrite in ``_state_from_account`` flipped ``status_seed`` to
    ACTIVE before the peer-recovery guards ran, so a peer replica with fresh
    monthly usage below 100% flipped a monthly-only rate-limited account
    straight back to ACTIVE, erasing the persisted cooldown."""
    limited = _make_account("free_limited", plan_type="free")
    healthy = _make_account("free_healthy", plan_type="free")
    # fill_first prefers the higher long-window usage, so replica B would pick
    # the rate-limited account if it (incorrectly) considered it selectable.
    await _seed_free_accounts_with_monthly_usage((limited, 50.0), (healthy, 10.0))

    balancer_a = LoadBalancer(_repo_factory)
    await balancer_a.mark_rate_limit(limited, {"message": "Rate limit exceeded."})

    row = await _fetch_account(limited.id)
    assert row.status == AccountStatus.RATE_LIMITED
    assert row.blocked_at is not None
    assert row.reset_at is not None
    persisted_reset_at = row.reset_at

    # Replica B: fresh instance, empty runtime state, same database.
    balancer_b = LoadBalancer(_repo_factory)
    selection = await balancer_b.select_account(routing_strategy="fill_first")

    assert selection.account is not None
    assert selection.account.id == healthy.id

    row = await _fetch_account(limited.id)
    assert row.status == AccountStatus.RATE_LIMITED
    assert row.reset_at == persisted_reset_at
    assert row.blocked_at is not None


@pytest.mark.asyncio
async def test_legacy_free_plan_rate_limited_row_is_floored_despite_fresh_monthly_quota(db_setup):
    """Regression (codex P2): a legacy RATE_LIMITED row (reset_at NULL) with a
    recent blocked_at must be held by the minimum-cooldown floor even on a
    zero-primary-capacity plan where the recovery rewrite previously bypassed
    the floor."""
    now_epoch = int(time.time())
    limited = _make_account(
        "free_legacy_limited",
        status=AccountStatus.RATE_LIMITED,
        blocked_at=now_epoch - 5,
        reset_at=None,
        plan_type="free",
    )
    healthy = _make_account("free_legacy_healthy", plan_type="free")
    await _seed_free_accounts_with_monthly_usage((limited, 50.0), (healthy, 10.0))

    balancer = LoadBalancer(_repo_factory)
    selection = await balancer.select_account(routing_strategy="fill_first")

    assert selection.account is not None
    assert selection.account.id == healthy.id

    row = await _fetch_account(limited.id)
    assert row.status == AccountStatus.RATE_LIMITED


@pytest.mark.asyncio
async def test_legacy_rate_limited_row_without_reset_at_is_floored(db_setup):
    """A RATE_LIMITED row with blocked_at set but reset_at NULL (written before
    cooldown persistence) is held out of rotation for the minimum floor window."""
    now_epoch = int(time.time())
    limited = _make_account(
        "legacy_limited",
        status=AccountStatus.RATE_LIMITED,
        blocked_at=now_epoch - 5,
        reset_at=None,
    )
    healthy = _make_account("legacy_healthy")
    await _seed_accounts_with_usage((limited, 50.0, 40.0), (healthy, 20.0, 10.0))

    balancer = LoadBalancer(_repo_factory)
    selection = await balancer.select_account(routing_strategy="fill_first")

    assert selection.account is not None
    assert selection.account.id == healthy.id

    row = await _fetch_account(limited.id)
    assert row.status == AccountStatus.RATE_LIMITED


@pytest.mark.asyncio
async def test_stale_runtime_cooldown_does_not_unlock_early_recovery_of_peer_marked_block(db_setup):
    """Regression (codex P2): leftover runtime cooldown state from an earlier
    429 must not count as having observed the current 429. Replica A holds an
    expired runtime cooldown from an old block; a peer later re-marked the
    account RATE_LIMITED with a fresh blocked_at and a 20-minute reset_at, and
    usage was recorded after that new block. Replica A must keep honoring the
    persisted deadline instead of recovering the account early."""
    now_epoch = int(time.time())
    limited = _make_account(
        "stale_runtime_limited",
        status=AccountStatus.RATE_LIMITED,
        blocked_at=now_epoch - 5,
        reset_at=now_epoch + 1200,
    )
    healthy = _make_account("stale_runtime_healthy")
    # Usage rows are recorded "now", i.e. after the peer's blocked_at, which is
    # exactly the evidence the stale-runtime gate previously mistook for a
    # local post-block recovery signal.
    await _seed_accounts_with_usage((limited, 50.0, 40.0), (healthy, 20.0, 10.0))

    balancer_a = LoadBalancer(_repo_factory)
    # Leftover runtime state from an earlier 429 of the same account: the
    # cooldown elapsed long ago and the runtime block marker predates the
    # peer's current persisted blocked_at.
    balancer_a._runtime[limited.id] = RuntimeState(
        cooldown_until=time.time() - 600.0,
        blocked_at=time.time() - 900.0,
    )

    selection = await balancer_a.select_account(routing_strategy="fill_first")

    assert selection.account is not None
    assert selection.account.id == healthy.id

    row = await _fetch_account(limited.id)
    assert row.status == AccountStatus.RATE_LIMITED
    assert row.reset_at == now_epoch + 1200
    assert row.blocked_at == now_epoch - 5


@pytest.mark.asyncio
async def test_legacy_rate_limited_row_recovers_after_floor_elapses(db_setup):
    """Once the floor window has elapsed, a fresh replica may recover the
    legacy row back to ACTIVE through the CAS-guarded persistence path."""
    now_epoch = int(time.time())
    limited = _make_account(
        "recovered_limited",
        status=AccountStatus.RATE_LIMITED,
        blocked_at=now_epoch - 3600,
        reset_at=None,
    )
    healthy = _make_account("recovered_healthy")
    await _seed_accounts_with_usage((limited, 50.0, 40.0), (healthy, 20.0, 10.0))

    balancer = LoadBalancer(_repo_factory)
    selection = await balancer.select_account(routing_strategy="fill_first")

    assert selection.account is not None
    assert selection.account.id == limited.id

    row = await _fetch_account(limited.id)
    assert row.status == AccountStatus.ACTIVE

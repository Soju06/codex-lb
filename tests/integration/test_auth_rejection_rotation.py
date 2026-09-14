from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import pytest

import app.modules.accounts.repository as accounts_repository
from app.core.auth.refresh import TokenRefreshResult
from app.core.balancer import PERMANENT_FAILURE_CODES
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.db.snapshot import clone_row
from app.dependencies import get_proxy_service_for_app
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy import account_cache
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration

_REJECTED_REASON = PERMANENT_FAILURE_CODES["account_auth_invalidated"]


async def _create_account(*, status: AccountStatus = AccountStatus.ACTIVE, reason: str | None = None) -> Account:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = Account(
            id="auth-rotation",
            email="auth-rotation@example.com",
            plan_type="plus",
            access_token_encrypted=encryptor.encrypt("old-access"),
            refresh_token_encrypted=encryptor.encrypt("old-refresh"),
            id_token_encrypted=encryptor.encrypt("old-id"),
            last_refresh=utcnow(),
            status=status,
            deactivation_reason=reason,
        )
        session.add(account)
        await session.commit()
        return clone_row(account)


@pytest.mark.asyncio
@pytest.mark.parametrize("order", ["rejection_first", "rotation_first", "delayed_rejection_mark"])
async def test_refresh_rotation_reconciles_concurrent_rejection_and_restores_selection(
    async_client, monkeypatch, order
):
    stale = await _create_account()
    selection_cache = account_cache.AccountSelectionCache(ttl_seconds=60)
    monkeypatch.setattr(account_cache, "_account_selection_cache", selection_cache)
    balancer = get_proxy_service_for_app(async_client._transport.app)._load_balancer
    monkeypatch.setattr(balancer, "_selection_inputs_cache", selection_cache)
    routing_cache = account_cache.get_routing_availability_cache()
    await routing_cache.refresh_from_db()
    peer_cache = account_cache.RoutingAvailabilityCache(SessionLocal)
    refresh_started = asyncio.Event()
    finish_refresh = asyncio.Event()
    rejection_committed = asyncio.Event()
    finish_rejection = asyncio.Event()

    async def refresh_tokens(self, token, *, account):
        assert token == "old-refresh"
        refresh_started.set()
        await finish_refresh.wait()
        return TokenRefreshResult(
            access_token="new-access",
            refresh_token="new-refresh",
            id_token="new-id",
            account_id=None,
            plan_type="plus",
            email=None,
        )

    original_update = AccountsRepository.update_status_if_current

    async def update_status(self, *args, **kwargs):
        updated = await original_update(self, *args, **kwargs)
        if order == "delayed_rejection_mark" and updated:
            rejection_committed.set()
            await finish_rejection.wait()
        return updated

    monkeypatch.setattr(AuthManager, "_refresh_tokens", refresh_tokens)
    monkeypatch.setattr(AccountsRepository, "update_status_if_current", update_status)
    async with SessionLocal() as refresh_session:
        manager = AuthManager(AccountsRepository(refresh_session))
        refresh_task = asyncio.create_task(manager.refresh_account(clone_row(stale)))
        rejection_task = None
        try:
            await asyncio.wait_for(refresh_started.wait(), 2)
            if order == "rejection_first":
                assert await balancer.mark_permanent_failure(clone_row(stale), "account_auth_invalidated")
                await routing_cache.refresh_from_db()
                await peer_cache.refresh_from_db()
                assert routing_cache.is_unavailable(stale.id)
                assert peer_cache.is_unavailable(stale.id)
                assert (await balancer.select_account()).account is None
                assert await selection_cache.get((None, None, None, "{}", None)) is not None
            elif order == "delayed_rejection_mark":
                rejection_task = asyncio.create_task(
                    balancer.mark_permanent_failure(clone_row(stale), "account_auth_invalidated")
                )
                await asyncio.wait_for(rejection_committed.wait(), 2)

            finish_refresh.set()
            refreshed = await asyncio.wait_for(refresh_task, 5)
            if order == "rotation_first":
                assert not await balancer.mark_permanent_failure(clone_row(stale), "account_auth_invalidated")
            elif rejection_task is not None:
                finish_rejection.set()
                assert await asyncio.wait_for(rejection_task, 5)
        finally:
            finish_refresh.set()
            finish_rejection.set()
            await asyncio.gather(refresh_task, *([rejection_task] if rejection_task is not None else []))

    assert refreshed.status == AccountStatus.ACTIVE
    assert refreshed.deactivation_reason is None
    assert not routing_cache.is_unavailable(stale.id)
    await peer_cache.refresh_from_db()
    assert not peer_cache.is_unavailable(stale.id)
    selected = await balancer.select_account()
    assert selected.account is not None
    assert selected.account.id == stale.id
    assert selected.account.status == AccountStatus.ACTIVE
    assert TokenEncryptor().decrypt(selected.account.access_token_encrypted) == "new-access"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [AccountStatus.PAUSED, AccountStatus.DEACTIVATED])
async def test_refresh_adopts_operator_status_committed_during_exchange(db_setup, monkeypatch, status):
    stale = await _create_account()

    async def refresh_tokens(self, token, *, account):
        async with SessionLocal() as session:
            assert await AccountsRepository(session).update_status(account.id, status, "operator decision")
        account_cache.mark_account_routing_unavailable(account.id)
        return TokenRefreshResult(
            access_token="new-access",
            refresh_token="new-refresh",
            id_token="new-id",
            account_id=None,
            plan_type="plus",
            email=None,
        )

    monkeypatch.setattr(AuthManager, "_refresh_tokens", refresh_tokens)
    async with SessionLocal() as session:
        refreshed = await AuthManager(AccountsRepository(session)).refresh_account(clone_row(stale))
    assert refreshed.status == status
    assert refreshed.deactivation_reason == "operator decision"
    assert TokenEncryptor().decrypt(refreshed.access_token_encrypted) == "new-access"
    assert account_cache.is_account_routing_unavailable(stale.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (AccountStatus.PAUSED, "operator pause"),
        (AccountStatus.DEACTIVATED, PERMANENT_FAILURE_CODES["account_suspended"]),
        (AccountStatus.RATE_LIMITED, "rate limited"),
        (AccountStatus.QUOTA_EXCEEDED, "quota exhausted"),
        (AccountStatus.REAUTH_REQUIRED, PERMANENT_FAILURE_CODES["invalid_grant"]),
        (AccountStatus.REAUTH_REQUIRED, _REJECTED_REASON),
    ],
)
async def test_rotation_preserves_independent_status_and_quota_state(db_setup, status, reason):
    stale = await _create_account(status=status, reason=reason)
    reset_at = 2_000_000_000
    blocked_at = reset_at - 60
    async with SessionLocal() as session:
        row = await session.get(Account, stale.id)
        assert row is not None
        row.reset_at = reset_at
        row.blocked_at = blocked_at
        row.security_work_authorized = True
        await session.commit()

    routing_cache = account_cache.get_routing_availability_cache()
    await routing_cache.refresh_from_db()
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        assert await AccountsRepository(session).rotate_tokens(
            stale.id,
            encryptor.encrypt("new-access"),
            encryptor.encrypt("new-refresh"),
            encryptor.encrypt("new-id"),
            utcnow(),
            expected_refresh_token_encrypted=stale.refresh_token_encrypted,
        )

    async with SessionLocal() as session:
        row = await session.get(Account, stale.id)
        assert row is not None
        repaired = status == AccountStatus.REAUTH_REQUIRED and reason == _REJECTED_REASON
        assert row.status == (AccountStatus.RATE_LIMITED if repaired else status)
        assert row.deactivation_reason == (None if repaired else reason)
        assert row.reset_at == reset_at
        assert row.blocked_at == blocked_at
        assert row.security_work_authorized
        assert encryptor.decrypt(row.refresh_token_encrypted) == "new-refresh"
    assert routing_cache.is_unavailable(stale.id) == (status in (AccountStatus.PAUSED, AccountStatus.DEACTIVATED))


@pytest.mark.asyncio
@pytest.mark.parametrize("reencrypt", [False, True])
async def test_rotation_without_new_access_material_does_not_clear_rejection(db_setup, reencrypt):
    stale = await _create_account(status=AccountStatus.REAUTH_REQUIRED, reason=_REJECTED_REASON)
    routing_cache = account_cache.get_routing_availability_cache()
    await routing_cache.refresh_from_db()
    encryptor = TokenEncryptor()
    access = encryptor.encrypt("old-access") if reencrypt else stale.access_token_encrypted
    if reencrypt:
        assert access != stale.access_token_encrypted
    async with SessionLocal() as session:
        assert await AccountsRepository(session).rotate_tokens(
            stale.id,
            access,
            encryptor.encrypt("new-refresh"),
            encryptor.encrypt("new-id"),
            utcnow(),
            expected_refresh_token_encrypted=stale.refresh_token_encrypted,
        )
    async with SessionLocal() as session:
        row = await session.get(Account, stale.id)
        assert row is not None
        assert row.status == AccountStatus.REAUTH_REQUIRED
        assert row.deactivation_reason == _REJECTED_REASON
    assert routing_cache.is_unavailable(stale.id)
    await routing_cache.refresh_from_db()
    assert routing_cache.is_unavailable(stale.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("access_material", ["repaired", "unchanged", "reencrypted"])
@pytest.mark.parametrize("current_snapshot", [False, True])
async def test_rejection_repair_preserves_cooldown_until_selection_reset(
    async_client, monkeypatch, access_material, current_snapshot
):
    now = 1_800_000_000
    monkeypatch.setattr(time, "time", lambda: now)
    stale = await _create_account()
    encryptor = TokenEncryptor()
    reencrypted_refresh = encryptor.encrypt("old-refresh")
    reset_at = now + 600
    blocked_at = now - 60
    async with SessionLocal() as session:
        row = await session.get(Account, stale.id)
        assert row is not None
        row.plan_type = "free"
        row.access_token_encrypted = encryptor.encrypt("old-access")
        row.refresh_token_encrypted = reencrypted_refresh
        assert await AccountsRepository(session).update_status(
            stale.id, AccountStatus.RATE_LIMITED, reset_at=reset_at, blocked_at=blocked_at
        )
        await session.refresh(row)
        rejected_snapshot = clone_row(row) if current_snapshot else clone_row(stale)
        await UsageRepository(session).add_entry(
            account_id=stale.id,
            used_percent=10.0,
            window="monthly",
            reset_at=now + 30 * 24 * 3600,
            window_minutes=43_200,
            recorded_at=datetime.fromtimestamp(now - 70, timezone.utc).replace(tzinfo=None),
        )

    balancer = get_proxy_service_for_app(async_client._transport.app)._load_balancer
    assert stale.id not in balancer._runtime

    def unexpected_encryptor():
        raise AssertionError("Repository must reuse the owner's encryptor")

    monkeypatch.setattr(accounts_repository, "TokenEncryptor", unexpected_encryptor)
    assert await balancer.mark_permanent_failure(rejected_snapshot, "account_auth_invalidated")
    async with SessionLocal() as session:
        rejected = await session.get(Account, stale.id)
        assert rejected is not None
        assert rejected.status == AccountStatus.REAUTH_REQUIRED
        assert rejected.deactivation_reason == _REJECTED_REASON
        assert (rejected.reset_at, rejected.blocked_at) == (reset_at, blocked_at)

    access = {
        "repaired": encryptor.encrypt("new-access"),
        "unchanged": rejected.access_token_encrypted,
        "reencrypted": encryptor.encrypt("old-access"),
    }[access_material]
    async with SessionLocal() as session:
        assert await AccountsRepository(session).rotate_tokens(
            stale.id,
            access,
            encryptor.encrypt("new-refresh"),
            encryptor.encrypt("new-id"),
            utcnow(),
            expected_refresh_token_encrypted=reencrypted_refresh,
            encryptor=encryptor,
        )

    async with SessionLocal() as session:
        row = await session.get(Account, stale.id)
        assert row is not None
        assert row.deactivation_reason == (None if access_material == "repaired" else _REJECTED_REASON)
        assert (row.reset_at, row.blocked_at) == (reset_at, blocked_at)

    assert (await balancer.select_account()).account is None
    assert row.status == (
        AccountStatus.RATE_LIMITED if access_material == "repaired" else AccountStatus.REAUTH_REQUIRED
    )
    now = reset_at + 1
    selected = await balancer.select_account()
    if access_material == "repaired":
        assert selected.account is not None
        assert selected.account.id == stale.id
        assert selected.account.status == AccountStatus.ACTIVE
        assert encryptor.decrypt(selected.account.access_token_encrypted) == "new-access"
    else:
        assert selected.account is None


@pytest.mark.asyncio
@pytest.mark.parametrize("persist_conflict", [False, True])
async def test_refresh_with_unchanged_rejected_access_material_keeps_account_unavailable(
    async_client, monkeypatch, persist_conflict
):
    stale = await _create_account()
    rotate_tokens = AccountsRepository.rotate_tokens
    rotation_attempts = 0

    async def lose_rotation_to_reencryption(self, account_id, *args, **kwargs):
        nonlocal rotation_attempts
        rotation_attempts += 1
        row = await self.session.get(Account, account_id)
        assert row is not None
        row.refresh_token_encrypted = TokenEncryptor().encrypt("old-refresh")
        await self.session.commit()
        return await rotate_tokens(self, account_id, *args, **kwargs)

    if persist_conflict:
        monkeypatch.setattr(AccountsRepository, "rotate_tokens", lose_rotation_to_reencryption)

    async def refresh_tokens(self, token, *, account):
        async with SessionLocal() as session:
            assert await AccountsRepository(session).update_status(
                account.id, AccountStatus.REAUTH_REQUIRED, _REJECTED_REASON
            )
        account_cache.mark_account_routing_unavailable(account.id)
        return TokenRefreshResult(
            access_token="old-access",
            refresh_token="new-refresh",
            id_token="new-id",
            account_id=None,
            plan_type="plus",
            email=None,
        )

    monkeypatch.setattr(AuthManager, "_refresh_tokens", refresh_tokens)

    async with SessionLocal() as session:
        refreshed = await AuthManager(AccountsRepository(session)).refresh_account(clone_row(stale))

    if persist_conflict:
        assert rotation_attempts > 1
        assert refreshed.access_token_encrypted == stale.access_token_encrypted
    else:
        assert refreshed.access_token_encrypted != stale.access_token_encrypted
    assert TokenEncryptor().decrypt(refreshed.access_token_encrypted) == "old-access"
    assert refreshed.status == AccountStatus.REAUTH_REQUIRED
    assert refreshed.deactivation_reason == _REJECTED_REASON
    async with SessionLocal() as session:
        row = await session.get(Account, stale.id)
        assert row is not None
        assert row.status == AccountStatus.REAUTH_REQUIRED
        assert row.deactivation_reason == _REJECTED_REASON
    balancer = get_proxy_service_for_app(async_client._transport.app)._load_balancer
    assert (await balancer.select_account()).account is None

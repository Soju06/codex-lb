from __future__ import annotations

import asyncio

import pytest

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
        assert row.status == (AccountStatus.ACTIVE if repaired else status)
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
async def test_refresh_with_unchanged_rejected_access_material_keeps_account_unavailable(async_client, monkeypatch):
    stale = await _create_account()

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

    assert refreshed.access_token_encrypted != stale.access_token_encrypted
    assert TokenEncryptor().decrypt(refreshed.access_token_encrypted) == "old-access"
    assert refreshed.status == AccountStatus.REAUTH_REQUIRED
    assert refreshed.deactivation_reason == _REJECTED_REASON
    balancer = get_proxy_service_for_app(async_client._transport.app)._load_balancer
    assert (await balancer.select_account()).account is None

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

import app.modules.proxy.service as proxy_module
from app.core.balancer import PERMANENT_FAILURE_CODES
from app.core.balancer.types import UpstreamError
from app.core.clients.proxy import ProxyResponseError
from app.core.crypto import TokenEncryptor
from app.core.errors import openai_error
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.db.snapshot import clone_row
from app.dependencies import get_proxy_service_for_app
from app.modules.accounts.repository import AccountsRepository
from app.modules.proxy import account_cache
from app.modules.proxy._service.http_bridge.helpers import _http_bridge_session_account_active

if TYPE_CHECKING:
    from app.modules.proxy._service.http_bridge.helpers import _HTTPBridgeSession

pytestmark = pytest.mark.integration

_REJECTED_REASON = PERMANENT_FAILURE_CODES["account_auth_invalidated"]


async def _create_account() -> Account:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = Account(
            id="auth-cas",
            email="auth-cas@example.com",
            plan_type="plus",
            access_token_encrypted=encryptor.encrypt("rejected-access"),
            refresh_token_encrypted=encryptor.encrypt("rejected-refresh"),
            id_token_encrypted=encryptor.encrypt("id"),
            last_refresh=utcnow(),
            status=AccountStatus.ACTIVE,
        )
        session.add(account)
        await session.commit()
        return clone_row(account)


@pytest.mark.asyncio
@pytest.mark.parametrize("reencrypt", [False, True])
async def test_stream_rejection_survives_concurrent_cooldown_after_it_expires(async_client, monkeypatch, reencrypt):
    account = await _create_account()
    reset_at = int(time.time()) - 60
    blocked_at = reset_at - 60
    original_update = AccountsRepository.update_status_if_current
    rejection_writes = 0
    upstream_calls = 0

    async def update_status(self, account_id, status, reason=None, *args, **kwargs):
        nonlocal rejection_writes
        if reason == _REJECTED_REASON:
            rejection_writes += 1
            if rejection_writes == 1:
                async with SessionLocal() as session:
                    repo = AccountsRepository(session)
                    if reencrypt:
                        row = await repo.get_by_id(account_id)
                        assert row is not None
                        row.access_token_encrypted = TokenEncryptor().encrypt("rejected-access")
                        row.refresh_token_encrypted = TokenEncryptor().encrypt("rejected-refresh")
                    await repo.update_status(
                        account_id, AccountStatus.RATE_LIMITED, reset_at=reset_at, blocked_at=blocked_at
                    )
        return await original_update(self, account_id, status, reason, *args, **kwargs)

    async def ensure_fresh(self, account, *, force=False, **kwargs):
        if force:
            raise proxy_module.RefreshError("invalid_grant", "Rejected refresh", True)
        return account

    async def stream(*args, **kwargs):
        nonlocal upstream_calls
        upstream_calls += 1
        raise ProxyResponseError(401, openai_error("token_expired", "Rejected access"), failure_phase="status")
        yield  # pragma: no cover

    monkeypatch.setattr(AccountsRepository, "update_status_if_current", update_status)
    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", ensure_fresh)
    monkeypatch.setattr(proxy_module, "core_stream_responses", stream)
    monkeypatch.setattr(proxy_module, "_STREAM_MAX_ACCOUNT_ATTEMPTS", 1)
    payload = {"model": "gpt-5.1", "instructions": "hi", "input": [], "stream": True}
    await async_client.post("/backend-api/codex/responses", json=payload)

    assert rejection_writes == 1
    async with SessionLocal() as session:
        row = await session.get(Account, account.id)
        assert row is not None
        assert row.status == AccountStatus.REAUTH_REQUIRED
        assert row.deactivation_reason == _REJECTED_REASON
        assert (row.reset_at, row.blocked_at) == (reset_at, blocked_at)
    peer_cache = account_cache.RoutingAvailabilityCache(SessionLocal)
    await peer_cache.refresh_from_db()
    assert peer_cache.is_unavailable(account.id)
    await async_client.post("/backend-api/codex/responses", json=payload)
    assert upstream_calls == 1


@pytest.mark.asyncio
async def test_rejection_atomic_retry_preserves_health_write_after_each_read(async_client, monkeypatch):
    stale = await _create_account()
    reset_at = int(time.time()) + 600
    blocked_at = reset_at - 60
    async with SessionLocal() as session:
        await AccountsRepository(session).update_status(
            stale.id, AccountStatus.RATE_LIMITED, reset_at=reset_at, blocked_at=blocked_at
        )
    original_read = AccountsRepository.get_by_id_fresh
    reads = 0

    async def read_with_health_write(self, account_id):
        nonlocal reads
        current = await original_read(self, account_id)
        assert current is not None
        snapshot = clone_row(current)
        reads += 1
        async with SessionLocal() as session:
            await AccountsRepository(session).update_status(
                account_id,
                AccountStatus.QUOTA_EXCEEDED,
                reset_at=reset_at + reads,
                blocked_at=blocked_at + reads,
            )
        return snapshot

    monkeypatch.setattr(AccountsRepository, "get_by_id_fresh", read_with_health_write)
    balancer = get_proxy_service_for_app(async_client._transport.app)._load_balancer
    assert await balancer.mark_permanent_failure(stale, "account_auth_invalidated")
    assert reads == 1
    async with SessionLocal() as session:
        row = await session.get(Account, stale.id)
        assert row is not None
        assert row.status == AccountStatus.REAUTH_REQUIRED
        assert row.deactivation_reason == _REJECTED_REASON
        assert (row.reset_at, row.blocked_at) == (reset_at + 1, blocked_at + 1)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    ["access_repair", "corrupt_access", "paused", "deactivated", "deleted"],
)
@pytest.mark.parametrize("after_retry_read", [False, True])
async def test_rejection_retry_preserves_concurrent_repair_or_operator_state(
    async_client, monkeypatch, mutation, after_retry_read
):
    stale = await _create_account()
    original_update = AccountsRepository.update_status_if_current
    original_read = AccountsRepository.get_by_id_fresh
    attempts = 0

    async def mutate(account_id):
        async with SessionLocal() as session:
            row = await session.get(Account, account_id)
            assert row is not None
            if mutation == "access_repair":
                row.access_token_encrypted = TokenEncryptor().encrypt("repaired-access")
            elif mutation == "corrupt_access":
                row.access_token_encrypted = b"invalid-access-ciphertext"
            elif mutation == "deleted":
                row.delete_requested_at = utcnow()
            else:
                row.status = AccountStatus.PAUSED if mutation == "paused" else AccountStatus.DEACTIVATED
                row.deactivation_reason = "operator decision"
            await session.commit()

    async def update_status(self, account_id, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if after_retry_read:
            async with SessionLocal() as session:
                await AccountsRepository(session).update_status(account_id, AccountStatus.RATE_LIMITED)
        else:
            await mutate(account_id)
        return await original_update(self, account_id, *args, **kwargs)

    async def read_with_mutation(self, account_id):
        current = await original_read(self, account_id)
        assert current is not None
        snapshot = clone_row(current)
        await mutate(account_id)
        return snapshot

    monkeypatch.setattr(AccountsRepository, "update_status_if_current", update_status)
    if after_retry_read:
        monkeypatch.setattr(AccountsRepository, "get_by_id_fresh", read_with_mutation)
    balancer = get_proxy_service_for_app(async_client._transport.app)._load_balancer
    assert not await balancer.mark_permanent_failure(stale, "account_auth_invalidated")
    assert attempts == 1
    async with SessionLocal() as session:
        row = await session.get(Account, stale.id)
        assert row is not None
        assert row.deactivation_reason != _REJECTED_REASON
        if mutation == "access_repair":
            assert TokenEncryptor().decrypt(row.access_token_encrypted) == "repaired-access"
        elif mutation == "corrupt_access":
            assert row.access_token_encrypted == b"invalid-access-ciphertext"
        elif mutation == "deleted":
            assert row.delete_requested_at is not None
        else:
            assert row.status == (AccountStatus.PAUSED if mutation == "paused" else AccountStatus.DEACTIVATED)
            assert row.deactivation_reason == "operator decision"


@pytest.mark.asyncio
@pytest.mark.parametrize("reencrypt_access", [False, True])
@pytest.mark.parametrize("after_retry_read", [False, True])
async def test_refresh_only_rotation_does_not_suppress_access_rejection(
    async_client, monkeypatch, reencrypt_access, after_retry_read
):
    stale = await _create_account()
    encryptor = TokenEncryptor()
    cache = account_cache.get_routing_availability_cache()
    await cache.refresh_from_db()
    original_update = AccountsRepository.update_status_if_current
    original_read = AccountsRepository.get_by_id_fresh
    rotated = False

    async def rotate_refresh():
        nonlocal rotated
        async with SessionLocal() as session:
            assert await AccountsRepository(session).rotate_tokens(
                stale.id,
                encryptor.encrypt("rejected-access") if reencrypt_access else stale.access_token_encrypted,
                encryptor.encrypt("new-refresh"),
                encryptor.encrypt("new-id"),
                utcnow(),
                expected_refresh_token_encrypted=stale.refresh_token_encrypted,
                encryptor=encryptor,
            )
        rotated = True

    async def update_with_rotation(self, account_id, *args, **kwargs):
        if after_retry_read:
            async with SessionLocal() as session:
                await AccountsRepository(session).update_status(account_id, AccountStatus.RATE_LIMITED)
        else:
            await rotate_refresh()
        return await original_update(self, account_id, *args, **kwargs)

    async def read_with_rotation(self, account_id):
        current = await original_read(self, account_id)
        assert current is not None
        snapshot = clone_row(current)
        if not rotated:
            await rotate_refresh()
        return snapshot

    monkeypatch.setattr(AccountsRepository, "update_status_if_current", update_with_rotation)
    if after_retry_read:
        monkeypatch.setattr(AccountsRepository, "get_by_id_fresh", read_with_rotation)
    balancer = get_proxy_service_for_app(async_client._transport.app)._load_balancer
    assert await balancer.mark_permanent_failure(clone_row(stale), "account_auth_invalidated")
    bridge = cast("_HTTPBridgeSession", SimpleNamespace(account=clone_row(stale), access_token_expires_at=None))
    assert not _http_bridge_session_account_active(bridge)
    async with SessionLocal() as session:
        row = await session.get(Account, stale.id)
        assert row is not None
        assert row.status == AccountStatus.REAUTH_REQUIRED
        assert row.deactivation_reason == _REJECTED_REASON
        assert encryptor.decrypt(row.refresh_token_encrypted) == "new-refresh"
        assert encryptor.decrypt(row.access_token_encrypted) == "rejected-access"
    await cache.refresh_from_db()
    assert not _http_bridge_session_account_active(bridge)
    assert (await balancer.select_account()).account is None


@pytest.mark.asyncio
@pytest.mark.parametrize("health", ["rate_limit", "quota_exceeded"])
@pytest.mark.parametrize("repair_before_expiry", [False, True])
async def test_late_health_write_preserves_rejection_through_reset_and_repair(
    async_client, monkeypatch, health, repair_before_expiry
):
    stale = await _create_account()
    balancer = get_proxy_service_for_app(async_client._transport.app)._load_balancer
    assert await balancer.mark_permanent_failure(clone_row(stale), "account_auth_invalidated")
    now = int(time.time())
    reset_at = now + 600
    method = balancer.mark_rate_limit if health == "rate_limit" else balancer.mark_quota_exceeded
    await method(stale, UpstreamError(resets_at=reset_at))
    assert stale.status == AccountStatus.REAUTH_REQUIRED
    assert stale.deactivation_reason == _REJECTED_REASON
    async with SessionLocal() as session:
        row = await session.get(Account, stale.id)
        assert row is not None
        assert row.status == AccountStatus.REAUTH_REQUIRED
        assert row.deactivation_reason == _REJECTED_REASON
        assert row.reset_at == reset_at
        assert row.blocked_at is not None
    cache = account_cache.get_routing_availability_cache()
    await cache.refresh_from_db()
    bridge = cast("_HTTPBridgeSession", SimpleNamespace(account=clone_row(stale), access_token_expires_at=None))
    assert not _http_bridge_session_account_active(bridge)
    peer_cache = account_cache.RoutingAvailabilityCache(SessionLocal)
    await peer_cache.refresh_from_db()
    assert peer_cache.is_unavailable(stale.id)
    if not repair_before_expiry:
        monkeypatch.setattr(time, "time", lambda: reset_at + 1)
        assert (await balancer.select_account()).account is None
        await balancer.record_success(stale)
        assert (await balancer.select_account()).account is None
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        assert await repo.rotate_tokens(
            stale.id,
            encryptor.encrypt("repaired-access"),
            encryptor.encrypt("repaired-refresh"),
            encryptor.encrypt("repaired-id"),
            utcnow(),
            expected_refresh_token_encrypted=stale.refresh_token_encrypted,
            encryptor=encryptor,
        )
        row = await repo.get_by_id_fresh(stale.id)
        assert row is not None
        assert row.status == (AccountStatus.RATE_LIMITED if repair_before_expiry else AccountStatus.ACTIVE)
        assert row.deactivation_reason is None
        assert row.reset_at == (reset_at if repair_before_expiry else None)

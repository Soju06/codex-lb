from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest

from app.core.auth.refresh import RefreshError, TokenRefreshResult
from app.core.balancer import PERMANENT_FAILURE_CODES
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts import auth_manager as auth_manager_module
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def clear_refresh_state() -> None:
    auth_manager_module._clear_refresh_singleflight_state()


@asynccontextmanager
async def _repo_scope() -> AsyncIterator[AccountsRepository]:
    async with SessionLocal() as session:
        yield AccountsRepository(session)


async def _create_stale_account(*, access_token: str | None = None) -> Account:
    if access_token is None:
        payload = json.dumps({"exp": int(time.time()) + 3600}).encode()
        access_token = f"header.{base64.urlsafe_b64encode(payload).rstrip(b'=').decode()}.sig"
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = Account(
            id="preflight-account",
            chatgpt_account_id="preflight-workspace",
            email="preflight@example.com",
            plan_type="plus",
            status=AccountStatus.ACTIVE,
            access_token_encrypted=encryptor.encrypt(access_token),
            refresh_token_encrypted=encryptor.encrypt("refresh-old"),
            id_token_encrypted=encryptor.encrypt("id-old"),
            last_refresh=utcnow() - timedelta(days=9),
        )
        session.add(account)
        await session.commit()
        return account


@pytest.mark.asyncio
async def test_stale_preflight_does_not_recover_a_peer_session_invalidation(db_setup):
    snapshot = await _create_stale_account()
    async with _repo_scope() as peer:
        await peer.update_status(
            snapshot.id, AccountStatus.REAUTH_REQUIRED, PERMANENT_FAILURE_CODES["app_session_terminated"]
        )

    async with _repo_scope() as repo:
        manager = AuthManager(repo, refresh_repo_factory=_repo_scope)
        with pytest.raises(RefreshError):
            await manager.ensure_fresh(snapshot)


@pytest.mark.asyncio
@pytest.mark.parametrize("access_token", ["opaque-token", "header.e30.sig", "header.eyJleHAiOjF9.sig"])
async def test_preflight_does_not_recover_unknown_or_expired_access(db_setup, monkeypatch, access_token):
    snapshot = await _create_stale_account(access_token=access_token)
    failure = RefreshError("refresh_token_invalidated", "Refresh token revoked", True)

    async def reject_refresh(_token: str, **_kwargs: object) -> TokenRefreshResult:
        raise failure

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", reject_refresh)
    async with _repo_scope() as repo:
        with pytest.raises(RefreshError) as raised:
            await AuthManager(repo, refresh_repo_factory=_repo_scope).ensure_fresh(snapshot)
        assert raised.value is failure


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("code", "permanent", "recovers"),
    [
        ("refresh_token_expired", True, True),
        ("refresh_token_reused", True, True),
        ("refresh_token_invalidated", True, True),
        ("invalid_refresh_token", True, True),
        ("invalid_grant", True, True),
        ("refresh_token_invalidated", False, False),
        ("transport_error", False, False),
        ("status_downgrade_conflict", False, False),
        ("token_invalidated", True, False),
        ("token_expired", True, False),
        ("app_session_terminated", True, False),
        ("account_session_expired", True, False),
        ("account_auth_invalidated", True, False),
        ("account_deactivated", True, False),
        ("account_suspended", True, False),
        ("account_deleted", True, False),
    ],
)
async def test_preflight_recovers_only_refresh_credential_failures(db_setup, monkeypatch, code, permanent, recovers):
    snapshot = await _create_stale_account()
    failure = RefreshError(code, "Upstream refresh failure", permanent)

    async def reject_refresh(_token: str, **_kwargs: object) -> TokenRefreshResult:
        raise failure

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", reject_refresh)
    async with _repo_scope() as repo:
        manager = AuthManager(repo, refresh_repo_factory=_repo_scope)
        if recovers:
            result = await manager.ensure_fresh(snapshot)
            assert result.status == AccountStatus.REAUTH_REQUIRED
            assert result.deactivation_reason == PERMANENT_FAILURE_CODES[code]
        else:
            with pytest.raises(RefreshError) as raised:
                await manager.ensure_fresh(snapshot)
            assert raised.value is failure


@pytest.mark.asyncio
@pytest.mark.parametrize("force_first", [False, True])
async def test_shared_refresh_failure_recovers_only_the_ordinary_caller(db_setup, monkeypatch, force_first):
    snapshot = await _create_stale_account()
    started = asyncio.Event()
    second_entered = asyncio.Event()
    release = asyncio.Event()
    refresh_calls = 0

    async def reject_refresh(_token: str, **_kwargs: object) -> TokenRefreshResult:
        nonlocal refresh_calls
        refresh_calls += 1
        started.set()
        await release.wait()
        raise RefreshError("refresh_token_invalidated", "Refresh token revoked", True)

    async def call(*, force: bool, second: bool) -> Account:
        async with _repo_scope() as repo:
            account = await repo.get_by_id(snapshot.id)
            assert account is not None
            if second:
                second_entered.set()
            return await AuthManager(repo, refresh_repo_factory=_repo_scope).ensure_fresh(account, force=force)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", reject_refresh)
    first = asyncio.create_task(call(force=force_first, second=False))
    second: asyncio.Task[Account] | None = None
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
        second = asyncio.create_task(call(force=not force_first, second=True))
        await asyncio.wait_for(second_entered.wait(), timeout=5)
    finally:
        release.set()
        tasks = [first] if second is None else [first, second]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    ordinary, forced = (results[1], results[0]) if force_first else (results[0], results[1])
    assert isinstance(ordinary, Account)
    assert ordinary.status == AccountStatus.REAUTH_REQUIRED
    assert isinstance(forced, RefreshError)
    assert forced.code == "refresh_token_invalidated"
    assert forced.is_permanent
    assert refresh_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [AccountStatus.PAUSED, AccountStatus.DEACTIVATED])
async def test_non_active_preflight_does_not_recover(db_setup, monkeypatch, status):
    snapshot = await _create_stale_account()
    async with _repo_scope() as repo:
        await repo.update_status(snapshot.id, status)
        snapshot.status = status

        async def reject_refresh(_token: str, **_kwargs: object) -> TokenRefreshResult:
            raise RefreshError("refresh_token_invalidated", "Refresh token revoked", True)

        monkeypatch.setattr(auth_manager_module, "refresh_access_token", reject_refresh)
        with pytest.raises(RefreshError):
            await AuthManager(repo, refresh_repo_factory=_repo_scope).ensure_fresh(snapshot)

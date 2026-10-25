from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import cast

import pytest

from app.core.auth.refresh import RefreshError
from app.db.models import Account
from app.modules.accounts import auth_manager
from app.modules.accounts.repository import AccountsRepository
from app.modules.oauth import service as oauth

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
async def test_refresh_completion_is_owned_while_waiting_for_lock(
    outcome: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A completion retains its task and preserves the existing cache policy."""
    owner = auth_manager._RefreshSingleflight()
    key = ("account-test", "token-fingerprint")
    account = Account(id=key[0])
    started = asyncio.Event()
    complete = owner._complete

    async def observed_complete(key: tuple[str, str], task: asyncio.Task[Account]) -> None:
        started.set()
        await complete(key, task)

    monkeypatch.setattr(owner, "_complete", observed_complete)

    async def refresh() -> Account:
        if outcome == "failure":
            raise RefreshError("invalid_grant", "refresh failed", True)
        if outcome == "cancelled":
            raise asyncio.CancelledError
        return account

    refresh_task = asyncio.create_task(refresh())
    await asyncio.gather(refresh_task, return_exceptions=True)
    owner._inflight[key] = refresh_task
    await owner._lock.acquire()
    try:
        owner._schedule_complete(key, refresh_task)
        assert len(owner._completions) == 1
        completion = next(iter(owner._completions))
        await asyncio.wait_for(started.wait(), timeout=1)
        assert not completion.done()
    finally:
        owner._lock.release()
    await asyncio.wait_for(completion, timeout=1)
    assert owner._completions == set()
    assert key not in owner._inflight
    assert (key in owner._recent_failures) == (outcome == "failure")


@pytest.mark.asyncio
async def test_cancelled_completion_releases_its_retained_reference() -> None:
    """Cancelling before completion starts must not leak a retained task."""
    owner = auth_manager._RefreshSingleflight()
    refresh_task = asyncio.create_task(asyncio.sleep(0, result=Account(id="account-test")))
    await refresh_task
    owner._schedule_complete(("account-test", "fingerprint"), refresh_task)
    completion = next(iter(owner._completions))
    completion.cancel()
    await asyncio.gather(completion, return_exceptions=True)
    assert owner._completions == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
async def test_callback_cleanup_is_owned_and_observed(outcome: str, caplog: pytest.LogCaptureFixture) -> None:
    """Pending callback cleanup is retained; every terminal outcome releases it."""
    store = oauth.OAuthStateStore()
    service = oauth.OauthService(cast(AccountsRepository, SimpleNamespace()), store=store)
    started = asyncio.Event()
    finish = asyncio.Event()

    class CallbackServer:
        async def stop(self) -> None:
            started.set()
            await finish.wait()
            if outcome == "failure":
                raise RuntimeError("secret-test-credential")

    store._callback_server = cast(oauth.OAuthCallbackServer, CallbackServer())
    with caplog.at_level(logging.ERROR, logger="app.modules.oauth.service"):
        service._spawn_callback_server_idle_stop()
        cleanup = next(iter(store._idle_stop_tasks))
        await asyncio.wait_for(started.wait(), timeout=1)
        assert cleanup in store._idle_stop_tasks
        assert not cleanup.done()
        if outcome == "cancelled":
            cleanup.cancel()
        finish.set()
        await asyncio.gather(cleanup, return_exceptions=True)
    assert store._idle_stop_tasks == set()
    assert "secret-test-credential" not in caplog.text
    assert ("OAuth callback idle cleanup failed" in caplog.text) == (outcome == "failure")
    # Cancellation of the waiter deliberately leaves the shielded server stop owned.
    if store._callback_server_stop_task is not None:
        await asyncio.gather(store._callback_server_stop_task, return_exceptions=True)
    await store.reset()

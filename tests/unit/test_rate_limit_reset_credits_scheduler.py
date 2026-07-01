from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import pytest

from app.core.clients.rate_limit_reset_credits import (
    RateLimitResetCreditsSnapshot,
    ResetCreditFetchError,
    ResetCreditsResponse,
)
from app.core.crypto import TokenEncryptor
from app.core.usage import reset_credits_refresh_scheduler as scheduler_module
from app.core.usage.reset_credits_refresh_scheduler import (
    RateLimitResetCreditsRefreshScheduler,
    refresh_reset_credits_for_accounts,
)
from app.db.models import Account, AccountStatus
from app.modules.rate_limit_reset_credits.store import RateLimitResetCreditsStore

pytestmark = pytest.mark.unit


class StubEncryptor(TokenEncryptor):
    def __init__(self) -> None:
        # Skip key-file I/O; tests only exercise decrypt().
        pass

    def decrypt(self, encrypted: bytes) -> str:
        return f"token-for-{encrypted.decode() if encrypted else ''}"


def _make_account(
    account_id: str,
    *,
    status: AccountStatus = AccountStatus.ACTIVE,
    chatgpt_account_id: str | None = "workspace-x",
) -> Account:
    return Account(
        id=account_id,
        chatgpt_account_id=chatgpt_account_id,
        email=f"{account_id}@example.com",
        plan_type="plus",
        access_token_encrypted=account_id.encode(),
        refresh_token_encrypted=b"refresh",
        id_token_encrypted=b"id",
        last_refresh=datetime(2025, 1, 1),
        status=status,
    )


def _response(available_count: int = 1) -> ResetCreditsResponse:
    return ResetCreditsResponse.model_validate(
        {
            "credits": [
                {"id": "c1", "status": "available", "expires_at": "2026-07-12T00:00:00Z"},
            ],
            "available_count": available_count,
        }
    )


@pytest.mark.asyncio
async def test_refresh_skips_paused_reauth_and_deactivated_accounts() -> None:
    store = RateLimitResetCreditsStore()
    stale = RateLimitResetCreditsSnapshot(available_count=5)
    await store.set("acc_paused", stale)
    await store.set("acc_reauth", stale)
    await store.set("acc_deactivated", stale)
    fetched: list[str] = []

    async def fetch_fn(access_token: str, account_id: str | None, **kwargs: Any) -> ResetCreditsResponse:
        fetched.append(access_token)
        return _response()

    accounts = [
        _make_account("acc_paused", status=AccountStatus.PAUSED),
        _make_account("acc_reauth", status=AccountStatus.REAUTH_REQUIRED),
        _make_account("acc_deactivated", status=AccountStatus.DEACTIVATED),
        _make_account("acc_active"),
    ]

    await refresh_reset_credits_for_accounts(
        accounts=accounts,
        encryptor=StubEncryptor(),
        store=store,
        fetch_fn=fetch_fn,
    )

    # Only the active account was fetched and cached.
    assert fetched == ["token-for-acc_active"]
    assert store.get("acc_paused") is stale
    assert store.get("acc_reauth") is stale
    assert store.get("acc_deactivated") is stale
    assert store.get("acc_active") is not None


@pytest.mark.asyncio
async def test_refresh_skips_account_without_chatgpt_account_id() -> None:
    store = RateLimitResetCreditsStore()
    stale = RateLimitResetCreditsSnapshot(available_count=4)
    await store.set("acc_no_workspace", stale)
    fetched: list[str] = []

    async def fetch_fn(access_token: str, account_id: str | None, **kwargs: Any) -> ResetCreditsResponse:
        fetched.append(access_token)
        return _response()

    await refresh_reset_credits_for_accounts(
        accounts=[_make_account("acc_no_workspace", chatgpt_account_id=None)],
        encryptor=StubEncryptor(),
        store=store,
        fetch_fn=fetch_fn,
    )

    assert fetched == []
    assert store.get("acc_no_workspace") is stale


@pytest.mark.asyncio
async def test_refresh_401_retains_prior_snapshot_without_status_mutation() -> None:
    """A 401 on reset-credits must not trigger a token refresh or status write.

    Reset-credits polling owns no account-status derivation; usage refresh owns
    token refresh and deactivation. A 401 logs and retains the prior cached
    snapshot with a single fetch attempt and no AuthManager involvement.
    """
    store = RateLimitResetCreditsStore()
    prior = RateLimitResetCreditsSnapshot(available_count=2)
    await store.set("acc_401", prior)
    account = _make_account("acc_401", status=AccountStatus.ACTIVE)
    fetch_calls = {"count": 0}

    async def fetch_fn(access_token: str, account_id: str | None, **kwargs: Any) -> ResetCreditsResponse:
        fetch_calls["count"] += 1
        raise ResetCreditFetchError(401, "unauthorized")

    await refresh_reset_credits_for_accounts(
        accounts=[account],
        encryptor=StubEncryptor(),
        store=store,
        fetch_fn=fetch_fn,
    )

    assert fetch_calls["count"] == 1
    assert store.get("acc_401") is prior
    assert account.status == AccountStatus.ACTIVE


@pytest.mark.asyncio
async def test_one_account_failure_does_not_break_the_loop() -> None:
    store = RateLimitResetCreditsStore()
    fetched: list[str] = []

    async def fetch_fn(access_token: str, account_id: str | None, **kwargs: Any) -> ResetCreditsResponse:
        fetched.append(access_token)
        if access_token == "token-for-acc_fail":
            raise ResetCreditFetchError(500, "boom")
        return _response(available_count=3)

    accounts = [_make_account("acc_fail"), _make_account("acc_ok")]

    await refresh_reset_credits_for_accounts(
        accounts=accounts,
        encryptor=StubEncryptor(),
        store=store,
        fetch_fn=fetch_fn,
    )

    # Both accounts were attempted despite the first raising.
    assert fetched == ["token-for-acc_fail", "token-for-acc_ok"]
    # The failing account left no snapshot; the healthy one was cached.
    assert store.get("acc_fail") is None
    ok_snapshot = store.get("acc_ok")
    assert ok_snapshot is not None
    assert ok_snapshot.available_count == 3


@pytest.mark.asyncio
async def test_upstream_error_retains_prior_snapshot_and_does_not_mutate_status() -> None:
    store = RateLimitResetCreditsStore()
    prior = RateLimitResetCreditsSnapshot(available_count=2)
    await store.set("acc_retain", prior)
    account = _make_account("acc_retain", status=AccountStatus.ACTIVE)

    async def fetch_fn(access_token: str, account_id: str | None, **kwargs: Any) -> ResetCreditsResponse:
        raise ResetCreditFetchError(503, "busy")

    await refresh_reset_credits_for_accounts(
        accounts=[account],
        encryptor=StubEncryptor(),
        store=store,
        fetch_fn=fetch_fn,
    )

    # Prior snapshot is retained exactly.
    assert store.get("acc_retain") is prior
    assert prior.available_count == 2
    # Account status is untouched.
    assert account.status == AccountStatus.ACTIVE


@pytest.mark.asyncio
async def test_refresh_does_not_resurrect_snapshot_invalidated_during_fetch() -> None:
    store = RateLimitResetCreditsStore()
    prior = RateLimitResetCreditsSnapshot(available_count=1)
    await store.set("acc_redeemed", prior)
    account = _make_account("acc_redeemed", status=AccountStatus.ACTIVE)
    fetch_started = asyncio.Event()
    release_fetch = asyncio.Event()

    async def fetch_fn(access_token: str, account_id: str | None, **kwargs: Any) -> ResetCreditsResponse:
        fetch_started.set()
        await release_fetch.wait()
        return _response(available_count=1)

    refresh_task = asyncio.create_task(
        refresh_reset_credits_for_accounts(
            accounts=[account],
            encryptor=StubEncryptor(),
            store=store,
            fetch_fn=fetch_fn,
        )
    )
    await fetch_started.wait()

    await store.invalidate("acc_redeemed")
    release_fetch.set()
    await refresh_task

    assert store.get("acc_redeemed") is None


@pytest.mark.asyncio
async def test_unrelated_account_write_does_not_drop_in_flight_refresh() -> None:
    store = RateLimitResetCreditsStore()
    await store.set("acc_a", RateLimitResetCreditsSnapshot(available_count=1))
    account = _make_account("acc_b", status=AccountStatus.ACTIVE)
    fetch_started = asyncio.Event()
    release_fetch = asyncio.Event()

    async def fetch_fn(access_token: str, account_id: str | None, **kwargs: Any) -> ResetCreditsResponse:
        fetch_started.set()
        await release_fetch.wait()
        return _response(available_count=4)

    refresh_task = asyncio.create_task(
        refresh_reset_credits_for_accounts(
            accounts=[account],
            encryptor=StubEncryptor(),
            store=store,
            fetch_fn=fetch_fn,
        )
    )
    await fetch_started.wait()

    await store.set("acc_a", RateLimitResetCreditsSnapshot(available_count=9))
    release_fetch.set()
    await refresh_task

    snapshot_b = store.get("acc_b")
    assert snapshot_b is not None
    assert snapshot_b.available_count == 4


@pytest.mark.asyncio
async def test_refresh_never_calls_account_status_writes() -> None:
    """The scheduler must not transition account status under any path.

    The refresh function operates only on the in-memory store; it holds no
    reference to a repository and therefore cannot perform status writes. We
    assert the account objects are byte-identical in status before and after,
    including across the failure path.
    """
    store = RateLimitResetCreditsStore()

    async def fetch_fn(access_token: str, account_id: str | None, **kwargs: Any) -> ResetCreditsResponse:
        if access_token == "token-for-acc_fail":
            raise ResetCreditFetchError(401, "unauthorized")
        return _response()

    accounts = [_make_account("acc_fail"), _make_account("acc_ok")]
    statuses_before = {a.id: a.status for a in accounts}

    await refresh_reset_credits_for_accounts(
        accounts=accounts,
        encryptor=StubEncryptor(),
        store=store,
        fetch_fn=fetch_fn,
    )

    assert {a.id: a.status for a in accounts} == statuses_before


@pytest.mark.asyncio
async def test_refresh_once_caches_snapshots_on_each_replica(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each process refreshes its own in-memory cache without leader gating."""

    account = _make_account("acc_replica")
    store = RateLimitResetCreditsStore()

    captured: list[Any] = []

    class _FakeRepo:
        async def list_accounts(self) -> list[Account]:
            captured.append("list_accounts")
            return [account]

    class _FakeSession:
        async def __aenter__(self) -> "_FakeSession":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

        def expunge_all(self) -> None:
            captured.append("expunge_all")

    @asynccontextmanager
    async def _fake_background_session():
        captured.append("session_opened")
        yield _FakeSession()

    monkeypatch.setattr(scheduler_module, "get_background_session", _fake_background_session)
    monkeypatch.setattr(scheduler_module, "AccountsRepository", lambda session: _FakeRepo())
    monkeypatch.setattr(scheduler_module, "TokenEncryptor", lambda: StubEncryptor())
    monkeypatch.setattr(scheduler_module, "get_rate_limit_reset_credits_store", lambda: store)

    async def fetch_fn(access_token: str, account_id: str | None, **kwargs: Any) -> ResetCreditsResponse:
        captured.append(("fetch", access_token, account_id))
        return _response(available_count=7)

    monkeypatch.setattr(scheduler_module, "fetch_reset_credits", fetch_fn)

    scheduler = RateLimitResetCreditsRefreshScheduler(interval_seconds=60)
    await scheduler._refresh_once()

    assert ("fetch", "token-for-acc_replica", "workspace-x") in captured
    snapshot = store.get("acc_replica")
    assert snapshot is not None
    assert snapshot.available_count == 7
    assert account.status == AccountStatus.ACTIVE


@pytest.mark.asyncio
async def test_refresh_once_closes_account_read_session_before_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    account = _make_account("acc_session")
    store = RateLimitResetCreditsStore()
    session_closed = False

    class _FakeRepo:
        async def list_accounts(self) -> list[Account]:
            return [account]

    class _FakeSession:
        def expunge_all(self) -> None:
            return None

    @asynccontextmanager
    async def _fake_background_session():
        nonlocal session_closed
        session_closed = False
        try:
            yield _FakeSession()
        finally:
            session_closed = True

    async def fetch_fn(access_token: str, account_id: str | None, **kwargs: Any) -> ResetCreditsResponse:
        assert session_closed is True
        return _response(available_count=8)

    monkeypatch.setattr(scheduler_module, "get_background_session", _fake_background_session)
    monkeypatch.setattr(scheduler_module, "AccountsRepository", lambda session: _FakeRepo())
    monkeypatch.setattr(scheduler_module, "TokenEncryptor", lambda: StubEncryptor())
    monkeypatch.setattr(scheduler_module, "get_rate_limit_reset_credits_store", lambda: store)
    monkeypatch.setattr(scheduler_module, "fetch_reset_credits", fetch_fn)

    scheduler = RateLimitResetCreditsRefreshScheduler(interval_seconds=60)
    await scheduler._refresh_once()

    snapshot = store.get(account.id)
    assert snapshot is not None
    assert snapshot.available_count == 8

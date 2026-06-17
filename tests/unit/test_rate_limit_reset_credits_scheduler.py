from __future__ import annotations

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
async def test_refresh_skips_paused_and_deactivated_accounts() -> None:
    store = RateLimitResetCreditsStore()
    fetched: list[str] = []

    async def fetch_fn(access_token: str, account_id: str | None) -> ResetCreditsResponse:
        fetched.append(access_token)
        return _response()

    accounts = [
        _make_account("acc_paused", status=AccountStatus.PAUSED),
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
    assert store.get("acc_paused") is None
    assert store.get("acc_deactivated") is None
    assert store.get("acc_active") is not None


@pytest.mark.asyncio
async def test_refresh_skips_account_without_chatgpt_account_id() -> None:
    store = RateLimitResetCreditsStore()
    fetched: list[str] = []

    async def fetch_fn(access_token: str, account_id: str | None) -> ResetCreditsResponse:
        fetched.append(access_token)
        return _response()

    await refresh_reset_credits_for_accounts(
        accounts=[_make_account("acc_no_workspace", chatgpt_account_id=None)],
        encryptor=StubEncryptor(),
        store=store,
        fetch_fn=fetch_fn,
    )

    assert fetched == []
    assert store.get("acc_no_workspace") is None


@pytest.mark.asyncio
async def test_one_account_failure_does_not_break_the_loop() -> None:
    store = RateLimitResetCreditsStore()
    fetched: list[str] = []

    async def fetch_fn(access_token: str, account_id: str | None) -> ResetCreditsResponse:
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

    async def fetch_fn(access_token: str, account_id: str | None) -> ResetCreditsResponse:
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
async def test_refresh_never_calls_account_status_writes() -> None:
    """The scheduler must not transition account status under any path.

    The refresh function operates only on the in-memory store; it holds no
    reference to a repository and therefore cannot perform status writes. We
    assert the account objects are byte-identical in status before and after,
    including across the failure path.
    """
    store = RateLimitResetCreditsStore()

    async def fetch_fn(access_token: str, account_id: str | None) -> ResetCreditsResponse:
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
async def test_refresh_once_skips_when_not_leader(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-leader replicas perform no upstream fetches and open no DB session."""

    class NonLeader:
        async def try_acquire(self) -> bool:
            return False

    monkeypatch.setattr(scheduler_module, "_get_leader_election", lambda: NonLeader())

    session_entered = False

    @asynccontextmanager
    async def _forbidden_session():  # type: ignore[no-untyped-def]
        nonlocal session_entered
        session_entered = True
        yield None

    monkeypatch.setattr(scheduler_module, "get_background_session", _forbidden_session)

    scheduler = RateLimitResetCreditsRefreshScheduler(interval_seconds=60, enabled=True)
    await scheduler._refresh_once()

    assert session_entered is False


@pytest.mark.asyncio
async def test_refresh_once_leader_path_caches_snapshots(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end leader-gated tick wires accounts -> store without status writes."""

    class Leader:
        async def try_acquire(self) -> bool:
            return True

    monkeypatch.setattr(scheduler_module, "_get_leader_election", lambda: Leader())

    account = _make_account("acc_leader")
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

    @asynccontextmanager
    async def _fake_background_session():
        captured.append("session_opened")
        yield _FakeSession()

    monkeypatch.setattr(scheduler_module, "get_background_session", _fake_background_session)
    monkeypatch.setattr(scheduler_module, "AccountsRepository", lambda session: _FakeRepo())
    monkeypatch.setattr(scheduler_module, "TokenEncryptor", lambda: StubEncryptor())
    monkeypatch.setattr(scheduler_module, "get_rate_limit_reset_credits_store", lambda: store)

    async def fetch_fn(access_token: str, account_id: str | None) -> ResetCreditsResponse:
        captured.append(("fetch", access_token, account_id))
        return _response(available_count=7)

    monkeypatch.setattr(scheduler_module, "fetch_reset_credits", fetch_fn)

    scheduler = RateLimitResetCreditsRefreshScheduler(interval_seconds=60, enabled=True)
    await scheduler._refresh_once()

    assert ("fetch", "token-for-acc_leader", "workspace-x") in captured
    leader_snapshot = store.get("acc_leader")
    assert leader_snapshot is not None
    assert leader_snapshot.available_count == 7
    assert account.status == AccountStatus.ACTIVE

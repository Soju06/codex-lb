from __future__ import annotations

import asyncio
import random
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

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


class _FakeDashboardSettings:
    def __init__(self, *, auto_redeem_reset_credits_before_expiry: bool = False) -> None:
        self.auto_redeem_reset_credits_before_expiry = auto_redeem_reset_credits_before_expiry


class _FakeSettingsRepository:
    def __init__(self, *, auto_redeem_reset_credits_before_expiry: bool = False) -> None:
        self._settings = _FakeDashboardSettings(
            auto_redeem_reset_credits_before_expiry=auto_redeem_reset_credits_before_expiry,
        )

    async def get_or_create(self) -> _FakeDashboardSettings:
        return self._settings


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


def _response(
    available_count: int = 1,
    *,
    expires_at: datetime | str | None = "2026-07-12T00:00:00Z",
) -> ResetCreditsResponse:
    return ResetCreditsResponse.model_validate(
        {
            "credits": [
                {"id": "c1", "status": "available", "expires_at": expires_at},
            ],
            "available_count": available_count,
        }
    )


def _response_expiring_in(seconds: int) -> ResetCreditsResponse:
    return _response(expires_at=datetime.now(UTC) + timedelta(seconds=seconds))


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
    monkeypatch.setattr(scheduler_module, "SettingsRepository", lambda session: _FakeSettingsRepository())
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
    monkeypatch.setattr(scheduler_module, "SettingsRepository", lambda session: _FakeSettingsRepository())
    monkeypatch.setattr(scheduler_module, "TokenEncryptor", lambda: StubEncryptor())
    monkeypatch.setattr(scheduler_module, "get_rate_limit_reset_credits_store", lambda: store)
    monkeypatch.setattr(scheduler_module, "fetch_reset_credits", fetch_fn)

    scheduler = RateLimitResetCreditsRefreshScheduler(interval_seconds=60)
    await scheduler._refresh_once()

    snapshot = store.get(account.id)
    assert snapshot is not None
    assert snapshot.available_count == 8


@pytest.mark.asyncio
async def test_refresh_once_uses_consume_route_for_auto_redeem(monkeypatch: pytest.MonkeyPatch) -> None:
    account = _make_account("acc_auto_route")
    captured: dict[str, Any] = {}

    class _FakeRepo:
        async def list_accounts(self) -> list[Account]:
            return [account]

    class _FakeSession:
        def expunge_all(self) -> None:
            return None

    @asynccontextmanager
    async def _fake_background_session():
        yield _FakeSession()

    async def refresh_stub(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(scheduler_module, "get_background_session", _fake_background_session)
    monkeypatch.setattr(scheduler_module, "AccountsRepository", lambda session: _FakeRepo())
    monkeypatch.setattr(
        scheduler_module,
        "SettingsRepository",
        lambda session: _FakeSettingsRepository(auto_redeem_reset_credits_before_expiry=True),
    )
    monkeypatch.setattr(scheduler_module, "TokenEncryptor", lambda: StubEncryptor())
    monkeypatch.setattr(scheduler_module, "get_rate_limit_reset_credits_store", lambda: RateLimitResetCreditsStore())
    monkeypatch.setattr(scheduler_module, "refresh_reset_credits_for_accounts", refresh_stub)

    scheduler = RateLimitResetCreditsRefreshScheduler(interval_seconds=60)
    await scheduler._refresh_once()

    assert captured["accounts"] == [account]
    assert captured["resolve_route"] is scheduler_module._resolve_reset_credits_refresh_route
    assert captured["auto_redeem_resolve_route"] is scheduler_module._resolve_reset_credits_consume_route
    assert captured["auto_redeem_before_expiry"] is True
    assert captured["auto_redeem_window_seconds"] == 300.0


@pytest.mark.asyncio
async def test_auto_redeem_disabled_by_default_does_not_call_redeem() -> None:
    store = RateLimitResetCreditsStore()
    account = _make_account("acc_manual")
    redeem_calls: list[str] = []

    async def fetch_fn(access_token: str, account_id: str | None, **kwargs: Any) -> ResetCreditsResponse:
        return _response_expiring_in(240)

    async def redeem_fn(**kwargs: Any) -> None:
        redeem_calls.append(kwargs["account"].id)

    await refresh_reset_credits_for_accounts(
        accounts=[account],
        encryptor=StubEncryptor(),
        store=store,
        fetch_fn=fetch_fn,
        redeem_fn=redeem_fn,
        auto_redeem_window_seconds=60,
    )

    assert redeem_calls == []
    snapshot = store.get(account.id)
    assert snapshot is not None
    assert snapshot.available_count == 1


@pytest.mark.asyncio
async def test_auto_redeem_uses_existing_helper_for_soonest_expiring_credit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.modules.rate_limit_reset_credits.api as reset_credits_api
    import app.modules.rate_limit_reset_credits.redeem_coordination as redeem_coordination

    store = RateLimitResetCreditsStore()
    account = _make_account("acc_auto")
    latest_account = _make_account("acc_auto")
    redeem_calls: list[dict[str, Any]] = []

    class _FakeLockSession:
        async def get(self, model: Any, account_id: str) -> Account | None:
            assert model is Account
            assert account_id == account.id
            return latest_account

    lock_session = _FakeLockSession()

    @asynccontextmanager
    async def _fake_background_session():
        yield lock_session

    async def fetch_fn(access_token: str, account_id: str | None, **kwargs: Any) -> ResetCreditsResponse:
        return _response_expiring_in(240)

    async def redeem_helper(**kwargs: Any) -> None:
        cached_snapshot = store.get(account.id)
        assert cached_snapshot is not None
        assert cached_snapshot.available_count == 1
        redeem_calls.append(kwargs)

    monkeypatch.setattr(scheduler_module, "get_background_session", _fake_background_session)
    monkeypatch.setattr(reset_credits_api, "_redeem_soonest_reset_credit", redeem_helper)
    monkeypatch.setattr(redeem_coordination, "get_pinned_redeem_credit_id", AsyncMock(return_value=None))

    await refresh_reset_credits_for_accounts(
        accounts=[account],
        encryptor=StubEncryptor(),
        store=store,
        fetch_fn=fetch_fn,
        auto_redeem_before_expiry=True,
        auto_redeem_window_seconds=300,
    )

    assert len(redeem_calls) == 1
    assert redeem_calls[0]["account"] is latest_account
    assert redeem_calls[0]["store"] is store
    assert redeem_calls[0]["fetch_fn"] is fetch_fn
    assert redeem_calls[0]["lock_session"] is lock_session
    assert isinstance(redeem_calls[0]["redeem_request_id"], str)
    assert redeem_calls[0]["redeem_request_id"].startswith("auto-reset-credit:")
    assert redeem_calls[0]["skip_if_redeem_request_pinned"] is True
    assert redeem_calls[0]["expected_credit_id"] == "c1"
    assert redeem_calls[0]["expected_credit_expires_at"] == store.get(account.id).credits[0].expires_at
    assert callable(redeem_calls[0]["refresh_usage"])


@pytest.mark.asyncio
async def test_auto_redeem_reloads_account_and_skips_if_no_longer_eligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.modules.rate_limit_reset_credits.redeem_coordination as redeem_coordination

    store = RateLimitResetCreditsStore()
    stale_account = _make_account("acc_auto_paused", status=AccountStatus.ACTIVE)
    latest_account = _make_account("acc_auto_paused", status=AccountStatus.PAUSED)
    redeem_calls: list[str] = []

    class _FakeLockSession:
        async def get(self, model: Any, account_id: str) -> Account | None:
            assert model is Account
            assert account_id == stale_account.id
            return latest_account

    @asynccontextmanager
    async def _fake_background_session():
        yield _FakeLockSession()

    async def fetch_fn(access_token: str, account_id: str | None, **kwargs: Any) -> ResetCreditsResponse:
        return _response_expiring_in(240)

    async def redeem_fn(**kwargs: Any) -> None:
        redeem_calls.append(kwargs["account"].id)

    monkeypatch.setattr(scheduler_module, "get_background_session", _fake_background_session)
    monkeypatch.setattr(redeem_coordination, "get_pinned_redeem_credit_id", AsyncMock(return_value=None))

    await refresh_reset_credits_for_accounts(
        accounts=[stale_account],
        encryptor=StubEncryptor(),
        store=store,
        fetch_fn=fetch_fn,
        redeem_fn=redeem_fn,
        auto_redeem_before_expiry=True,
        auto_redeem_window_seconds=300,
    )

    assert redeem_calls == []
    snapshot = store.get(stale_account.id)
    assert snapshot is not None
    assert snapshot.available_count == 1


@pytest.mark.asyncio
async def test_auto_redeem_ignores_expiry_outside_five_minute_window(monkeypatch: pytest.MonkeyPatch) -> None:
    store = RateLimitResetCreditsStore()
    account = _make_account("acc_far")
    redeem_calls: list[str] = []

    async def fetch_fn(access_token: str, account_id: str | None, **kwargs: Any) -> ResetCreditsResponse:
        return _response_expiring_in(3600)

    async def redeem_fn(**kwargs: Any) -> None:
        redeem_calls.append(kwargs["account"].id)

    await refresh_reset_credits_for_accounts(
        accounts=[account],
        encryptor=StubEncryptor(),
        store=store,
        fetch_fn=fetch_fn,
        redeem_fn=redeem_fn,
        auto_redeem_before_expiry=True,
        auto_redeem_window_seconds=300,
    )

    assert redeem_calls == []


@pytest.mark.asyncio
async def test_auto_redeem_ignores_snapshot_without_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    store = RateLimitResetCreditsStore()
    account = _make_account("acc_no_expiry")
    redeem_calls: list[str] = []

    async def fetch_fn(access_token: str, account_id: str | None, **kwargs: Any) -> ResetCreditsResponse:
        return _response(expires_at=None)

    async def redeem_fn(**kwargs: Any) -> None:
        redeem_calls.append(kwargs["account"].id)

    await refresh_reset_credits_for_accounts(
        accounts=[account],
        encryptor=StubEncryptor(),
        store=store,
        fetch_fn=fetch_fn,
        redeem_fn=redeem_fn,
        auto_redeem_before_expiry=True,
        auto_redeem_window_seconds=60,
    )

    assert redeem_calls == []


@pytest.mark.asyncio
async def test_auto_redeem_skips_when_automatic_request_is_already_pinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.modules.rate_limit_reset_credits.redeem_coordination as redeem_coordination

    store = RateLimitResetCreditsStore()
    account = _make_account("acc_already_pinned")
    redeem_calls: list[str] = []

    async def fetch_fn(access_token: str, account_id: str | None, **kwargs: Any) -> ResetCreditsResponse:
        return _response_expiring_in(240)

    async def redeem_fn(**kwargs: Any) -> None:
        redeem_calls.append(kwargs["account"].id)

    monkeypatch.setattr(redeem_coordination, "get_pinned_redeem_credit_id", AsyncMock(return_value="credit-pinned"))

    await refresh_reset_credits_for_accounts(
        accounts=[account],
        encryptor=StubEncryptor(),
        store=store,
        fetch_fn=fetch_fn,
        redeem_fn=redeem_fn,
        auto_redeem_before_expiry=True,
        auto_redeem_window_seconds=300,
    )

    assert redeem_calls == []


@pytest.mark.asyncio
async def test_auto_redeem_skips_when_helper_finds_pin_inside_redeem_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.modules.rate_limit_reset_credits.redeem_coordination as redeem_coordination
    from app.modules.rate_limit_reset_credits.api import ResetCreditRedeemRequestAlreadyPinned

    store = RateLimitResetCreditsStore()
    account = _make_account("acc_inner_pin")
    redeem_calls: list[str] = []

    class _FakeLockSession:
        async def get(self, model: Any, account_id: str) -> Account | None:
            assert model is Account
            assert account_id == account.id
            return account

    lock_session = _FakeLockSession()

    @asynccontextmanager
    async def _fake_background_session():
        yield lock_session

    async def fetch_fn(access_token: str, account_id: str | None, **kwargs: Any) -> ResetCreditsResponse:
        return _response_expiring_in(240)

    async def redeem_fn(**kwargs: Any) -> None:
        redeem_calls.append(kwargs["account"].id)
        raise ResetCreditRedeemRequestAlreadyPinned(
            account_id=kwargs["account"].id,
            redeem_request_id=kwargs["redeem_request_id"],
            credit_id="credit-pinned",
        )

    monkeypatch.setattr(scheduler_module, "get_background_session", _fake_background_session)
    monkeypatch.setattr(redeem_coordination, "get_pinned_redeem_credit_id", AsyncMock(return_value=None))

    await refresh_reset_credits_for_accounts(
        accounts=[account],
        encryptor=StubEncryptor(),
        store=store,
        fetch_fn=fetch_fn,
        redeem_fn=redeem_fn,
        auto_redeem_before_expiry=True,
        auto_redeem_window_seconds=300,
    )

    assert redeem_calls == ["acc_inner_pin"]
    snapshot = store.get(account.id)
    assert snapshot is not None
    assert snapshot.available_count == 1


@pytest.mark.asyncio
async def test_auto_redeem_failure_is_isolated_to_one_account(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.modules.rate_limit_reset_credits.redeem_coordination as redeem_coordination

    store = RateLimitResetCreditsStore()
    accounts = [_make_account("acc_redeem_fail"), _make_account("acc_redeem_ok")]
    issued_lock_sessions: list[Any] = []
    redeem_calls: list[tuple[str, Any]] = []

    class _FakeLockSession:
        def __init__(self, account: Account) -> None:
            self.account = account

        async def get(self, model: Any, account_id: str) -> Account | None:
            assert model is Account
            assert account_id == self.account.id
            return self.account

    @asynccontextmanager
    async def _fake_background_session():
        lock_session = _FakeLockSession(accounts[len(issued_lock_sessions)])
        issued_lock_sessions.append(lock_session)
        yield lock_session

    async def fetch_fn(access_token: str, account_id: str | None, **kwargs: Any) -> ResetCreditsResponse:
        return _response_expiring_in(30)

    async def redeem_fn(**kwargs: Any) -> None:
        account = kwargs["account"]
        redeem_calls.append((account.id, kwargs["lock_session"]))
        if account.id == "acc_redeem_fail":
            raise RuntimeError("stub redeem failed")

    monkeypatch.setattr(scheduler_module, "get_background_session", _fake_background_session)
    monkeypatch.setattr(redeem_coordination, "get_pinned_redeem_credit_id", AsyncMock(return_value=None))

    await refresh_reset_credits_for_accounts(
        accounts=accounts,
        encryptor=StubEncryptor(),
        store=store,
        fetch_fn=fetch_fn,
        redeem_fn=redeem_fn,
        auto_redeem_before_expiry=True,
        auto_redeem_window_seconds=300,
    )

    assert redeem_calls == [
        ("acc_redeem_fail", issued_lock_sessions[0]),
        ("acc_redeem_ok", issued_lock_sessions[1]),
    ]
    assert [account_id for account_id, _lock_session in redeem_calls] == ["acc_redeem_fail", "acc_redeem_ok"]
    assert store.get("acc_redeem_fail") is not None
    assert store.get("acc_redeem_ok") is not None


# --- tick desynchronization (replicas must not fetch in lockstep) ---


def test_startup_delay_stays_within_one_full_interval() -> None:
    scheduler = RateLimitResetCreditsRefreshScheduler(interval_seconds=60, rng=random.Random(1234))

    delays = [scheduler._startup_delay_seconds() for _ in range(200)]

    assert all(0.0 <= delay <= 60.0 for delay in delays)
    # A uniform draw over the full interval, not a constant offset.
    assert max(delays) - min(delays) > 1.0


def test_tick_delay_jitter_stays_within_ten_percent() -> None:
    scheduler = RateLimitResetCreditsRefreshScheduler(interval_seconds=60, rng=random.Random(1234))

    delays = [scheduler._tick_delay_seconds() for _ in range(200)]

    assert all(54.0 <= delay <= 66.0 for delay in delays)
    assert max(delays) - min(delays) > 1.0


def test_two_replicas_with_distinct_rngs_do_not_tick_in_lockstep() -> None:
    replica_a = RateLimitResetCreditsRefreshScheduler(interval_seconds=60, rng=random.Random(1))
    replica_b = RateLimitResetCreditsRefreshScheduler(interval_seconds=60, rng=random.Random(2))

    assert replica_a._startup_delay_seconds() != replica_b._startup_delay_seconds()


@pytest.mark.asyncio
async def test_run_loop_stops_cleanly_during_startup_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    refreshed = False

    scheduler = RateLimitResetCreditsRefreshScheduler(interval_seconds=3600)

    async def _refresh_once(self: RateLimitResetCreditsRefreshScheduler) -> None:
        nonlocal refreshed
        refreshed = True

    monkeypatch.setattr(RateLimitResetCreditsRefreshScheduler, "_refresh_once", _refresh_once)

    await scheduler.start()
    await asyncio.sleep(0.01)
    await scheduler.stop()

    assert refreshed is False

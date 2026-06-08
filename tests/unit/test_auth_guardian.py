from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import cast

import pytest

from app.core.auth import guardian as guardian_module
from app.core.auth.guardian import AuthGuardianScheduler, build_auth_guardian_scheduler, select_auth_guardian_candidates
from app.core.auth.refresh import RefreshError
from app.core.config import settings as settings_module
from app.db.models import Account, AccountStatus
from app.modules.accounts.auth_manager import AuthManager

pytestmark = pytest.mark.unit


def _account(account_id: str, *, status: AccountStatus, last_refresh: datetime) -> Account:
    return Account(
        id=account_id,
        chatgpt_account_id=f"workspace-{account_id}",
        email=f"{account_id}@example.com",
        alias=None,
        plan_type="plus",
        access_token_encrypted=b"access",
        refresh_token_encrypted=b"refresh",
        id_token_encrypted=b"id",
        last_refresh=last_refresh,
        status=status,
        deactivation_reason=None,
    )


class _Repo:
    def __init__(self, accounts: list[Account]) -> None:
        self._accounts = {account.id: account for account in accounts}

    async def list_accounts(self, *, refresh_existing: bool = False) -> list[Account]:
        del refresh_existing
        return list(self._accounts.values())

    async def get_by_id(self, account_id: str) -> Account | None:
        return self._accounts.get(account_id)


class _Leader:
    async def try_acquire(self) -> bool:
        return True


class _AuthManager:
    def __init__(self, calls: list[str], failures: dict[str, RefreshError] | None = None) -> None:
        self._calls = calls
        self._failures = failures or {}

    async def ensure_fresh(self, account: Account, *, force: bool = False) -> Account:
        assert force is True
        self._calls.append(account.id)
        failure = self._failures.get(account.id)
        if failure is not None:
            raise failure
        account.last_refresh = datetime(2026, 1, 2, 12, 0, 0)
        return account


class _AccountSelectionCache:
    def __init__(self) -> None:
        self.invalidate_calls = 0

    def invalidate(self) -> None:
        self.invalidate_calls += 1


def test_select_auth_guardian_candidates_returns_stale_active_only() -> None:
    now = datetime(2026, 1, 2, 12, 0, 0)
    accounts = [
        _account("fresh-active", status=AccountStatus.ACTIVE, last_refresh=now - timedelta(hours=1)),
        _account("stale-active", status=AccountStatus.ACTIVE, last_refresh=now - timedelta(hours=13)),
        _account("oldest-active", status=AccountStatus.ACTIVE, last_refresh=now - timedelta(hours=20)),
        _account("paused", status=AccountStatus.PAUSED, last_refresh=now - timedelta(hours=20)),
        _account("reauth", status=AccountStatus.REAUTH_REQUIRED, last_refresh=now - timedelta(hours=20)),
    ]

    selected = select_auth_guardian_candidates(accounts, now=now, max_age_seconds=12 * 3600, limit=2)

    assert [account.id for account in selected] == ["oldest-active", "stale-active"]


def test_default_auth_manager_factory_uses_owned_refresh_repo() -> None:
    repo = _Repo([])

    manager = cast(AuthManager, guardian_module._default_auth_manager_factory(repo))

    assert manager._refresh_repo_factory is guardian_module._default_accounts_repo_factory


def test_build_auth_guardian_scheduler_allows_single_replica_without_leader_election(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(auth_guardian_enabled=True, leader_election_enabled=False)
    monkeypatch.setattr(settings_module, "get_settings", lambda: settings)

    scheduler = build_auth_guardian_scheduler()

    assert scheduler.enabled is True


def test_build_auth_guardian_scheduler_requires_leader_election_for_multi_replica(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        auth_guardian_enabled=True,
        leader_election_enabled=False,
        instance_ring=["pod-a", "pod-b"],
    )
    monkeypatch.setattr(settings_module, "get_settings", lambda: settings)

    scheduler = build_auth_guardian_scheduler()

    assert scheduler.enabled is False

    settings.leader_election_enabled = True
    scheduler = build_auth_guardian_scheduler()

    assert scheduler.enabled is True

    settings.auth_guardian_enabled = False
    scheduler = build_auth_guardian_scheduler()

    assert scheduler.enabled is False


@pytest.mark.asyncio
async def test_auth_guardian_refresh_once_refreshes_stale_active_and_skips_others() -> None:
    now = datetime(2026, 1, 2, 12, 0, 0)
    accounts = [
        _account("fresh-active", status=AccountStatus.ACTIVE, last_refresh=now - timedelta(hours=1)),
        _account("stale-active", status=AccountStatus.ACTIVE, last_refresh=now - timedelta(hours=13)),
        _account("paused", status=AccountStatus.PAUSED, last_refresh=now - timedelta(hours=13)),
    ]
    repo = _Repo(accounts)
    calls: list[str] = []

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[_Repo]:
        yield repo

    scheduler = AuthGuardianScheduler(
        interval_seconds=21600,
        enabled=True,
        max_age_seconds=12 * 3600,
        batch_size=10,
        concurrency=2,
        jitter_seconds=0.0,
        leader_election_factory=lambda: _Leader(),
        repo_factory=repo_factory,
        auth_manager_factory=lambda _repo: _AuthManager(calls),
        sleep=lambda _delay: _noop_sleep(),
        now=lambda: now,
    )

    await scheduler._refresh_once()

    assert calls == ["stale-active"]


@pytest.mark.asyncio
async def test_auth_guardian_refresh_once_invalidates_account_selection_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 1, 2, 12, 0, 0)
    account = _account("stale-active", status=AccountStatus.ACTIVE, last_refresh=now - timedelta(hours=13))
    repo = _Repo([account])
    calls: list[str] = []
    cache = _AccountSelectionCache()

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[_Repo]:
        yield repo

    monkeypatch.setattr(guardian_module, "get_account_selection_cache", lambda: cache)

    scheduler = AuthGuardianScheduler(
        interval_seconds=21600,
        enabled=True,
        max_age_seconds=12 * 3600,
        batch_size=10,
        concurrency=1,
        jitter_seconds=0.0,
        leader_election_factory=lambda: _Leader(),
        repo_factory=repo_factory,
        auth_manager_factory=lambda _repo: _AuthManager(calls),
        sleep=lambda _delay: _noop_sleep(),
        now=lambda: now,
    )

    await scheduler._refresh_once()

    assert calls == [account.id]
    assert cache.invalidate_calls == 1


@pytest.mark.asyncio
async def test_auth_guardian_transport_failure_does_not_mark_status() -> None:
    now = datetime(2026, 1, 2, 12, 0, 0)
    account = _account("transport-failure", status=AccountStatus.ACTIVE, last_refresh=now - timedelta(hours=13))
    repo = _Repo([account])
    calls: list[str] = []
    failures = {
        account.id: RefreshError(
            "transport_error",
            "Transport error during token refresh",
            False,
            transport_error=True,
        )
    }

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[_Repo]:
        yield repo

    scheduler = AuthGuardianScheduler(
        interval_seconds=21600,
        enabled=True,
        max_age_seconds=12 * 3600,
        batch_size=10,
        concurrency=1,
        jitter_seconds=0.0,
        leader_election_factory=lambda: _Leader(),
        repo_factory=repo_factory,
        auth_manager_factory=lambda _repo: _AuthManager(calls, failures),
        sleep=lambda _delay: _noop_sleep(),
        now=lambda: now,
    )

    await scheduler._refresh_once()

    assert calls == [account.id]
    assert account.status == AccountStatus.ACTIVE
    assert account.deactivation_reason is None


@pytest.mark.asyncio
async def test_auth_guardian_permanent_refresh_failure_invalidates_account_selection_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 1, 2, 12, 0, 0)
    account = _account("permanent-failure", status=AccountStatus.ACTIVE, last_refresh=now - timedelta(hours=13))
    repo = _Repo([account])
    calls: list[str] = []
    cache = _AccountSelectionCache()
    failures = {
        account.id: RefreshError(
            "refresh_token_invalidated",
            "Refresh token was revoked",
            True,
        )
    }

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[_Repo]:
        yield repo

    monkeypatch.setattr(guardian_module, "get_account_selection_cache", lambda: cache)

    scheduler = AuthGuardianScheduler(
        interval_seconds=21600,
        enabled=True,
        max_age_seconds=12 * 3600,
        batch_size=10,
        concurrency=1,
        jitter_seconds=0.0,
        leader_election_factory=lambda: _Leader(),
        repo_factory=repo_factory,
        auth_manager_factory=lambda _repo: _AuthManager(calls, failures),
        sleep=lambda _delay: _noop_sleep(),
        now=lambda: now,
    )

    await scheduler._refresh_once()

    assert calls == [account.id]
    assert cache.invalidate_calls == 1


@pytest.mark.asyncio
async def test_auth_guardian_run_loop_survives_transient_pass_failure(caplog: pytest.LogCaptureFixture) -> None:
    now = datetime(2026, 1, 2, 12, 0, 0)
    calls = 0
    scheduler: AuthGuardianScheduler

    class _FlakyRepo(_Repo):
        async def list_accounts(self, *, refresh_existing: bool = False) -> list[Account]:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("database is briefly unavailable")
            scheduler._stop.set()
            return await super().list_accounts(refresh_existing=refresh_existing)

    repo = _FlakyRepo([])

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[_FlakyRepo]:
        yield repo

    scheduler = AuthGuardianScheduler(
        interval_seconds=1,
        enabled=True,
        max_age_seconds=12 * 3600,
        batch_size=10,
        concurrency=1,
        jitter_seconds=0.0,
        leader_election_factory=lambda: _Leader(),
        repo_factory=repo_factory,
        auth_manager_factory=lambda _repo: _AuthManager([]),
        sleep=lambda _delay: _noop_sleep(),
        now=lambda: now,
    )

    with caplog.at_level(logging.ERROR, logger="app.core.auth.guardian"):
        await asyncio.wait_for(scheduler._run_loop(), timeout=2)

    assert calls == 2
    assert "Auth Guardian refresh pass failed" in caplog.text


@pytest.mark.asyncio
async def test_auth_guardian_skips_backoff_before_batch_limit() -> None:
    now = datetime(2026, 1, 2, 12, 0, 0)
    accounts = [
        _account("backoff-oldest", status=AccountStatus.ACTIVE, last_refresh=now - timedelta(hours=30)),
        _account("runnable-older", status=AccountStatus.ACTIVE, last_refresh=now - timedelta(hours=20)),
        _account("runnable-newer", status=AccountStatus.ACTIVE, last_refresh=now - timedelta(hours=13)),
    ]
    repo = _Repo(accounts)
    calls: list[str] = []

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[_Repo]:
        yield repo

    scheduler = AuthGuardianScheduler(
        interval_seconds=21600,
        enabled=True,
        max_age_seconds=12 * 3600,
        batch_size=2,
        concurrency=1,
        jitter_seconds=0.0,
        leader_election_factory=lambda: _Leader(),
        repo_factory=repo_factory,
        auth_manager_factory=lambda _repo: _AuthManager(calls),
        sleep=lambda _delay: _noop_sleep(),
        now=lambda: now,
    )
    scheduler._record_failure("backoff-oldest")

    await scheduler._refresh_once()

    assert calls == ["runnable-older", "runnable-newer"]


@pytest.mark.asyncio
async def test_auth_guardian_waits_for_refresh_before_cancelled_candidate_exits() -> None:
    now = datetime(2026, 1, 2, 12, 0, 0)
    account = _account("stale-active", status=AccountStatus.ACTIVE, last_refresh=now - timedelta(hours=13))
    repo = _Repo([account])
    started = asyncio.Event()
    allow_finish = asyncio.Event()
    completed = False
    repo_exited = False

    class _DelayedAuthManager:
        async def ensure_fresh(self, account: Account, *, force: bool = False) -> Account:
            nonlocal completed
            assert force is True
            assert account.id == "stale-active"
            started.set()
            await allow_finish.wait()
            completed = True
            account.last_refresh = now
            return account

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[_Repo]:
        nonlocal repo_exited
        try:
            yield repo
        finally:
            if started.is_set():
                repo_exited = True

    scheduler = AuthGuardianScheduler(
        interval_seconds=21600,
        enabled=True,
        max_age_seconds=12 * 3600,
        batch_size=10,
        concurrency=1,
        jitter_seconds=0.0,
        leader_election_factory=lambda: _Leader(),
        repo_factory=repo_factory,
        auth_manager_factory=lambda _repo: _DelayedAuthManager(),
        sleep=lambda _delay: _noop_sleep(),
        now=lambda: now,
    )

    task = asyncio.create_task(scheduler._refresh_once())
    await asyncio.wait_for(started.wait(), timeout=1)

    task.cancel()
    await asyncio.sleep(0)

    assert completed is False
    assert repo_exited is False

    allow_finish.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=1)

    assert completed is True
    assert repo_exited is True


async def _noop_sleep() -> None:
    return None


def _settings(
    *,
    auth_guardian_enabled: bool,
    leader_election_enabled: bool,
    instance_ring: list[str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        auth_guardian_enabled=auth_guardian_enabled,
        leader_election_enabled=leader_election_enabled,
        http_responses_session_bridge_instance_ring=instance_ring or [],
        auth_guardian_interval_seconds=21600,
        auth_guardian_max_refresh_age_seconds=12 * 3600,
        auth_guardian_batch_size=10,
        auth_guardian_concurrency=1,
        auth_guardian_jitter_seconds=0.0,
        auth_guardian_failure_backoff_base_seconds=300.0,
        auth_guardian_failure_backoff_max_seconds=3600.0,
    )

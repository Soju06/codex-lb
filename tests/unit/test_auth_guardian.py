from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.auth import guardian as guardian_module
from app.core.auth.guardian import AuthGuardianScheduler, build_auth_guardian_scheduler, select_auth_guardian_candidates
from app.core.auth.refresh import RefreshError
from app.core.config import settings as settings_module
from app.db.models import Account, AccountStatus, Base
from app.db.session import close_session
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.repository import AccountsRepository

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
    async def run_if_leader(self, fn: Callable[[], Awaitable[object]]) -> object:
        return await fn()


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


def test_select_auth_guardian_candidates_returns_stale_eligible_accounts_only() -> None:
    now = datetime(2026, 1, 2, 12, 0, 0)
    accounts = [
        _account("fresh-active", status=AccountStatus.ACTIVE, last_refresh=now - timedelta(hours=1)),
        _account("stale-active", status=AccountStatus.ACTIVE, last_refresh=now - timedelta(hours=13)),
        _account("oldest-active", status=AccountStatus.ACTIVE, last_refresh=now - timedelta(hours=20)),
        _account("stale-paused", status=AccountStatus.PAUSED, last_refresh=now - timedelta(hours=18)),
        _account("fresh-paused", status=AccountStatus.PAUSED, last_refresh=now - timedelta(hours=1)),
        _account("reauth", status=AccountStatus.REAUTH_REQUIRED, last_refresh=now - timedelta(hours=24)),
        _account("deactivated", status=AccountStatus.DEACTIVATED, last_refresh=now - timedelta(hours=24)),
        _account("rate-limited", status=AccountStatus.RATE_LIMITED, last_refresh=now - timedelta(hours=24)),
        _account("quota-exceeded", status=AccountStatus.QUOTA_EXCEEDED, last_refresh=now - timedelta(hours=24)),
    ]

    selected = select_auth_guardian_candidates(accounts, now=now, max_age_seconds=12 * 3600, limit=10)

    assert [account.id for account in selected] == ["oldest-active", "stale-paused", "stale-active"]

    batched = select_auth_guardian_candidates(accounts, now=now, max_age_seconds=12 * 3600, limit=2)

    assert [account.id for account in batched] == ["oldest-active", "stale-paused"]


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
    assert scheduler.topology_blocked is False


def test_build_auth_guardian_scheduler_enabled_by_default_for_single_replica(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CODEX_LB_AUTH_GUARDIAN_ENABLED", raising=False)
    settings = _settings(leader_election_enabled=True)
    monkeypatch.setattr(settings_module, "get_settings", lambda: settings)

    scheduler = build_auth_guardian_scheduler()

    assert settings.auth_guardian_enabled is True
    assert scheduler.enabled is True
    assert scheduler.topology_blocked is False


def test_build_auth_guardian_scheduler_always_starts_and_reads_the_dashboard_toggle_per_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M2: the env alias no longer decides whether the loop exists; each pass reads the effective toggle."""
    settings = _settings(auth_guardian_enabled=False, leader_election_enabled=True)
    monkeypatch.setattr(settings_module, "get_settings", lambda: settings)

    scheduler = build_auth_guardian_scheduler()

    assert scheduler.enabled is True
    assert scheduler.dashboard_enabled is guardian_module._dashboard_guardian_enabled


def test_build_auth_guardian_scheduler_requires_leader_election_for_multi_replica(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _settings(
        auth_guardian_enabled=True,
        leader_election_enabled=False,
        instance_ring=["pod-a", "pod-b"],
    )
    monkeypatch.setattr(settings_module, "get_settings", lambda: settings)

    with caplog.at_level(logging.WARNING, logger=guardian_module.logger.name):
        scheduler = build_auth_guardian_scheduler()

    # The loop starts (the dashboard toggle is read per pass) but the static
    # topology gate makes every pass skip; operators must be told rather than
    # silently losing proactive refresh work.
    assert scheduler.enabled is True
    assert scheduler.topology_blocked is True
    assert any(
        "Auth Guardian disabled" in record.getMessage() and "without leader election" in record.getMessage()
        for record in caplog.records
    )

    settings.leader_election_enabled = True
    scheduler = build_auth_guardian_scheduler()

    assert scheduler.topology_blocked is False


def test_build_auth_guardian_scheduler_wires_leader_election_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(auth_guardian_enabled=True, leader_election_enabled=False)
    monkeypatch.setattr(settings_module, "get_settings", lambda: settings)

    scheduler = build_auth_guardian_scheduler()
    assert scheduler.leader_election_enabled is False

    settings.leader_election_enabled = True
    scheduler = build_auth_guardian_scheduler()
    assert scheduler.leader_election_enabled is True


def test_build_auth_guardian_scheduler_warns_when_topology_blocks_without_leader_election(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _settings(
        auth_guardian_enabled=True,
        leader_election_enabled=False,
        instance_ring=["pod-a", "pod-b"],
    )
    monkeypatch.setattr(settings_module, "get_settings", lambda: settings)

    with caplog.at_level(logging.WARNING, logger="app.core.auth.guardian"):
        scheduler = build_auth_guardian_scheduler()

    assert scheduler.topology_blocked is True
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "Auth Guardian disabled" in message
    assert "CODEX_LB_LEADER_ELECTION_ENABLED" in message


def test_build_auth_guardian_scheduler_does_not_warn_when_leader_election_enabled(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _settings(
        auth_guardian_enabled=True,
        leader_election_enabled=True,
        instance_ring=["pod-a", "pod-b"],
    )
    monkeypatch.setattr(settings_module, "get_settings", lambda: settings)

    with caplog.at_level(logging.WARNING, logger="app.core.auth.guardian"):
        scheduler = build_auth_guardian_scheduler()

    assert scheduler.enabled is True
    assert scheduler.topology_blocked is False
    assert not [record for record in caplog.records if record.levelno == logging.WARNING]


def _tick_scheduler(
    calls: list[str],
    *,
    now: datetime,
    dashboard_enabled: Callable[[], Awaitable[bool]],
    topology_blocked: bool = False,
    leader_election_enabled: bool = True,
    clock: Callable[[], datetime] | None = None,
) -> AuthGuardianScheduler:
    account = _account("stale-active", status=AccountStatus.ACTIVE, last_refresh=now - timedelta(hours=13))
    repo = _Repo([account])

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[_Repo]:
        yield repo

    async def _single_live_replica() -> int:
        return 1

    return AuthGuardianScheduler(
        interval_seconds=21600,
        enabled=True,
        dashboard_enabled=dashboard_enabled,
        topology_blocked=topology_blocked,
        max_age_seconds=12 * 3600,
        batch_size=10,
        concurrency=1,
        jitter_seconds=0.0,
        leader_election_enabled=leader_election_enabled,
        live_replica_count=_single_live_replica,
        leader_election_factory=lambda: _Leader(),
        repo_factory=repo_factory,
        auth_manager_factory=lambda _repo: _AuthManager(calls),
        sleep=lambda _delay: _noop_sleep(),
        now=clock or (lambda: now),
    )


@pytest.mark.asyncio
async def test_auth_guardian_ticks_follow_the_dashboard_toggle_without_restart() -> None:
    """M2: booted enabled -> dashboard off -> next tick skipped -> dashboard on -> next tick runs."""
    now = datetime(2026, 1, 2, 12, 0, 0)
    # Fake clock: every tick is one guardian interval later, so the account
    # refreshed on the first tick is stale again (> max age) by the third.
    clock = {"now": now}
    calls: list[str] = []
    toggle = {"enabled": True}

    async def dashboard_enabled() -> bool:
        return toggle["enabled"]

    def tick() -> None:
        clock["now"] += timedelta(hours=13)

    scheduler = _tick_scheduler(calls, now=now, dashboard_enabled=dashboard_enabled, clock=lambda: clock["now"])

    await scheduler._refresh_once()
    assert calls == ["stale-active"]

    tick()
    toggle["enabled"] = False
    await scheduler._refresh_once()
    assert calls == ["stale-active"]

    tick()
    toggle["enabled"] = True
    await scheduler._refresh_once()
    assert calls == ["stale-active", "stale-active"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("topology_blocked", "leader_election_enabled", "expected_calls"),
    [
        pytest.param(False, False, ["stale-active"], id="single-replica"),
        pytest.param(False, True, ["stale-active"], id="multi-replica-with-election"),
        pytest.param(True, False, [], id="multi-replica-without-election"),
    ],
)
async def test_auth_guardian_dashboard_toggle_cannot_override_the_topology_gate(
    topology_blocked: bool,
    leader_election_enabled: bool,
    expected_calls: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The static topology gate (multi-replica ring without election) wins over a dashboard ``true``."""
    now = datetime(2026, 1, 2, 12, 0, 0)
    calls: list[str] = []
    scheduler = _tick_scheduler(
        calls,
        now=now,
        dashboard_enabled=_always_enabled,
        topology_blocked=topology_blocked,
        leader_election_enabled=leader_election_enabled,
    )

    with caplog.at_level(logging.DEBUG, logger="app.core.auth.guardian"):
        await scheduler._refresh_once()

    assert calls == expected_calls
    # The builder logs the static topology block once at WARNING; a pass that
    # skips because of it only says so at DEBUG (a 6-hourly WARNING for a
    # condition that cannot change without a restart is noise).
    assert [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING] == []
    skips = [record.getMessage() for record in caplog.records if "without leader election" in record.getMessage()]
    if topology_blocked:
        assert len(skips) == 1
    else:
        assert skips == []


@pytest.mark.asyncio
async def test_auth_guardian_refresh_once_refreshes_stale_active_and_skips_others() -> None:
    now = datetime(2026, 1, 2, 12, 0, 0)
    accounts = [
        _account("fresh-active", status=AccountStatus.ACTIVE, last_refresh=now - timedelta(hours=1)),
        _account("stale-active", status=AccountStatus.ACTIVE, last_refresh=now - timedelta(hours=13)),
        _account("reauth", status=AccountStatus.REAUTH_REQUIRED, last_refresh=now - timedelta(hours=13)),
    ]
    repo = _Repo(accounts)
    calls: list[str] = []

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[_Repo]:
        yield repo

    scheduler = AuthGuardianScheduler(
        interval_seconds=21600,
        enabled=True,
        dashboard_enabled=_always_enabled,
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
async def test_auth_guardian_refresh_once_refreshes_stale_paused_without_changing_status() -> None:
    now = datetime(2026, 1, 2, 12, 0, 0)
    paused = _account("stale-paused", status=AccountStatus.PAUSED, last_refresh=now - timedelta(hours=13))
    repo = _Repo([paused])
    calls: list[str] = []

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[_Repo]:
        yield repo

    scheduler = AuthGuardianScheduler(
        interval_seconds=21600,
        enabled=True,
        dashboard_enabled=_always_enabled,
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

    assert calls == ["stale-paused"]
    assert paused.status is AccountStatus.PAUSED


@pytest.mark.asyncio
async def test_auth_guardian_refresh_once_survives_candidate_session_close() -> None:
    now = datetime(2026, 1, 2, 12, 0, 0)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            session.add(_account("stale-active", status=AccountStatus.ACTIVE, last_refresh=now - timedelta(hours=13)))
            await session.commit()

        @asynccontextmanager
        async def repo_factory() -> AsyncIterator[AccountsRepository]:
            session: AsyncSession = session_factory()
            try:
                yield AccountsRepository(session)
            finally:
                await close_session(session)

        calls: list[str] = []
        scheduler = AuthGuardianScheduler(
            interval_seconds=21600,
            enabled=True,
            dashboard_enabled=_always_enabled,
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

        assert calls == ["stale-active"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_auth_guardian_skips_pass_when_dynamic_ring_shows_multiple_replicas(
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime(2026, 1, 2, 12, 0, 0)
    account = _account("stale-active", status=AccountStatus.ACTIVE, last_refresh=now - timedelta(hours=13))
    repo = _Repo([account])
    calls: list[str] = []

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[_Repo]:
        yield repo

    async def _two_live_replicas() -> int:
        return 2

    scheduler = AuthGuardianScheduler(
        interval_seconds=21600,
        enabled=True,
        dashboard_enabled=_always_enabled,
        max_age_seconds=12 * 3600,
        batch_size=10,
        concurrency=1,
        jitter_seconds=0.0,
        leader_election_enabled=False,
        live_replica_count=_two_live_replicas,
        leader_election_factory=lambda: _Leader(),
        repo_factory=repo_factory,
        auth_manager_factory=lambda _repo: _AuthManager(calls),
        sleep=lambda _delay: _noop_sleep(),
        now=lambda: now,
    )

    with caplog.at_level(logging.WARNING, logger="app.core.auth.guardian"):
        await scheduler._refresh_once()

    assert calls == []
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "CODEX_LB_LEADER_ELECTION_ENABLED" in message


@pytest.mark.asyncio
async def test_auth_guardian_runs_when_dynamic_ring_has_single_replica() -> None:
    now = datetime(2026, 1, 2, 12, 0, 0)
    account = _account("stale-active", status=AccountStatus.ACTIVE, last_refresh=now - timedelta(hours=13))
    repo = _Repo([account])
    calls: list[str] = []

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[_Repo]:
        yield repo

    async def _one_live_replica() -> int:
        return 1

    scheduler = AuthGuardianScheduler(
        interval_seconds=21600,
        enabled=True,
        dashboard_enabled=_always_enabled,
        max_age_seconds=12 * 3600,
        batch_size=10,
        concurrency=1,
        jitter_seconds=0.0,
        leader_election_enabled=False,
        live_replica_count=_one_live_replica,
        leader_election_factory=lambda: _Leader(),
        repo_factory=repo_factory,
        auth_manager_factory=lambda _repo: _AuthManager(calls),
        sleep=lambda _delay: _noop_sleep(),
        now=lambda: now,
    )

    await scheduler._refresh_once()

    assert calls == ["stale-active"]


@pytest.mark.asyncio
async def test_auth_guardian_ignores_dynamic_ring_when_leader_election_enabled() -> None:
    now = datetime(2026, 1, 2, 12, 0, 0)
    account = _account("stale-active", status=AccountStatus.ACTIVE, last_refresh=now - timedelta(hours=13))
    repo = _Repo([account])
    calls: list[str] = []
    ring_counted = False

    @asynccontextmanager
    async def repo_factory() -> AsyncIterator[_Repo]:
        yield repo

    async def _count_ring() -> int:
        nonlocal ring_counted
        ring_counted = True
        return 5

    scheduler = AuthGuardianScheduler(
        interval_seconds=21600,
        enabled=True,
        dashboard_enabled=_always_enabled,
        max_age_seconds=12 * 3600,
        batch_size=10,
        concurrency=1,
        jitter_seconds=0.0,
        leader_election_enabled=True,
        live_replica_count=_count_ring,
        leader_election_factory=lambda: _Leader(),
        repo_factory=repo_factory,
        auth_manager_factory=lambda _repo: _AuthManager(calls),
        sleep=lambda _delay: _noop_sleep(),
        now=lambda: now,
    )

    await scheduler._refresh_once()

    assert calls == ["stale-active"]
    assert ring_counted is False


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
        dashboard_enabled=_always_enabled,
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
        dashboard_enabled=_always_enabled,
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
        dashboard_enabled=_always_enabled,
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
        dashboard_enabled=_always_enabled,
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
        dashboard_enabled=_always_enabled,
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
        dashboard_enabled=_always_enabled,
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


async def _always_enabled() -> bool:
    return True


def _settings(
    *,
    leader_election_enabled: bool,
    auth_guardian_enabled: bool | None = None,
    instance_ring: list[str] | None = None,
) -> settings_module.Settings:
    settings = settings_module.Settings(
        _env_file=None,
        leader_election_enabled=leader_election_enabled,
        http_responses_session_bridge_instance_id="pod-a",
        http_responses_session_bridge_instance_ring=instance_ring or [],
    )
    if auth_guardian_enabled is not None:
        settings.auth_guardian_enabled = auth_guardian_enabled
    return settings

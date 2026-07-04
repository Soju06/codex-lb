from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import cast

import pytest

from app.core.auth import guardian as guardian_module
from app.core.auth.guardian import AuthGuardianScheduler, build_auth_guardian_scheduler, select_auth_guardian_candidates
from app.core.auth.refresh import RefreshError
from app.core.clients.anthropic.errors import ClaudeUpstreamError
from app.core.clients.anthropic.oauth import ClaudeRefreshResult
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


# ---------------------------------------------------------------------------
# Claude refresh-pass test fixtures
# ---------------------------------------------------------------------------


def _claude_account(
    account_id: str,
    *,
    expires_at: datetime | None,
    status: AccountStatus = AccountStatus.ACTIVE,
    refresh_token: bytes = b"rt",
) -> Account:
    """Build a Claude-flavored :class:`Account` row with the columns the
    guardian's Claude pass actually inspects. The remaining Codex-flavored
    columns are populated with the bare-minimum defaults so the model can be
    constructed without a database session.
    """
    now = datetime(2026, 1, 2, 12, 0, 0, tzinfo=timezone.utc).replace(tzinfo=None)
    return Account(
        id=account_id,
        chatgpt_account_id=f"workspace-{account_id}",
        email=f"{account_id}@example.com",
        alias=None,
        plan_type="claude_subscription",
        access_token_encrypted=b"access",
        refresh_token_encrypted=b"refresh",
        id_token_encrypted=b"id",
        last_refresh=now,
        status=status,
        deactivation_reason=None,
        provider="claude",
        claude_account_uuid=f"uuid-{account_id}",
        claude_refresh_token_encrypted=refresh_token,
        claude_access_token_encrypted=b"access-claude",
        claude_access_token_expires_at=expires_at,
    )


class _ClaudeAuthManager:
    """Stand-in for :class:`ClaudeAuthManager` covering the surface the
    auth guardian's Claude pass actually calls:

    - ``find_accounts_due_for_rotation(skew_seconds=...)``
    - ``rotate_claude_access_token(account)``

    Test-side state: ``due_accounts`` controls which accounts ``find_*``
    returns (filtered by ``expires_at <= now + skew_seconds``); ``rotate_responses``
    maps account_id -> sentinel. Sentinels:

    - ``"ok"`` (default): rotate succeeds, returns a synthetic
      ``ClaudeRefreshResult``.
    - ``"invalid_grant"``: rotate returns ``None`` (mirrors the real
      auth manager's behavior when ``invalid_grant`` deactivates the row).
    - an ``Exception`` instance: rotate raises it.
    - a ``ClaudeRefreshResult`` instance: returned verbatim.
    """

    #: Sentinel for "rotate returns ``None`` (invalid_grant → deactivated)".
    INVALID_GRANT = "invalid_grant"

    def __init__(
        self,
        due_accounts: list[Account] | None = None,
        rotate_responses: dict[str, object] | None = None,
        *,
        now: datetime | None = None,
    ) -> None:
        self.due_accounts = list(due_accounts or [])
        self.rotate_responses = dict(rotate_responses or {})
        self.rotate_calls: list[str] = []
        self.find_calls: list[int] = []
        self.lock_holders: list[str] = []
        self._now = now or datetime.now(timezone.utc).replace(tzinfo=None)

    async def find_accounts_due_for_rotation(self, *, skew_seconds: int) -> list[Account]:
        self.find_calls.append(skew_seconds)
        cutoff = self._now + timedelta(seconds=skew_seconds)
        # Mirror production: filter to accounts whose access token expires
        # within the skew window. Tests that pass ALL accounts as "due" must
        # also seed them with ``expires_at`` inside the window — otherwise
        # they will be filtered out here, which is the correct production
        # behavior the test is meant to exercise.
        return [
            account
            for account in self.due_accounts
            if account.claude_access_token_expires_at is not None and account.claude_access_token_expires_at <= cutoff
        ]

    async def rotate_claude_access_token(self, account: Account) -> ClaudeRefreshResult | None:
        self.rotate_calls.append(account.id)
        response = self.rotate_responses.get(account.id, "ok")
        if isinstance(response, BaseException):
            raise response
        if response == self.INVALID_GRANT:
            return None
        if isinstance(response, ClaudeRefreshResult):
            return response
        return ClaudeRefreshResult(
            access_token=f"AT-{account.id}",
            refresh_token=f"RT-{account.id}",
            expires_in=3600,
        )


async def _run_claude_pass(
    scheduler: AuthGuardianScheduler,
    claude_manager: _ClaudeAuthManager,
    *,
    skew_seconds: int = 600,
) -> None:
    """Helper that drives the Claude refresh pass through a small entrypoint
    rather than the full Codex ``_refresh_once`` so the tests can target only
    the Claude code path. The entrypoint is the same method the scheduler
    uses internally; tests then read the ``claude_manager`` to assert
    behavior.
    """
    await scheduler._run_claude_refresh_pass(
        claude_manager,  # type: ignore[arg-type]
        skew_seconds=skew_seconds,
    )


@pytest.mark.asyncio
async def test_tick_refreshes_claude_accounts_expiring_within_skew(
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    due = _claude_account("claude-due", expires_at=now + timedelta(seconds=120))
    not_due = _claude_account("claude-fresh", expires_at=now + timedelta(hours=1))
    claude_manager = _ClaudeAuthManager(due_accounts=[due, not_due])
    scheduler = AuthGuardianScheduler(
        interval_seconds=21600,
        enabled=True,
        max_age_seconds=12 * 3600,
        batch_size=10,
        concurrency=1,
        jitter_seconds=0.0,
        leader_election_factory=lambda: _Leader(),
        repo_factory=_null_repo_factory,
        auth_manager_factory=lambda _repo: _AuthManager([]),
        sleep=lambda _delay: _noop_sleep(),
        now=lambda: now,
    )

    with caplog.at_level(logging.INFO, logger="app.core.auth.guardian"):
        await _run_claude_pass(scheduler, claude_manager, skew_seconds=600)

    assert claude_manager.find_calls == [600]
    assert claude_manager.rotate_calls == ["claude-due"]
    # The spec mandates a structured ``claude.refresh.success`` log line
    # carrying the account_id; assert against the ``event`` extra attr.
    success_records = [
        record for record in caplog.records if getattr(record, "event", None) == "claude.refresh.success"
    ]
    assert [getattr(record, "account_id", None) for record in success_records] == ["claude-due"]


@pytest.mark.asyncio
async def test_claude_rotation_invalid_grant_disables_account_and_continues_with_others(
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    invalid_grant = _claude_account("claude-invalid", expires_at=now + timedelta(seconds=120))
    rotates_ok = _claude_account("claude-ok", expires_at=now + timedelta(seconds=120))
    untouched = _claude_account("claude-fresh", expires_at=now + timedelta(hours=1))
    claude_manager = _ClaudeAuthManager(
        due_accounts=[invalid_grant, rotates_ok],
        rotate_responses={
            # Per the auth manager contract, invalid_grant is handled inside
            # ``rotate_claude_access_token`` which then returns ``None`` and
            # deactivates the row. The guardian sees a normal ``None`` return.
            "claude-invalid": _ClaudeAuthManager.INVALID_GRANT,
        },
    )
    scheduler = AuthGuardianScheduler(
        interval_seconds=21600,
        enabled=True,
        max_age_seconds=12 * 3600,
        batch_size=10,
        concurrency=1,
        jitter_seconds=0.0,
        leader_election_factory=lambda: _Leader(),
        repo_factory=_null_repo_factory,
        auth_manager_factory=lambda _repo: _AuthManager([]),
        sleep=lambda _delay: _noop_sleep(),
        now=lambda: now,
    )

    with caplog.at_level(logging.INFO, logger="app.core.auth.guardian"):
        await _run_claude_pass(scheduler, claude_manager, skew_seconds=600)

    assert claude_manager.rotate_calls == ["claude-invalid", "claude-ok"]
    assert untouched.status == AccountStatus.ACTIVE
    # Structured log lines: invalid_grant path emits ``claude.refresh.disabled``;
    # the surviving account emits ``claude.refresh.success``.
    events = {getattr(record, "event", None): getattr(record, "account_id", None) for record in caplog.records}
    assert events.get("claude.refresh.success") == "claude-ok"
    assert events.get("claude.refresh.disabled") == "claude-invalid"
    assert (
        events.get("claude.refresh.disabled")
        and getattr(
            next(r for r in caplog.records if getattr(r, "event", None) == "claude.refresh.disabled"),
            "reason",
            None,
        )
        == "invalid_grant"
    )


@pytest.mark.asyncio
async def test_claude_rotation_upstream_error_does_not_disable_account() -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    flaky = _claude_account("claude-flaky", expires_at=now + timedelta(seconds=120))
    claude_manager = _ClaudeAuthManager(
        due_accounts=[flaky],
        rotate_responses={"claude-flaky": ClaudeUpstreamError("upstream 502")},
    )
    scheduler = AuthGuardianScheduler(
        interval_seconds=21600,
        enabled=True,
        max_age_seconds=12 * 3600,
        batch_size=10,
        concurrency=1,
        jitter_seconds=0.0,
        leader_election_factory=lambda: _Leader(),
        repo_factory=_null_repo_factory,
        auth_manager_factory=lambda _repo: _AuthManager([]),
        sleep=lambda _delay: _noop_sleep(),
        now=lambda: now,
    )

    # First tick: upstream error. Account must remain ACTIVE; backoff is recorded.
    await _run_claude_pass(scheduler, claude_manager, skew_seconds=600)

    assert claude_manager.rotate_calls == ["claude-flaky"]
    assert flaky.status == AccountStatus.ACTIVE
    assert flaky.deactivation_reason is None
    assert scheduler._in_backoff("claude-flaky") is True

    # Reset the rotate-call log so we can prove the second tick still attempts
    # the account (singleflight is keyed on account_id; rotating the fake
    # response would mask the assertion).
    claude_manager.rotate_responses["claude-flaky"] = ClaudeRefreshResult(
        access_token="AT2",
        refresh_token="RT2",
        expires_in=3600,
    )

    # Expire the backoff so the next tick actually re-attempts the account.
    scheduler._failures["claude-flaky"].retry_after_monotonic = 0.0  # type: ignore[attr-defined]

    await _run_claude_pass(scheduler, claude_manager, skew_seconds=600)

    assert claude_manager.rotate_calls == ["claude-flaky", "claude-flaky"]
    assert flaky.status == AccountStatus.ACTIVE


@pytest.mark.asyncio
async def test_claude_refresh_acquires_per_account_lock() -> None:
    """Two concurrent guardian ticks calling rotate for the same account must
    coalesce through ``ClaudeAuthManager``'s singleflight lock so only ONE
    OAuth call is issued per refresh cycle.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    target = _claude_account("claude-lock", expires_at=now + timedelta(seconds=120))
    start_barrier = asyncio.Event()
    release_barrier = asyncio.Event()
    inflight: dict[str, asyncio.Task[ClaudeRefreshResult]] = {}
    inflight_lock = asyncio.Lock()

    class _LockedClaudeAuthManager:
        """Stand-in that mimics the per-account singleflight semantics of
        the real :class:`ClaudeAuthManager`. Two concurrent
        ``rotate_claude_access_token`` calls for the same ``account_id``
        MUST coalesce onto the same in-flight task so only ONE
        ``rotate_calls`` increment is observed.
        """

        def __init__(self) -> None:
            self.rotate_calls = 0
            self.find_calls = 0

        async def find_accounts_due_for_rotation(self, *, skew_seconds: int) -> list[Account]:
            del skew_seconds
            self.find_calls += 1
            return [target]

        async def _singleflight_run(self, key: str, factory) -> ClaudeRefreshResult:
            async with inflight_lock:
                task = inflight.get(key)
                if task is None or task.done():
                    task = asyncio.create_task(factory())
                    inflight[key] = task

                    def _clear(_t: asyncio.Task[ClaudeRefreshResult]) -> None:
                        inflight.pop(key, None)

                    task.add_done_callback(_clear)
            return await asyncio.shield(task)

        async def rotate_claude_access_token(self, account: Account) -> ClaudeRefreshResult:
            async def _factory() -> ClaudeRefreshResult:
                # The factory body counts as the OAuth call.
                self.rotate_calls += 1
                start_barrier.set()
                await release_barrier.wait()
                return ClaudeRefreshResult(
                    access_token="AT",
                    refresh_token="RT",
                    expires_in=3600,
                )

            return await self._singleflight_run(account.id, _factory)

    claude_manager = _LockedClaudeAuthManager()
    scheduler = AuthGuardianScheduler(
        interval_seconds=21600,
        enabled=True,
        max_age_seconds=12 * 3600,
        batch_size=10,
        concurrency=1,
        jitter_seconds=0.0,
        leader_election_factory=lambda: _Leader(),
        repo_factory=_null_repo_factory,
        auth_manager_factory=lambda _repo: _AuthManager([]),
        sleep=lambda _delay: _noop_sleep(),
        now=lambda: now,
    )

    first = asyncio.create_task(_run_claude_pass(scheduler, claude_manager, skew_seconds=600))  # ty:ignore[invalid-argument-type]
    second = asyncio.create_task(_run_claude_pass(scheduler, claude_manager, skew_seconds=600))  # ty:ignore[invalid-argument-type]

    await asyncio.wait_for(start_barrier.wait(), timeout=1)
    # Give the second task a chance to enter the singleflight and observe the
    # in-flight task before we release it.
    await asyncio.sleep(0)
    release_barrier.set()

    await asyncio.gather(first, second)

    assert claude_manager.find_calls == 2
    assert claude_manager.rotate_calls == 1


@asynccontextmanager
async def _null_repo_factory() -> AsyncIterator[_Repo]:
    """Empty repo factory for tests that target only the Claude pass — the
    Codex ``_refresh_once`` path requires a populated repo, but tests that
    invoke ``_run_claude_refresh_pass`` directly skip the Codex pass.
    """
    yield _Repo([])


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

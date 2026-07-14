"""Cross-replica token-refresh serialization regressions.

Two replicas are simulated as two independent AsyncSessions/AuthManagers over
one database with distinct refresh-claim claimant identities — the established
multi-replica pattern from ``tests/integration/test_multi_replica.py``.

Before the ``account_refresh_claims`` serialization landed, the concurrent-race
tests here failed with the loser calling upstream a second time, receiving a
permanent ``refresh_token_reused`` error, and writing ``REAUTH_REQUIRED`` (also
deleting the account's sticky sessions).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.auth.refresh import RefreshError, TokenRefreshResult
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountRefreshClaim, AccountStatus, StickySession, StickySessionKind
from app.db.session import SessionLocal
from app.modules.accounts import auth_manager as auth_manager_module
from app.modules.accounts.auth_manager import AuthManager
from app.modules.accounts.refresh_claims import RefreshClaimCoordinator
from app.modules.accounts.repository import AccountsRepository

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clear_refresh_state() -> None:
    auth_manager_module._clear_refresh_singleflight_state()


def _rotated_result(account_id: str) -> TokenRefreshResult:
    return TokenRefreshResult(
        access_token="access-new",
        refresh_token="refresh-new",
        id_token="id-new",
        account_id=None,
        plan_type="plus",
        email=None,
    )


async def _create_account(account_id: str, *, refresh_token: str = "refresh-old") -> None:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        session.add(
            Account(
                id=account_id,
                email=f"{account_id}@example.com",
                plan_type="plus",
                access_token_encrypted=encryptor.encrypt("access-old"),
                refresh_token_encrypted=encryptor.encrypt(refresh_token),
                id_token_encrypted=encryptor.encrypt("id-old"),
                last_refresh=utcnow(),
                status=AccountStatus.ACTIVE,
            )
        )
        session.add(
            StickySession(
                key=f"sticky-{account_id}",
                kind=StickySessionKind.STICKY_THREAD,
                account_id=account_id,
            )
        )
        await session.commit()


async def _insert_claim(account_id: str, *, claimed_by: str, expires_in_seconds: float) -> None:
    now = utcnow()
    async with SessionLocal() as session:
        session.add(
            AccountRefreshClaim(
                account_id=account_id,
                claimed_by=claimed_by,
                claimed_at=now,
                claim_expires_at=now + timedelta(seconds=expires_in_seconds),
            )
        )
        await session.commit()


async def _account_snapshot(account_id: str) -> tuple[AccountStatus, str, bool]:
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = (await session.execute(select(Account).where(Account.id == account_id))).scalars().one()
        sticky_present = (
            await session.execute(select(StickySession.key).where(StickySession.account_id == account_id))
        ).scalar_one_or_none() is not None
        return account.status, encryptor.decrypt(account.refresh_token_encrypted), sticky_present


@pytest.mark.asyncio
async def test_concurrent_cross_replica_refresh_runs_one_upstream_exchange(db_setup, monkeypatch):
    """THE RACE (failed pre-claims): both replicas force-refresh the same
    account; pre-claims the loser POSTed the same single-use refresh token,
    received permanent ``refresh_token_reused``, wrote REAUTH_REQUIRED, and
    deleted the sticky session. With claims, exactly one upstream exchange runs
    and the loser adopts the winner's rotation."""
    account_id = "acc_claim_race"
    await _create_account(account_id)

    upstream_calls = 0
    winner_started = asyncio.Event()
    winner_release = asyncio.Event()

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        nonlocal upstream_calls
        upstream_calls += 1
        if upstream_calls > 1:
            # This is exactly what upstream returns to the loser of a
            # concurrent rotation of a single-use refresh token.
            raise RefreshError("refresh_token_reused", "refresh token reused", True)
        winner_started.set()
        await winner_release.wait()
        return _rotated_result(account_id)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    async with SessionLocal() as session_a, SessionLocal() as session_b:
        repo_a = AccountsRepository(session_a)
        repo_b = AccountsRepository(session_b)
        account_a = await repo_a.get_by_id(account_id)
        account_b = await repo_b.get_by_id(account_id)
        assert account_a is not None and account_b is not None
        manager_a = AuthManager(repo_a, refresh_claims=RefreshClaimCoordinator(claimant_id="replica-a"))
        manager_b = AuthManager(repo_b, refresh_claims=RefreshClaimCoordinator(claimant_id="replica-b"))

        task_a = asyncio.create_task(manager_a.refresh_account(account_a))
        await asyncio.wait_for(winner_started.wait(), timeout=5)
        # Replica A holds the claim and is mid-exchange; replica B must wait.
        task_b = asyncio.create_task(manager_b.refresh_account(account_b))
        await asyncio.sleep(0.1)
        assert not task_b.done()

        winner_release.set()
        result_a = await asyncio.wait_for(task_a, timeout=5)
        result_b = await asyncio.wait_for(task_b, timeout=5)

    encryptor = TokenEncryptor()
    assert upstream_calls == 1
    assert encryptor.decrypt(result_a.refresh_token_encrypted) == "refresh-new"
    assert encryptor.decrypt(result_b.refresh_token_encrypted) == "refresh-new"
    status, stored_refresh_token, sticky_present = await _account_snapshot(account_id)
    assert status == AccountStatus.ACTIVE
    assert stored_refresh_token == "refresh-new"
    assert sticky_present is True

    # The claim was released after the winner persisted.
    async with SessionLocal() as session:
        remaining = (
            await session.execute(select(AccountRefreshClaim).where(AccountRefreshClaim.account_id == account_id))
        ).scalar_one_or_none()
        assert remaining is None


@pytest.mark.asyncio
async def test_expired_foreign_claim_is_taken_over(db_setup, monkeypatch):
    """Crashed-claimant liveness: an expired foreign claim must not block refresh."""
    account_id = "acc_claim_expired"
    await _create_account(account_id)
    await _insert_claim(account_id, claimed_by="dead-replica", expires_in_seconds=-5)

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        return _rotated_result(account_id)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        manager = AuthManager(repo, refresh_claims=RefreshClaimCoordinator(claimant_id="replica-a"))
        result = await asyncio.wait_for(manager.refresh_account(account), timeout=5)

    assert TokenEncryptor().decrypt(result.refresh_token_encrypted) == "refresh-new"
    status, stored_refresh_token, _ = await _account_snapshot(account_id)
    assert status == AccountStatus.ACTIVE
    assert stored_refresh_token == "refresh-new"


@pytest.mark.asyncio
async def test_unexpired_foreign_claim_times_out_transient_and_is_not_cached(db_setup, monkeypatch):
    """Bounded wait: with a live foreign claim the loser must fail with a
    transient (non-permanent) error, never call upstream, never touch account
    status, and the singleflight must not cache the failure as permanent."""
    monkeypatch.setenv("CODEX_LB_TOKEN_REFRESH_CLAIM_WAIT_SECONDS", "0.3")
    monkeypatch.setenv("CODEX_LB_TOKEN_REFRESH_CLAIM_POLL_SECONDS", "0.05")
    get_settings.cache_clear()

    account_id = "acc_claim_blocked"
    await _create_account(account_id)
    await _insert_claim(account_id, claimed_by="other-replica", expires_in_seconds=60)

    upstream_calls = 0

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        nonlocal upstream_calls
        upstream_calls += 1
        return _rotated_result(account_id)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        manager = AuthManager(repo, refresh_claims=RefreshClaimCoordinator(claimant_id="replica-b"))

        with pytest.raises(RefreshError) as exc_info:
            await manager.ensure_fresh(account, force=True)
        assert exc_info.value.code == "refresh_claim_timeout"
        assert exc_info.value.is_permanent is False
        assert exc_info.value.transport_error is True
        assert upstream_calls == 0

        status, stored_refresh_token, sticky_present = await _account_snapshot(account_id)
        assert status == AccountStatus.ACTIVE
        assert stored_refresh_token == "refresh-old"
        assert sticky_present is True

        # Release the foreign claim: the next forced refresh must proceed
        # immediately. A cached permanent failure would re-raise instead.
        async with SessionLocal() as cleanup_session:
            claim = (
                await cleanup_session.execute(
                    select(AccountRefreshClaim).where(AccountRefreshClaim.account_id == account_id)
                )
            ).scalar_one()
            await cleanup_session.delete(claim)
            await cleanup_session.commit()

        refreshed = await asyncio.wait_for(manager.ensure_fresh(account, force=True), timeout=5)
        assert upstream_calls == 1
        assert TokenEncryptor().decrypt(refreshed.refresh_token_encrypted) == "refresh-new"


@pytest.mark.asyncio
async def test_claim_wait_then_exchange_caps_to_remaining_budget(db_setup, monkeypatch):
    """Budget recompute after a claim wait: when the winner acquires the claim
    only after waiting out a foreign claim, it MUST cap the upstream OAuth
    exchange to the caller's REMAINING budget rather than restarting with the
    original full budget. The singleflight body is shielded from caller
    cancellation, so a full-budget exchange after a full-budget wait would
    overrun the request deadline while pinning the repo session and singleflight
    entry."""
    from app.core.auth.refresh import (
        get_token_refresh_timeout_override,
        pop_token_refresh_timeout_override,
        push_token_refresh_timeout_override,
    )

    account_id = "acc_claim_budget_cap"
    await _create_account(account_id)

    observed_override: list[float | None] = []

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        observed_override.append(get_token_refresh_timeout_override())
        return _rotated_result(account_id)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    class _WaitThenWinClaims:
        @property
        def claimant_id(self) -> str:
            return "replica-slow-win"

        async def try_acquire(self, account_id: str, *, ttl_seconds: float, owner: str) -> bool:
            del account_id, ttl_seconds, owner
            # Simulate waiting out a foreign claim that then releases: consume a
            # chunk of the caller budget before this replica wins the claim.
            await asyncio.sleep(0.5)
            return True

        async def release(self, account_id: str, *, owner: str) -> None:
            del account_id, owner

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        manager = AuthManager(repo, refresh_claims=_WaitThenWinClaims())

        token = push_token_refresh_timeout_override(5.0)
        try:
            result = await asyncio.wait_for(manager.refresh_account(account), timeout=5)
        finally:
            pop_token_refresh_timeout_override(token)

    assert TokenEncryptor().decrypt(result.refresh_token_encrypted) == "refresh-new"
    # The exchange ran exactly once and saw a capped override well below the
    # original 5.0s budget (roughly the remaining ~4.5s), never the full budget.
    assert len(observed_override) == 1
    capped = observed_override[0]
    assert capped is not None
    assert 0.0 < capped < 4.9


@pytest.mark.asyncio
async def test_claim_wait_exhausting_budget_fails_fast_without_exchange(db_setup, monkeypatch):
    """Fail fast when the claim wait consumes the whole caller budget: the
    winner MUST raise the transient (non-permanent) claim-timeout error before
    starting the upstream exchange rather than launching a full-budget OAuth
    exchange that overruns the request deadline."""
    from app.core.auth.refresh import (
        pop_token_refresh_timeout_override,
        push_token_refresh_timeout_override,
    )

    account_id = "acc_claim_budget_exhausted"
    await _create_account(account_id)

    upstream_calls = 0

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        nonlocal upstream_calls
        upstream_calls += 1
        return _rotated_result(account_id)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    released: list[str] = []

    class _WaitPastBudgetClaims:
        @property
        def claimant_id(self) -> str:
            return "replica-late-win"

        async def try_acquire(self, account_id: str, *, ttl_seconds: float, owner: str) -> bool:
            del ttl_seconds, owner
            # Wait longer than the 0.2s caller budget before winning the claim.
            await asyncio.sleep(0.3)
            del account_id
            return True

        async def release(self, account_id: str, *, owner: str) -> None:
            del owner
            released.append(account_id)

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        manager = AuthManager(repo, refresh_claims=_WaitPastBudgetClaims())

        token = push_token_refresh_timeout_override(0.2)
        try:
            with pytest.raises(RefreshError) as exc_info:
                await asyncio.wait_for(manager.refresh_account(account), timeout=5)
        finally:
            pop_token_refresh_timeout_override(token)

    assert exc_info.value.code == "refresh_claim_timeout"
    assert exc_info.value.is_permanent is False
    assert exc_info.value.transport_error is True
    # No upstream exchange ran, and the claim we won was released on the way out.
    assert upstream_calls == 0
    assert released == [account_id]
    status, stored_refresh_token, _ = await _account_snapshot(account_id)
    assert status == AccountStatus.ACTIVE
    assert stored_refresh_token == "refresh-old"


@pytest.mark.asyncio
async def test_claim_poll_sleep_capped_to_remaining_caller_budget(db_setup, monkeypatch):
    """Per-iteration poll sleep must be bounded by the caller budget.

    With a live foreign claim the loser polls until its bounded wait elapses.
    When the caller supplies a refresh budget SMALLER than the configured poll
    interval, each poll sleep must be capped to what remains of the caller
    deadline (``min(poll_interval, remaining)``) instead of sleeping the full
    interval. The singleflight body is shielded from caller cancellation, so a
    full-interval sleep would overrun the caller budget while holding the repo
    session and the inflight singleflight entry that later callers join.

    Before the fix the loop slept the full ~poll interval each iteration, so a
    0.1s caller budget with a 3.0s poll interval blocked for ~3.0s before the
    transient claim-timeout surfaced. The fix bounds the wait to the budget.
    """
    import time

    from app.core.auth.refresh import (
        pop_token_refresh_timeout_override,
        push_token_refresh_timeout_override,
    )

    # Configured cap and poll interval both far exceed the caller budget so the
    # only thing that can bound the wait is the per-iteration budget cap.
    monkeypatch.setenv("CODEX_LB_TOKEN_REFRESH_CLAIM_WAIT_SECONDS", "5.0")
    monkeypatch.setenv("CODEX_LB_TOKEN_REFRESH_CLAIM_POLL_SECONDS", "3.0")
    get_settings.cache_clear()

    account_id = "acc_claim_poll_budget_cap"
    await _create_account(account_id)
    await _insert_claim(account_id, claimed_by="other-replica", expires_in_seconds=60)

    upstream_calls = 0

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        nonlocal upstream_calls
        upstream_calls += 1
        return _rotated_result(account_id)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        manager = AuthManager(repo, refresh_claims=RefreshClaimCoordinator(claimant_id="replica-b"))

        token = push_token_refresh_timeout_override(0.1)
        started = time.monotonic()
        try:
            with pytest.raises(RefreshError) as exc_info:
                await asyncio.wait_for(manager.ensure_fresh(account, force=True), timeout=5)
        finally:
            pop_token_refresh_timeout_override(token)
        elapsed = time.monotonic() - started

    assert exc_info.value.code == "refresh_claim_timeout"
    assert exc_info.value.is_permanent is False
    assert exc_info.value.transport_error is True
    assert upstream_calls == 0
    # The wait was bounded by the ~0.1s caller budget, NOT the 3.0s poll
    # interval. A generous ceiling keeps this deterministic under load while
    # still failing loudly if the full poll interval is ever slept.
    assert elapsed < 1.5, f"claim poll overran caller budget: {elapsed:.3f}s"
    # The foreign claim was never won, so account material is untouched.
    status, stored_refresh_token, _ = await _account_snapshot(account_id)
    assert status == AccountStatus.ACTIVE
    assert stored_refresh_token == "refresh-old"


@pytest.mark.asyncio
async def test_saturated_admission_fails_fast_and_releases_claim_within_budget(db_setup, monkeypatch):
    """Admission wait must be bounded by the caller budget while the claim is held.

    The claim winner acquires token-refresh admission BEFORE the upstream OAuth
    exchange. ``WorkAdmissionController`` waits up to
    ``admission_wait_timeout_seconds`` for a slot on a saturated token-refresh
    semaphore, and that wait happens while this shielded task already holds the
    cross-replica DB refresh claim. Without capping the admission wait by the
    caller budget, a small-budget request would hold the claim — blocking peer
    replicas on the same account — for the full admission timeout.

    Here the single token-refresh admission slot is pre-held (saturated) and the
    admission wait timeout is a full 10s, but the caller budget is only 0.2s. The
    refresh MUST fail fast with the transient (non-permanent) claim-timeout,
    release the claim, and never start the exchange — all within roughly the
    caller budget, NOT the 10s admission timeout.
    """
    import time

    from app.core.auth.refresh import (
        pop_token_refresh_timeout_override,
        push_token_refresh_timeout_override,
    )
    from app.modules.proxy.work_admission import WorkAdmissionController

    account_id = "acc_claim_admission_saturated"
    await _create_account(account_id)

    upstream_calls = 0

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        nonlocal upstream_calls
        upstream_calls += 1
        return _rotated_result(account_id)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    released: list[str] = []

    class _ImmediateWinClaims:
        @property
        def claimant_id(self) -> str:
            return "replica-admission"

        async def try_acquire(self, account_id: str, *, ttl_seconds: float, owner: str) -> bool:
            del account_id, ttl_seconds, owner
            return True

        async def release(self, account_id: str, *, owner: str) -> None:
            del owner
            released.append(account_id)

    # One admission slot, held so the gate is saturated; a generous 10s wait
    # timeout so ONLY the caller budget can bound the admission wait.
    controller = WorkAdmissionController(
        token_refresh_limit=1,
        websocket_connect_limit=0,
        response_create_limit=0,
        compact_response_create_limit=0,
        admission_wait_timeout_seconds=10.0,
    )
    held_slot = await controller.acquire_token_refresh()
    try:
        async with SessionLocal() as session:
            repo = AccountsRepository(session)
            account = await repo.get_by_id(account_id)
            assert account is not None
            manager = AuthManager(
                repo,
                acquire_refresh_admission=controller.acquire_token_refresh,
                refresh_claims=_ImmediateWinClaims(),
            )

            token = push_token_refresh_timeout_override(0.2)
            started = time.monotonic()
            try:
                with pytest.raises(RefreshError) as exc_info:
                    await asyncio.wait_for(manager.refresh_account(account), timeout=5)
            finally:
                pop_token_refresh_timeout_override(token)
            elapsed = time.monotonic() - started
    finally:
        held_slot.release()

    assert exc_info.value.code == "refresh_claim_timeout"
    assert exc_info.value.is_permanent is False
    assert exc_info.value.transport_error is True
    # No exchange ran, and the DB refresh claim was released on the way out
    # (rather than held for the full 10s admission timeout, which would block
    # peer replicas on this account's claim).
    assert upstream_calls == 0
    assert released == [account_id]
    # Bounded by the ~0.2s budget, NOT the 10s admission wait timeout. A generous
    # ceiling keeps this deterministic under load while still failing loudly if
    # the full admission timeout is ever waited while holding the claim.
    assert elapsed < 2.0, f"admission wait overran caller budget while holding claim: {elapsed:.3f}s"
    status, stored_refresh_token, _ = await _account_snapshot(account_id)
    assert status == AccountStatus.ACTIVE
    assert stored_refresh_token == "refresh-old"


@pytest.mark.asyncio
async def test_winner_adopts_rotation_committed_before_its_claim(db_setup, monkeypatch):
    """Post-claim fresh re-read: when the material already rotated, the claim
    winner must adopt it with zero upstream calls."""
    account_id = "acc_claim_preclaim_rotation"
    await _create_account(account_id)
    encryptor = TokenEncryptor()

    async def unexpected_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        raise AssertionError("upstream exchange must not run when the material already rotated")

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", unexpected_refresh)

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None

        # Another replica rotates and commits after our snapshot was taken.
        async with SessionLocal() as winner_session:
            await AccountsRepository(winner_session).update_tokens(
                account_id,
                access_token_encrypted=encryptor.encrypt("access-new"),
                refresh_token_encrypted=encryptor.encrypt("refresh-new"),
                id_token_encrypted=encryptor.encrypt("id-new"),
                last_refresh=utcnow(),
            )

        manager = AuthManager(repo, refresh_claims=RefreshClaimCoordinator(claimant_id="replica-a"))
        result = await asyncio.wait_for(manager.refresh_account(account), timeout=5)

    assert encryptor.decrypt(result.refresh_token_encrypted) == "refresh-new"
    status, _, _ = await _account_snapshot(account_id)
    assert status == AccountStatus.ACTIVE


@pytest.mark.asyncio
async def test_permanent_failure_guard_sees_committed_rotation_despite_identity_map(db_setup, monkeypatch):
    """Stale-guard regression (failed pre-hardening): the loser's session
    identity map still holds the pre-rotation row; ``session.get`` returned it
    without a DB read and the loser wrote REAUTH_REQUIRED over a healthy
    account. The fresh ``populate_existing`` re-read must observe the winner's
    committed rotation and adopt it instead."""
    account_id = "acc_stale_guard"
    await _create_account(account_id)
    encryptor = TokenEncryptor()

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        raise RefreshError("refresh_token_reused", "refresh token reused", True)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    async with SessionLocal() as loser_session:
        loser_repo = AccountsRepository(loser_session)
        # Populate the loser session's identity map with the pre-rotation row.
        loser_account = await loser_repo.get_by_id(account_id)
        assert loser_account is not None

        # Winner commits the rotation through a different session (replica).
        async with SessionLocal() as winner_session:
            await AccountsRepository(winner_session).update_tokens(
                account_id,
                access_token_encrypted=encryptor.encrypt("access-new"),
                refresh_token_encrypted=encryptor.encrypt("refresh-new"),
                id_token_encrypted=encryptor.encrypt("id-new"),
                last_refresh=utcnow(),
            )

        # Exercise the legacy (unclaimed) path: the hardening must protect
        # callers even without a claim coordinator.
        manager = AuthManager(loser_repo)
        result = await manager.refresh_account(loser_account)

    assert encryptor.decrypt(result.refresh_token_encrypted) == "refresh-new"
    status, stored_refresh_token, sticky_present = await _account_snapshot(account_id)
    assert status == AccountStatus.ACTIVE
    assert stored_refresh_token == "refresh-new"
    assert sticky_present is True


@pytest.mark.asyncio
async def test_update_tokens_cas_rejects_stale_writer(db_setup):
    account_id = "acc_cas"
    await _create_account(account_id)
    encryptor = TokenEncryptor()

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        current_ciphertext = account.refresh_token_encrypted

        stale = await repo.update_tokens(
            account_id,
            access_token_encrypted=encryptor.encrypt("access-stale"),
            refresh_token_encrypted=encryptor.encrypt("refresh-stale"),
            id_token_encrypted=encryptor.encrypt("id-stale"),
            last_refresh=utcnow(),
            expected_refresh_token_encrypted=b"not-the-current-ciphertext",
        )
        assert stale is False

        applied = await repo.update_tokens(
            account_id,
            access_token_encrypted=encryptor.encrypt("access-new"),
            refresh_token_encrypted=encryptor.encrypt("refresh-new"),
            id_token_encrypted=encryptor.encrypt("id-new"),
            last_refresh=utcnow(),
            expected_refresh_token_encrypted=current_ciphertext,
        )
        assert applied is True

    _, stored_refresh_token, _ = await _account_snapshot(account_id)
    assert stored_refresh_token == "refresh-new"


@pytest.mark.asyncio
async def test_update_status_if_current_rejects_stale_refresh_token_material(db_setup):
    """The status CAS must also be conditioned on the refresh-token ciphertext
    so a permanent-failure downgrade cannot land over a concurrent rotation."""
    account_id = "acc_status_cas_material"
    await _create_account(account_id)

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        current_ciphertext = account.refresh_token_encrypted

        stale = await repo.update_status_if_current(
            account_id,
            AccountStatus.REAUTH_REQUIRED,
            "stale permanent-failure write",
            expected_status=AccountStatus.ACTIVE,
            expected_refresh_token_encrypted=b"not-the-current-ciphertext",
        )
        assert stale is False
        status, _, sticky_present = await _account_snapshot(account_id)
        assert status == AccountStatus.ACTIVE
        assert sticky_present is True

        applied = await repo.update_status_if_current(
            account_id,
            AccountStatus.REAUTH_REQUIRED,
            "current permanent-failure write",
            expected_status=AccountStatus.ACTIVE,
            expected_refresh_token_encrypted=current_ciphertext,
        )
        assert applied is True

    status, _, _ = await _account_snapshot(account_id)
    assert status == AccountStatus.REAUTH_REQUIRED


@pytest.mark.asyncio
async def test_permanent_failure_cas_loses_to_rotation_committed_during_status_write(db_setup, monkeypatch):
    """CAS race-window regression: a concurrent re-auth/import commits a token
    rotation AFTER the permanent-failure guard's fresh re-read but BEFORE its
    status CAS (status/reason/reset untouched, so the pre-hardening CAS
    matched). The stale REAUTH_REQUIRED write must lose and the freshly
    repaired account must stay active with the rotated material."""
    account_id = "acc_cas_race_window"
    await _create_account(account_id)
    encryptor = TokenEncryptor()

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        raise RefreshError("refresh_token_reused", "refresh token reused", True)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    class _RaceWindowRepo(AccountsRepository):
        async def get_by_id_fresh(self, account_id: str) -> Account | None:
            latest = await super().get_by_id_fresh(account_id)
            # Concurrent re-auth commits a rotation through another session in
            # the window between this fresh read and the status CAS.
            async with SessionLocal() as winner_session:
                await AccountsRepository(winner_session).update_tokens(
                    account_id,
                    access_token_encrypted=encryptor.encrypt("access-rotated"),
                    refresh_token_encrypted=encryptor.encrypt("refresh-rotated"),
                    id_token_encrypted=encryptor.encrypt("id-rotated"),
                    last_refresh=utcnow(),
                )
            return latest

    async with SessionLocal() as session:
        repo = _RaceWindowRepo(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        manager = AuthManager(repo)

        # The permanent-failure guard re-reads inside the status-CAS retry,
        # observes that the peer committed genuinely different refresh-token
        # material, and adopts the repaired row instead of re-raising the
        # permanent RefreshError. Re-raising would let the proxy caller invoke
        # LoadBalancer.mark_permanent_failure() and clobber the valid rotation.
        refreshed = await manager.refresh_account(account)

    assert refreshed.status == AccountStatus.ACTIVE
    assert TokenEncryptor().decrypt(refreshed.refresh_token_encrypted) == "refresh-rotated"
    status, stored_refresh_token, sticky_present = await _account_snapshot(account_id)
    assert status == AccountStatus.ACTIVE
    assert stored_refresh_token == "refresh-rotated"
    assert sticky_present is True


@pytest.mark.asyncio
async def test_permanent_failure_cas_retries_when_same_plaintext_re_encrypted(db_setup, monkeypatch):
    """Status-CAS retry regression: a concurrent re-auth/import re-encrypts the
    SAME refresh-token plaintext (non-deterministic Fernet) in the window
    between the permanent-failure guard's fresh re-read and its status CAS. The
    ciphertext guard misses even though there was no genuine rotation, and the
    account is still holding the very material that just failed permanently. The
    guard must re-read and retry the CAS against the freshly observed ciphertext
    and land the REAUTH_REQUIRED downgrade rather than skipping it and leaving a
    dead account active."""
    account_id = "acc_cas_reencrypt_retry"
    await _create_account(account_id)
    encryptor = TokenEncryptor()

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        raise RefreshError("refresh_token_reused", "refresh token reused", True)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    reencrypted = {"done": False}

    class _ReEncryptWindowRepo(AccountsRepository):
        async def get_by_id_fresh(self, account_id: str) -> Account | None:
            latest = await super().get_by_id_fresh(account_id)
            if not reencrypted["done"]:
                reencrypted["done"] = True
                # Re-auth re-encrypts the SAME plaintext to different bytes in
                # the window between this fresh read and the status CAS. Status/
                # reason/reset are untouched, so only the ciphertext guard trips.
                async with SessionLocal() as reauth_session:
                    await AccountsRepository(reauth_session).update_tokens(
                        account_id,
                        access_token_encrypted=encryptor.encrypt("access-old"),
                        refresh_token_encrypted=encryptor.encrypt("refresh-old"),
                        id_token_encrypted=encryptor.encrypt("id-old"),
                        last_refresh=utcnow(),
                    )
            return latest

    async with SessionLocal() as session:
        repo = _ReEncryptWindowRepo(session)
        account = await repo.get_by_id(account_id)
        assert account is not None
        manager = AuthManager(repo)

        with pytest.raises(RefreshError) as exc_info:
            await manager.refresh_account(account)

    assert exc_info.value.code == "refresh_token_reused"
    status, stored_refresh_token, sticky_present = await _account_snapshot(account_id)
    # The downgrade landed on retry: same (dead) plaintext, account de-routed.
    assert status == AccountStatus.REAUTH_REQUIRED
    assert stored_refresh_token == "refresh-old"
    assert sticky_present is False


def _encode_jwt(payload: dict) -> str:
    import base64
    import json

    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"header.{body}.sig"


def _make_auth_json(account_id: str, email: str) -> dict:
    payload = {
        "email": email,
        "chatgpt_account_id": account_id,
        "https://api.openai.com/auth": {"chatgpt_plan_type": "plus"},
    }
    return {
        "tokens": {
            "idToken": _encode_jwt(payload),
            "accessToken": "access-token",
            "refreshToken": "refresh-token",
            "accountId": account_id,
        },
    }


@pytest.mark.asyncio
async def test_proxy_401_with_foreign_claim_fails_over_without_reauth_write(async_client, monkeypatch):
    """Route-level regression (failed pre-claims by marking REAUTH_REQUIRED and
    deleting sticky sessions): an upstream 401 forces a token refresh while a
    foreign replica holds the account's refresh claim. The request must fail
    over to another account within the bounded wait, with zero upstream token
    exchanges and no status/sticky teardown."""
    import json

    import app.modules.proxy.service as proxy_module
    from app.modules.accounts.refresh_claims import set_refresh_claim_coordinator

    monkeypatch.setenv("CODEX_LB_TOKEN_REFRESH_CLAIM_WAIT_SECONDS", "0.3")
    monkeypatch.setenv("CODEX_LB_TOKEN_REFRESH_CLAIM_POLL_SECONDS", "0.05")
    get_settings.cache_clear()
    set_refresh_claim_coordinator(RefreshClaimCoordinator(claimant_id="this-replica"))

    for raw_account_id, email in (
        ("acc_claim_route_a", "claim-route-a@example.com"),
        ("acc_claim_route_b", "claim-route-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    async with SessionLocal() as session:
        account_ids = list((await session.execute(select(Account.id))).scalars().all())
        assert len(account_ids) == 2
        for account_id in account_ids:
            session.add(
                StickySession(
                    key=f"sticky-{account_id}",
                    kind=StickySessionKind.STICKY_THREAD,
                    account_id=account_id,
                )
            )
        await session.commit()
    for account_id in account_ids:
        await _insert_claim(account_id, claimed_by="other-replica", expires_in_seconds=60)

    refresh_exchange_calls = 0

    async def fake_refresh(refresh_token: str, **_kwargs: object) -> TokenRefreshResult:
        nonlocal refresh_exchange_calls
        refresh_exchange_calls += 1
        # Pre-claims this is what the race loser received upstream, and it
        # marked the account REAUTH_REQUIRED.
        raise RefreshError("refresh_token_reused", "refresh token reused", True)

    monkeypatch.setattr(auth_manager_module, "refresh_access_token", fake_refresh)

    invalidated_account_id: str | None = None
    captured_account_ids: list[str | None] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, base_url, raise_for_status, kwargs
        nonlocal invalidated_account_id
        if invalidated_account_id is None:
            invalidated_account_id = account_id
        captured_account_ids.append(account_id)
        if account_id == invalidated_account_id:
            raise proxy_module.ProxyResponseError(
                401,
                {"error": {"code": "invalid_api_key", "message": "token invalidated"}},
            )
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_claim_failover",'
            '"object":"response","status":"completed","usage":{"input_tokens":2,"output_tokens":1}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json={"model": "gpt-5.4", "instructions": "hi", "input": [], "stream": True},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = [json.loads(line[6:]) for line in lines if line.startswith("data: ") and line != "data: [DONE]"]
    assert any(event.get("type") == "response.completed" for event in events)
    assert captured_account_ids[0] == invalidated_account_id
    assert captured_account_ids[-1] != invalidated_account_id

    # The refresh claim was honored: no upstream token exchange ran at all.
    assert refresh_exchange_calls == 0

    async with SessionLocal() as session:
        accounts = list((await session.execute(select(Account))).scalars().all())
        assert {account.status for account in accounts} == {AccountStatus.ACTIVE}
        sticky_keys = set((await session.execute(select(StickySession.key))).scalars().all())
        assert {f"sticky-{account_id}" for account_id in account_ids} <= sticky_keys


@pytest.mark.asyncio
async def test_proxy_preflight_claim_timeout_fails_over_and_releases_lease(async_client, monkeypatch):
    """Route-level regression for the pre-401 proactive-refresh path.

    A transient refresh-claim timeout on the FIRST stream attempt (the
    proactive freshness check, before any upstream 401) must exclude the
    account, release its already-acquired stream lease, and fail over to
    another account. Before the fix the streaming retry loop only handled a
    transient claim failure in the post-401 forced-refresh path, so a
    first-attempt claim timeout propagated out of the generator (P2 #1) and,
    even where excluded, leaked the skipped account's stream lease (P2 #2).
    """
    import json

    import app.modules.proxy.service as proxy_module

    for raw_account_id, email in (
        ("acc_preflight_claim_a", "preflight-claim-a@example.com"),
        ("acc_preflight_claim_b", "preflight-claim-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    async with SessionLocal() as session:
        account_ids = list((await session.execute(select(Account.id))).scalars().all())
        assert len(account_ids) == 2
        for account_id in account_ids:
            session.add(
                StickySession(
                    key=f"sticky-{account_id}",
                    kind=StickySessionKind.STICKY_THREAD,
                    account_id=account_id,
                )
            )
        await session.commit()

    # Whichever account the retry loop freshens first fails with a transient
    # refresh-claim timeout (as if a foreign replica holds the claim on the
    # proactive, pre-401 freshness check); the other account freshens cleanly.
    first_seen: dict[str, str | None] = {"account_id": None}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        if first_seen["account_id"] is None:
            first_seen["account_id"] = account.id
        if account.id == first_seen["account_id"]:
            raise RefreshError(
                "transport_error",
                "refresh claim held by another replica",
                False,
                transport_error=True,
            )
        return account

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    released_lease_account_ids: list[str] = []
    original_release = proxy_module.LoadBalancer.release_account_lease

    async def spy_release(self, lease):
        if lease is not None:
            released_lease_account_ids.append(lease.account_id)
        return await original_release(self, lease)

    monkeypatch.setattr(proxy_module.LoadBalancer, "release_account_lease", spy_release)

    streamed_account_ids: list[str] = []
    released_before_stream: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, base_url, raise_for_status, kwargs
        if not streamed_account_ids:
            released_before_stream.extend(released_lease_account_ids)
        streamed_account_ids.append(account_id)
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_preflight_failover",'
            '"object":"response","status":"completed","usage":{"input_tokens":2,"output_tokens":1}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json={"model": "gpt-5.4", "instructions": "hi", "input": [], "stream": True},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = [json.loads(line[6:]) for line in lines if line.startswith("data: ") and line != "data: [DONE]"]
    assert any(event.get("type") == "response.completed" for event in events)

    failed_account_id = first_seen["account_id"]
    assert failed_account_id is not None
    # The pre-401 claim timeout excluded the failed account and failed over: it
    # never reached the upstream stream, and a different account served it.
    assert failed_account_id not in streamed_account_ids
    assert streamed_account_ids
    assert streamed_account_ids[-1] != failed_account_id
    # The skipped account's stream lease was released BEFORE failover streaming
    # (no leaked lease).
    assert failed_account_id in released_before_stream

    async with SessionLocal() as session:
        accounts = list((await session.execute(select(Account))).scalars().all())
        assert {account.status for account in accounts} == {AccountStatus.ACTIVE}


@pytest.mark.asyncio
async def test_proxy_preflight_permanent_refresh_releases_lease_before_failover(async_client, monkeypatch):
    """Route-level regression for the pre-401 proactive-refresh PERMANENT branch.

    A permanent ``RefreshError`` on the FIRST stream attempt (the proactive
    freshness check, before any upstream 401) marks the account permanently
    failed and fails over to another account. The failed account is removed from
    selection, but its already-acquired stream lease must be released BEFORE the
    failover ``continue`` -- otherwise the dead account's stream-concurrency slot
    stays occupied for the entire duration of the replacement stream. Before the
    fix the ``is_permanent`` branch fell through to ``continue`` without
    releasing ``current_account_lease`` (P2: leaked slot on permanent failover).
    """
    import json

    import app.modules.proxy.service as proxy_module

    for raw_account_id, email in (
        ("acc_preflight_perm_a", "preflight-perm-a@example.com"),
        ("acc_preflight_perm_b", "preflight-perm-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    async with SessionLocal() as session:
        account_ids = list((await session.execute(select(Account.id))).scalars().all())
        assert len(account_ids) == 2
        for account_id in account_ids:
            session.add(
                StickySession(
                    key=f"sticky-perm-{account_id}",
                    kind=StickySessionKind.STICKY_THREAD,
                    account_id=account_id,
                )
            )
        await session.commit()

    # Whichever account the retry loop freshens first fails PERMANENTLY on the
    # proactive, pre-401 freshness check; the other account freshens cleanly.
    first_seen: dict[str, str | None] = {"account_id": None}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        if first_seen["account_id"] is None:
            first_seen["account_id"] = account.id
        if account.id == first_seen["account_id"]:
            raise RefreshError(
                "refresh_token_reused",
                "refresh token reused",
                True,
            )
        return account

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    # Track only the tracked stream-concurrency lease (kind == "stream"); the
    # per-attempt ``response_create`` lease acquired inside ``_stream_once`` is a
    # DIFFERENT lease and would otherwise mask whether the stream-concurrency
    # slot was actually freed at failover.
    released_lease_account_ids: list[str] = []
    original_release = proxy_module.LoadBalancer.release_account_lease

    async def spy_release(self, lease):
        if lease is not None and lease.kind == "stream":
            released_lease_account_ids.append(lease.account_id)
        return await original_release(self, lease)

    monkeypatch.setattr(proxy_module.LoadBalancer, "release_account_lease", spy_release)

    streamed_account_ids: list[str] = []
    released_before_stream: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, base_url, raise_for_status, kwargs
        if not streamed_account_ids:
            released_before_stream.extend(released_lease_account_ids)
        streamed_account_ids.append(account_id)
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_preflight_perm_failover",'
            '"object":"response","status":"completed","usage":{"input_tokens":2,"output_tokens":1}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json={"model": "gpt-5.4", "instructions": "hi", "input": [], "stream": True},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = [json.loads(line[6:]) for line in lines if line.startswith("data: ") and line != "data: [DONE]"]
    assert any(event.get("type") == "response.completed" for event in events)

    failed_account_id = first_seen["account_id"]
    assert failed_account_id is not None
    # The permanent failure removed the failed account from selection and failed
    # over: it never reached the upstream stream, and a different account served.
    assert failed_account_id not in streamed_account_ids
    assert streamed_account_ids
    assert streamed_account_ids[-1] != failed_account_id
    # The failed account's stream lease was released BEFORE failover streaming
    # (no leaked concurrency slot held for the replacement stream's duration).
    assert failed_account_id in released_before_stream

    # The permanent failure was recorded; the failover account stays ACTIVE.
    async with SessionLocal() as session:
        status_by_id = dict((await session.execute(select(Account.id, Account.status))).all())
    assert status_by_id[failed_account_id] == AccountStatus.REAUTH_REQUIRED
    other_id = next(account_id for account_id in status_by_id if account_id != failed_account_id)
    assert status_by_id[other_id] == AccountStatus.ACTIVE


@pytest.mark.asyncio
async def test_proxy_post_401_permanent_refresh_releases_lease_before_failover(async_client, monkeypatch):
    """Route-level regression for the post-401 forced-refresh PERMANENT branch.

    The proactive freshness check succeeds so the stream opens; the upstream
    returns a 401 and the subsequent forced (``force=True``) refresh fails
    PERMANENTLY. The account is marked permanently failed and the request fails
    over to another account. As with the proactive permanent branch, the failed
    account's already-acquired stream lease must be released BEFORE the failover
    ``continue`` so its stream-concurrency slot is not held for the entire
    duration of the replacement stream.
    """
    import json

    import app.modules.proxy.service as proxy_module

    for raw_account_id, email in (
        ("acc_post401_perm_a", "post401-perm-a@example.com"),
        ("acc_post401_perm_b", "post401-perm-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    # The proactive freshness check (force=False) succeeds so the stream opens;
    # the post-401 forced refresh (force=True) fails PERMANENTLY for the first
    # account that reaches it. The other account never receives a 401.
    first_forced: dict[str, str | None] = {"account_id": None}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, timeout_seconds
        if force:
            if first_forced["account_id"] is None:
                first_forced["account_id"] = account.id
            if account.id == first_forced["account_id"]:
                raise RefreshError(
                    "refresh_token_reused",
                    "refresh token reused",
                    True,
                )
        return account

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    # Track only the tracked stream-concurrency lease (kind == "stream"). The
    # failed account opens a real stream (to receive the 401), so ``_stream_once``
    # acquires and releases a separate ``response_create`` lease for it; without
    # this filter that unrelated release would mask whether the stream-concurrency
    # slot itself was freed at the permanent-failure failover.
    released_lease_account_ids: list[str] = []
    original_release = proxy_module.LoadBalancer.release_account_lease

    async def spy_release(self, lease):
        if lease is not None and lease.kind == "stream":
            released_lease_account_ids.append(lease.account_id)
        return await original_release(self, lease)

    monkeypatch.setattr(proxy_module.LoadBalancer, "release_account_lease", spy_release)

    streamed_account_ids: list[str] = []
    released_before_failover_stream: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, base_url, raise_for_status, kwargs
        # First account opens then receives a 401 (triggering the forced
        # refresh that fails permanently). The failover account streams cleanly.
        if not streamed_account_ids:
            streamed_account_ids.append(account_id)
            raise proxy_module.ProxyResponseError(
                401,
                {"error": {"code": "invalid_api_key", "message": "token invalidated"}},
            )
        released_before_failover_stream.extend(released_lease_account_ids)
        streamed_account_ids.append(account_id)
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_post401_perm_failover",'
            '"object":"response","status":"completed","usage":{"input_tokens":2,"output_tokens":1}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json={"model": "gpt-5.4", "instructions": "hi", "input": [], "stream": True},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = [json.loads(line[6:]) for line in lines if line.startswith("data: ") and line != "data: [DONE]"]
    assert any(event.get("type") == "response.completed" for event in events)

    failed_account_id = first_forced["account_id"]
    assert failed_account_id is not None
    # Two distinct accounts streamed: the one that opened, took the 401, and
    # permanently failed its forced refresh, then the failover account.
    assert len(set(streamed_account_ids)) == 2
    assert streamed_account_ids[0] != streamed_account_ids[-1]
    # The permanently-failed account's stream-concurrency lease was released
    # BEFORE the failover account started streaming (no leaked slot held for the
    # replacement stream's duration).
    assert failed_account_id in released_before_failover_stream

    async with SessionLocal() as session:
        status_by_id = dict((await session.execute(select(Account.id, Account.status))).all())
    assert status_by_id[failed_account_id] == AccountStatus.REAUTH_REQUIRED
    other_id = next(account_id for account_id in status_by_id if account_id != failed_account_id)
    assert status_by_id[other_id] == AccountStatus.ACTIVE


@pytest.mark.asyncio
async def test_proxy_preflight_claim_timeout_exhaustion_reports_upstream_unavailable(async_client, monkeypatch):
    """Route-level regression: when EVERY candidate account hits a transient
    refresh-claim timeout on the proactive freshness check before the stream
    opens, exhaustion must surface a retryable ``upstream_unavailable`` error,
    not a misleading generic ``no_accounts`` response.

    Before the fix the transient-claim failover branch released the lease and
    excluded the account but left ``last_retryable_stream_error`` unset, so after
    attempts were exhausted the loop fell through to the no-accounts path and the
    client saw ``no_accounts`` for what is really transient contention.
    """
    import json

    import app.modules.proxy.service as proxy_module

    for raw_account_id, email in (
        ("acc_exhaust_claim_a", "exhaust-claim-a@example.com"),
        ("acc_exhaust_claim_b", "exhaust-claim-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    # EVERY account's proactive freshness check raises a transient refresh-claim
    # timeout (as if a foreign replica holds each account's claim).
    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        raise RefreshError(
            "transport_error",
            "refresh claim held by another replica",
            False,
            transport_error=True,
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    streamed_account_ids: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, base_url, raise_for_status, kwargs
        streamed_account_ids.append(account_id)
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_should_not_open",'
            '"object":"response","status":"completed","usage":{"input_tokens":2,"output_tokens":1}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json={"model": "gpt-5.4", "instructions": "hi", "input": [], "stream": True},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = [json.loads(line[6:]) for line in lines if line.startswith("data: ") and line != "data: [DONE]"]
    failed_events = [event for event in events if event.get("type") == "response.failed"]
    assert failed_events, f"expected a response.failed event, got {events}"
    error_codes = {event["response"]["error"]["code"] for event in failed_events}
    # Transient contention surfaces as retryable upstream_unavailable, NOT no_accounts.
    assert error_codes == {"upstream_unavailable"}
    assert "no_accounts" not in error_codes
    # No stream ever opened (every candidate failed its freshness check).
    assert streamed_account_ids == []

    # The transient claim contention is never recorded as a permanent failure.
    async with SessionLocal() as session:
        accounts = list((await session.execute(select(Account))).scalars().all())
        assert {account.status for account in accounts} == {AccountStatus.ACTIVE}


@pytest.mark.asyncio
async def test_proxy_pinned_stream_claim_timeout_stays_on_owner_and_reports_upstream_unavailable(
    async_client, monkeypatch
):
    """Route-level regression for the hard-pinned streaming preflight sub-case.

    A stream turn pinned by ``previous_response_id`` sets ``preferred_account_id``
    (and ``require_preferred_account``), so the movable transient-claim failover
    branch is correctly skipped -- a pinned request must not cross accounts. But
    before the fix the pinned path then fell through to the unconditional
    ``continue``: it reselected the same owner account on every attempt without
    releasing the already-acquired stream lease or recording a retryable error,
    leaking a stream-concurrency slot each iteration and finally surfacing a
    misleading ``no_accounts`` result once attempts were exhausted.

    The fix keeps the request on its owner account (no crossing) but releases the
    lease and surfaces a retryable ``upstream_unavailable`` promptly.
    """
    import json

    import app.modules.proxy.service as proxy_module

    for raw_account_id, email in (
        ("acc_pinned_claim_a", "pinned-claim-a@example.com"),
        ("acc_pinned_claim_b", "pinned-claim-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    async with SessionLocal() as session:
        account_ids = sorted((await session.execute(select(Account.id))).scalars().all())
    assert len(account_ids) == 2
    owner_account_id = account_ids[0]
    alternate_account_id = account_ids[1]

    # Pin the turn to the owner account: ``previous_response_id`` resolves to the
    # owner, setting preferred_account_id and require_preferred_account.
    async def fake_owner(self, *, previous_response_id, api_key, session_id=None, surface):
        del self, previous_response_id, api_key, session_id, surface
        return owner_account_id

    monkeypatch.setattr(proxy_module.ProxyService, "_resolve_websocket_previous_response_owner", fake_owner)

    # The pinned owner account's proactive freshness check raises a transient
    # refresh-claim timeout (claim held by a foreign replica). No other account
    # should ever be freshened, because the pinned request must not cross.
    freshened_account_ids: list[str] = []

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        freshened_account_ids.append(account.id)
        raise RefreshError(
            "refresh_claim_timeout",
            "refresh claim held by another replica",
            False,
            transport_error=True,
        )

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    released_lease_account_ids: list[str] = []
    original_release = proxy_module.LoadBalancer.release_account_lease

    async def spy_release(self, lease):
        if lease is not None:
            released_lease_account_ids.append(lease.account_id)
        return await original_release(self, lease)

    monkeypatch.setattr(proxy_module.LoadBalancer, "release_account_lease", spy_release)

    streamed_account_ids: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, base_url, raise_for_status, kwargs
        streamed_account_ids.append(account_id)
        yield (
            'data: {"type":"response.completed","response":{"id":"resp_should_not_open",'
            '"object":"response","status":"completed","usage":{"input_tokens":2,"output_tokens":1}}}\n\n'
        )

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json={
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [],
            "stream": True,
            "previous_response_id": "resp_pinned_owner",
        },
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = [json.loads(line[6:]) for line in lines if line.startswith("data: ") and line != "data: [DONE]"]
    failed_events = [event for event in events if event.get("type") == "response.failed"]
    assert failed_events, f"expected a response.failed event, got {events}"
    error_codes = {event["response"]["error"]["code"] for event in failed_events}
    # Pinned transient contention surfaces as retryable upstream_unavailable, NOT
    # a misleading no_accounts / previous_response_owner_unavailable exhaustion.
    assert error_codes == {"upstream_unavailable"}
    assert "no_accounts" not in error_codes
    assert "previous_response_owner_unavailable" not in error_codes
    # No stream ever opened, and the request never crossed to the alternate
    # account (the account-ownership invariant is preserved).
    assert streamed_account_ids == []
    assert alternate_account_id not in freshened_account_ids
    assert freshened_account_ids == [owner_account_id]
    # The pinned owner's already-acquired stream lease was released (not leaked).
    assert owner_account_id in released_lease_account_ids

    # The transient claim contention is never recorded as a permanent failure.
    async with SessionLocal() as session:
        accounts = list((await session.execute(select(Account))).scalars().all())
        assert {account.status for account in accounts} == {AccountStatus.ACTIVE}


@pytest.mark.asyncio
async def test_proxy_post_401_forced_refresh_claim_timeout_exhaustion_reports_upstream_unavailable(
    async_client, monkeypatch
):
    """Route-level regression for the post-401 forced-refresh sibling branch.

    When EVERY candidate account opens far enough to receive an upstream 401 and
    its subsequent forced (``force=True``) refresh raises a transient refresh-claim
    timeout (claim held by another replica), exhaustion must surface a retryable
    ``upstream_unavailable`` error, not a misleading generic ``no_accounts``.

    Before the fix the post-401 forced-refresh transient-claim branch released
    the lease and excluded the account but left ``last_retryable_stream_error``
    unset (unlike its pre-stream-open sibling), so after attempts were exhausted
    the loop fell through to the no-accounts path.
    """
    import json

    import app.modules.proxy.service as proxy_module

    for raw_account_id, email in (
        ("acc_post401_claim_a", "post401-claim-a@example.com"),
        ("acc_post401_claim_b", "post401-claim-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    # The proactive freshness check (force=False) succeeds so the stream opens;
    # the post-401 forced refresh (force=True) raises the transient claim timeout
    # for EVERY account, as if a foreign replica holds each account's claim.
    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, timeout_seconds
        if force:
            raise RefreshError(
                "refresh_claim_timeout",
                "refresh claim held by another replica",
                False,
                transport_error=True,
            )
        return account

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    streamed_account_ids: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, base_url, raise_for_status, kwargs
        streamed_account_ids.append(account_id)
        raise proxy_module.ProxyResponseError(
            401,
            {"error": {"code": "invalid_api_key", "message": "token invalidated"}},
        )
        yield ""  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json={"model": "gpt-5.4", "instructions": "hi", "input": [], "stream": True},
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = [json.loads(line[6:]) for line in lines if line.startswith("data: ") and line != "data: [DONE]"]
    failed_events = [event for event in events if event.get("type") == "response.failed"]
    assert failed_events, f"expected a response.failed event, got {events}"
    error_codes = {event["response"]["error"]["code"] for event in failed_events}
    # Transient contention surfaces as retryable upstream_unavailable, NOT no_accounts.
    assert error_codes == {"upstream_unavailable"}
    assert "no_accounts" not in error_codes
    # Every candidate account was attempted (each hit the 401 -> transient forced refresh).
    assert len(set(streamed_account_ids)) == 2

    # The transient claim contention is never recorded as a permanent failure.
    async with SessionLocal() as session:
        accounts = list((await session.execute(select(Account))).scalars().all())
        assert {account.status for account in accounts} == {AccountStatus.ACTIVE}


@pytest.mark.asyncio
async def test_proxy_pinned_stream_post_401_claim_timeout_stays_on_owner_and_reports_upstream_unavailable(
    async_client, monkeypatch
):
    """Route-level regression for the hard-pinned POST-401 forced-refresh sub-case.

    A stream turn pinned by ``previous_response_id`` sets ``preferred_account_id``
    (and ``require_preferred_account``). The proactive freshness check succeeds so
    the stream opens on the owner, but the upstream returns a 401 and the
    subsequent forced (``force=True``) refresh raises a transient refresh-claim
    timeout (claim held by another replica). Because ``preferred_account_id`` is
    set, the movable post-401 failover branch is correctly skipped -- a pinned
    request must not cross accounts.

    Before the fix the pinned post-401 sub-case fell through to the unconditional
    ``continue``: it reselected the same owner account on every attempt without
    releasing the already-acquired stream lease or recording a retryable error,
    leaking a stream-concurrency slot each iteration and finally surfacing a
    misleading ``no_accounts`` result once attempts were exhausted.

    The fix keeps the request on its owner account (no crossing) but releases the
    lease and surfaces a retryable ``upstream_unavailable`` promptly.
    """
    import json

    import app.modules.proxy.service as proxy_module

    for raw_account_id, email in (
        ("acc_pinned_post401_a", "pinned-post401-a@example.com"),
        ("acc_pinned_post401_b", "pinned-post401-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    async with SessionLocal() as session:
        rows = (await session.execute(select(Account.id, Account.chatgpt_account_id))).all()
    upstream_by_internal = {internal: upstream for internal, upstream in rows}
    account_ids = sorted(upstream_by_internal)
    assert len(account_ids) == 2
    owner_account_id = account_ids[0]
    alternate_account_id = account_ids[1]
    owner_upstream_id = upstream_by_internal[owner_account_id]
    alternate_upstream_id = upstream_by_internal[alternate_account_id]

    # Pin the turn to the owner account: ``previous_response_id`` resolves to the
    # owner, setting preferred_account_id and require_preferred_account.
    async def fake_owner(self, *, previous_response_id, api_key, session_id=None, surface):
        del self, previous_response_id, api_key, session_id, surface
        return owner_account_id

    monkeypatch.setattr(proxy_module.ProxyService, "_resolve_websocket_previous_response_owner", fake_owner)

    # The proactive freshness check (force=False) succeeds so the stream opens;
    # the post-401 forced refresh (force=True) raises the transient claim timeout,
    # as if a foreign replica holds the pinned owner's claim. No other account may
    # ever be freshened because the pinned request must not cross.
    freshened_force_flags: list[tuple[str, bool]] = []

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, timeout_seconds
        freshened_force_flags.append((account.id, force))
        if force:
            raise RefreshError(
                "refresh_claim_timeout",
                "refresh claim held by another replica",
                False,
                transport_error=True,
            )
        return account

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    released_lease_account_ids: list[str] = []
    original_release = proxy_module.LoadBalancer.release_account_lease

    async def spy_release(self, lease):
        if lease is not None:
            released_lease_account_ids.append(lease.account_id)
        return await original_release(self, lease)

    monkeypatch.setattr(proxy_module.LoadBalancer, "release_account_lease", spy_release)

    streamed_account_ids: list[str] = []

    async def fake_stream(payload, headers, access_token, account_id, base_url=None, raise_for_status=False, **kwargs):
        del payload, headers, access_token, base_url, raise_for_status, kwargs
        streamed_account_ids.append(account_id)
        raise proxy_module.ProxyResponseError(
            401,
            {"error": {"code": "invalid_api_key", "message": "token invalidated"}},
        )
        yield ""  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(proxy_module, "core_stream_responses", fake_stream)

    async with async_client.stream(
        "POST",
        "/backend-api/codex/responses",
        json={
            "model": "gpt-5.4",
            "instructions": "hi",
            "input": [],
            "stream": True,
            "previous_response_id": "resp_pinned_owner",
        },
    ) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]

    events = [json.loads(line[6:]) for line in lines if line.startswith("data: ") and line != "data: [DONE]"]
    failed_events = [event for event in events if event.get("type") == "response.failed"]
    assert failed_events, f"expected a response.failed event, got {events}"
    error_codes = {event["response"]["error"]["code"] for event in failed_events}
    # Pinned transient contention surfaces as retryable upstream_unavailable, NOT
    # a misleading no_accounts / previous_response_owner_unavailable exhaustion.
    assert error_codes == {"upstream_unavailable"}
    assert "no_accounts" not in error_codes
    assert "previous_response_owner_unavailable" not in error_codes
    # The stream only ever opened on the owner (it hit a 401), and the request
    # never crossed to the alternate account for a stream or a forced refresh.
    assert streamed_account_ids == [owner_upstream_id]
    assert alternate_upstream_id not in streamed_account_ids
    assert all(account_id == owner_account_id for account_id, _ in freshened_force_flags)
    assert alternate_account_id not in {account_id for account_id, _ in freshened_force_flags}
    # The transient claim timeout came from the forced (post-401) refresh.
    assert (owner_account_id, True) in freshened_force_flags
    # The pinned owner's already-acquired stream lease was released (not leaked).
    assert owner_account_id in released_lease_account_ids

    # The transient claim contention is never recorded as a permanent failure.
    async with SessionLocal() as session:
        accounts = list((await session.execute(select(Account))).scalars().all())
        assert {account.status for account in accounts} == {AccountStatus.ACTIVE}


@pytest.mark.asyncio
async def test_compact_preflight_claim_timeout_fails_over_and_releases_lease(async_client, monkeypatch):
    """Route-level regression for the compact freshness-check preflight.

    A transient refresh-claim timeout raised by the compact-responses
    ``_ensure_fresh_with_budget`` preflight (as if a foreign replica holds the
    account's refresh claim) must release the selected account's
    ``response_create`` lease, exclude the account, and fail over to another
    account. Before the fix the compact preflight only translated
    ``aiohttp.ClientError``/``asyncio.TimeoutError``, so the non-permanent
    ``RefreshError`` escaped unhandled and the healthy request errored out with
    an unhandled server error instead of failing over.
    """
    import json

    import app.modules.proxy.service as proxy_module
    from app.core.openai.models import OpenAIResponsePayload

    for raw_account_id, email in (
        ("acc_compact_claim_a", "compact-claim-a@example.com"),
        ("acc_compact_claim_b", "compact-claim-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    # Whichever account the compact loop freshens first fails with a transient
    # refresh-claim timeout; the other account freshens cleanly.
    first_seen: dict[str, str | None] = {"internal_id": None}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        if first_seen["internal_id"] is None:
            first_seen["internal_id"] = account.id
        if account.id == first_seen["internal_id"]:
            raise RefreshError(
                "transport_error",
                "refresh claim held by another replica",
                False,
                transport_error=True,
            )
        return account

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    released_lease_account_ids: list[str] = []
    original_release = proxy_module.LoadBalancer.release_account_lease

    async def spy_release(self, lease):
        if lease is not None:
            released_lease_account_ids.append(lease.account_id)
        return await original_release(self, lease)

    monkeypatch.setattr(proxy_module.LoadBalancer, "release_account_lease", spy_release)

    served_account_ids: list[str] = []
    released_before_compact: list[str] = []

    async def fake_compact(payload, headers, access_token, account_id):
        del payload, headers, access_token
        if not served_account_ids:
            released_before_compact.extend(released_lease_account_ids)
        served_account_ids.append(account_id)
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)

    assert response.status_code == 200
    assert response.json()["output"] == []

    failed_internal_id = first_seen["internal_id"]
    assert failed_internal_id is not None

    async with SessionLocal() as session:
        accounts = {account.id: account for account in (await session.execute(select(Account))).scalars().all()}
        assert {account.status for account in accounts.values()} == {AccountStatus.ACTIVE}
        failed_chatgpt_id = accounts[failed_internal_id].chatgpt_account_id

    # The failed account never served the compact request; a different account did.
    assert served_account_ids
    assert failed_chatgpt_id not in served_account_ids
    # Its response_create lease was released before the failover compact call.
    assert failed_internal_id in released_before_compact


@pytest.mark.asyncio
async def test_compact_preflight_claim_timeout_does_not_penalize_account_health(async_client, monkeypatch):
    """The compact freshness-check transient-claim failover MUST NOT record an
    account-health penalty for peer-claim contention.

    The account's credentials are healthy — only its refresh claim is held by
    another replica — so, like the streaming and WebSocket paths, the compact
    account-attempt loop must merely release + exclude the account and never call
    ``record_error``. Before the fix the branch routed the transient claim
    timeout through ``_handle_stream_error(..., "upstream_unavailable")``, which
    treats ``upstream_unavailable`` as a transient account error and calls
    ``record_error``, pushing an otherwise-healthy account into backoff for
    normal cross-replica claim contention.
    """
    import json

    import app.modules.proxy.service as proxy_module
    from app.core.openai.models import OpenAIResponsePayload

    for raw_account_id, email in (
        ("acc_compact_nopenalty_a", "compact-nopenalty-a@example.com"),
        ("acc_compact_nopenalty_b", "compact-nopenalty-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    first_seen: dict[str, str | None] = {"internal_id": None}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, force, timeout_seconds
        if first_seen["internal_id"] is None:
            first_seen["internal_id"] = account.id
        if account.id == first_seen["internal_id"]:
            raise RefreshError(
                "refresh_claim_timeout",
                "refresh claim held by another replica",
                False,
                transport_error=True,
            )
        return account

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    recorded_error_account_ids: list[str] = []
    original_record_error = proxy_module.LoadBalancer.record_error

    async def spy_record_error(self, account):
        recorded_error_account_ids.append(account.id)
        return await original_record_error(self, account)

    monkeypatch.setattr(proxy_module.LoadBalancer, "record_error", spy_record_error)

    served_account_ids: list[str] = []

    async def fake_compact(payload, headers, access_token, account_id):
        del payload, headers, access_token
        served_account_ids.append(account_id)
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)

    assert response.status_code == 200
    assert response.json()["output"] == []

    failed_internal_id = first_seen["internal_id"]
    assert failed_internal_id is not None
    # A different, healthy account served the request.
    assert served_account_ids
    # The account that hit peer-claim contention was NOT penalized, and in fact
    # no account was penalized at all for this healthy fallback.
    assert failed_internal_id not in recorded_error_account_ids
    assert recorded_error_account_ids == []

    async with SessionLocal() as session:
        accounts = {account.id: account for account in (await session.execute(select(Account))).scalars().all()}
        assert {account.status for account in accounts.values()} == {AccountStatus.ACTIVE}


@pytest.mark.asyncio
async def test_compact_post_401_forced_refresh_claim_timeout_fails_over(async_client, monkeypatch):
    """Route-level regression for the compact post-401 forced-refresh path.

    A compact request whose first-selected account returns an upstream 401 must,
    when the subsequent forced (``force=True``) refresh raises a transient
    refresh-claim timeout (claim held by another replica), fail over to a healthy
    account instead of re-raising the misleading original 401. Before the fix the
    compact post-401 branch handled only permanent RefreshErrors and re-raised the
    401 for a transient claim timeout, so the client saw ``invalid_api_key`` for
    what is really transient cross-replica contention.
    """
    import json

    import app.modules.proxy.service as proxy_module
    from app.core.openai.models import OpenAIResponsePayload

    for raw_account_id, email in (
        ("acc_compact_p401_a", "compact-p401-a@example.com"),
        ("acc_compact_p401_b", "compact-p401-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    # The proactive freshness check (force=False) succeeds; the post-401 forced
    # refresh (force=True) raises the transient claim timeout for the first
    # account only, the other account freshens cleanly.
    first_seen: dict[str, str | None] = {"internal_id": None}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, timeout_seconds
        if not force:
            return account
        if first_seen["internal_id"] is None:
            first_seen["internal_id"] = account.id
        if account.id == first_seen["internal_id"]:
            raise RefreshError(
                "refresh_claim_timeout",
                "refresh claim held by another replica",
                False,
                transport_error=True,
            )
        return account

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    served_account_ids: list[str] = []
    raised_401_for: set[str] = set()

    async def fake_compact(payload, headers, access_token, account_id):
        del payload, headers, access_token
        # Each account returns a single upstream 401 the first time it is called,
        # forcing the post-401 refresh; after a successful forced refresh it
        # would serve, but the failed account's forced refresh never succeeds.
        if account_id not in raised_401_for:
            raised_401_for.add(account_id)
            raise proxy_module.ProxyResponseError(
                401,
                {"error": {"code": "invalid_api_key", "message": "token invalidated"}},
            )
        served_account_ids.append(account_id)
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)

    assert response.status_code == 200
    assert response.json()["output"] == []
    # A different account served the compact request after the transient failover.
    assert served_account_ids

    async with SessionLocal() as session:
        accounts = list((await session.execute(select(Account))).scalars().all())
        assert {account.status for account in accounts} == {AccountStatus.ACTIVE}


@pytest.mark.asyncio
async def test_compact_post_401_forced_refresh_claim_timeout_does_not_penalize_account_health(
    async_client, monkeypatch
):
    """The compact post-401 forced-refresh transient-claim failover MUST NOT
    record an account-health penalty for peer-claim contention.

    When a compact account returns an upstream 401 and the subsequent forced
    (``force=True``) refresh raises a transient ``refresh_claim_timeout``
    ``RefreshError`` (claim held by another replica), the account's credentials
    are healthy — only its refresh claim is temporarily owned elsewhere. Before
    the fix this movable branch routed the transient claim timeout through
    ``_handle_stream_error(..., "upstream_unavailable")``, which treats
    ``upstream_unavailable`` as a transient account error and calls
    ``record_error``, pushing an otherwise-healthy account into backoff for
    normal cross-replica contention. Like the preflight and streaming/WebSocket
    paths, this branch must only release + exclude the account and never call
    ``record_error``.
    """
    import json

    import app.modules.proxy.service as proxy_module
    from app.core.openai.models import OpenAIResponsePayload

    for raw_account_id, email in (
        ("acc_compact_p401_nopenalty_a", "compact-p401-nopenalty-a@example.com"),
        ("acc_compact_p401_nopenalty_b", "compact-p401-nopenalty-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    # The proactive freshness check (force=False) succeeds; the post-401 forced
    # refresh (force=True) raises the transient claim timeout for the first
    # account only, the other account freshens cleanly.
    first_seen: dict[str, str | None] = {"internal_id": None}

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, timeout_seconds
        if not force:
            return account
        if first_seen["internal_id"] is None:
            first_seen["internal_id"] = account.id
        if account.id == first_seen["internal_id"]:
            raise RefreshError(
                "refresh_claim_timeout",
                "refresh claim held by another replica",
                False,
                transport_error=True,
            )
        return account

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    recorded_error_account_ids: list[str] = []
    original_record_error = proxy_module.LoadBalancer.record_error

    async def spy_record_error(self, account):
        recorded_error_account_ids.append(account.id)
        return await original_record_error(self, account)

    monkeypatch.setattr(proxy_module.LoadBalancer, "record_error", spy_record_error)

    served_account_ids: list[str] = []
    raised_401_for: set[str] = set()

    async def fake_compact(payload, headers, access_token, account_id):
        del payload, headers, access_token
        if account_id not in raised_401_for:
            raised_401_for.add(account_id)
            raise proxy_module.ProxyResponseError(
                401,
                {"error": {"code": "invalid_api_key", "message": "token invalidated"}},
            )
        served_account_ids.append(account_id)
        return OpenAIResponsePayload.model_validate({"output": []})

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)

    assert response.status_code == 200
    assert response.json()["output"] == []

    failed_internal_id = first_seen["internal_id"]
    assert failed_internal_id is not None
    # A different, healthy account served the request after failover.
    assert served_account_ids
    # The account that hit peer-claim contention on the forced refresh was NOT
    # penalized, and no account was penalized at all for this healthy fallback.
    assert failed_internal_id not in recorded_error_account_ids
    assert recorded_error_account_ids == []

    async with SessionLocal() as session:
        accounts = list((await session.execute(select(Account))).scalars().all())
        assert {account.status for account in accounts} == {AccountStatus.ACTIVE}


@pytest.mark.asyncio
async def test_compact_post_401_forced_refresh_claim_timeout_exhaustion_reports_upstream_unavailable(
    async_client, monkeypatch
):
    """Route-level regression: when EVERY compact candidate account returns a 401
    and its post-401 forced refresh raises the transient claim timeout, exhaustion
    must surface a retryable ``upstream_unavailable`` (502) error, not the
    misleading original 401.
    """
    import json

    import app.modules.proxy.service as proxy_module

    for raw_account_id, email in (
        ("acc_compact_p401x_a", "compact-p401x-a@example.com"),
        ("acc_compact_p401x_b", "compact-p401x-b@example.com"),
    ):
        auth_json = _make_auth_json(raw_account_id, email)
        files = {"auth_json": ("auth.json", json.dumps(auth_json), "application/json")}
        response = await async_client.post("/api/accounts/import", files=files)
        assert response.status_code == 200

    async def fake_ensure_fresh(self, account, *, force=False, timeout_seconds=None):
        del self, timeout_seconds
        if force:
            raise RefreshError(
                "refresh_claim_timeout",
                "refresh claim held by another replica",
                False,
                transport_error=True,
            )
        return account

    monkeypatch.setattr(proxy_module.ProxyService, "_ensure_fresh_with_budget", fake_ensure_fresh)

    served_account_ids: list[str] = []

    async def fake_compact(payload, headers, access_token, account_id):
        del payload, headers, access_token
        served_account_ids.append(account_id)
        raise proxy_module.ProxyResponseError(
            401,
            {"error": {"code": "invalid_api_key", "message": "token invalidated"}},
        )

    monkeypatch.setattr(proxy_module, "core_compact_responses", fake_compact)

    payload = {"model": "gpt-5.1", "instructions": "hi", "input": []}
    response = await async_client.post("/backend-api/codex/responses/compact", json=payload)

    # Transient contention surfaces as retryable upstream_unavailable (502), NOT the 401.
    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "upstream_unavailable"
    assert body["error"]["code"] != "invalid_api_key"

    async with SessionLocal() as session:
        accounts = list((await session.execute(select(Account))).scalars().all())
        assert {account.status for account in accounts} == {AccountStatus.ACTIVE}


@pytest.mark.asyncio
async def test_claim_coordinator_win_lose_release_semantics(db_setup):
    account_id = "acc_claim_semantics"
    await _create_account(account_id)
    coordinator_a = RefreshClaimCoordinator(claimant_id="replica-a")
    coordinator_b = RefreshClaimCoordinator(claimant_id="replica-b")
    owner = "fingerprint-1"

    assert await coordinator_a.try_acquire(account_id, ttl_seconds=30, owner=owner) is True
    # Re-entrant for the same claimant AND owner, exclusive against others.
    assert await coordinator_a.try_acquire(account_id, ttl_seconds=30, owner=owner) is True
    assert await coordinator_b.try_acquire(account_id, ttl_seconds=30, owner=owner) is False

    # A foreign release is a no-op; the owner's release frees the claim.
    await coordinator_b.release(account_id, owner=owner)
    assert await coordinator_b.try_acquire(account_id, ttl_seconds=30, owner=owner) is False
    await coordinator_a.release(account_id, owner=owner)
    assert await coordinator_b.try_acquire(account_id, ttl_seconds=30, owner=owner) is True
    snapshot = await coordinator_b.current_claim(account_id)
    assert snapshot is not None
    assert snapshot.claimed_by.startswith("replica-b")
    await coordinator_b.release(account_id, owner=owner)


@pytest.mark.asyncio
async def test_claim_owner_is_per_refresh_not_process_wide(db_setup):
    """Regression: two refreshes for one account in ONE process with different
    token fingerprints must contend for the claim, not piggyback. Before the
    per-refresh owner fix the claim was keyed process-wide (account only), so
    the second owner re-entered the first owner's live claim and either
    release() deleted the other's claim, letting a third replica in mid-exchange."""
    account_id = "acc_claim_per_owner"
    await _create_account(account_id)
    # One process => one claimant identity, two distinct in-flight refreshes.
    coordinator = RefreshClaimCoordinator(claimant_id="replica-a")
    owner_one = "fingerprint-old"
    owner_two = "fingerprint-reauth"

    assert await coordinator.try_acquire(account_id, ttl_seconds=30, owner=owner_one) is True
    # A different-fingerprint refresh in the same process must NOT re-enter the
    # live claim; it contends and loses until the first owner releases/expires.
    assert await coordinator.try_acquire(account_id, ttl_seconds=30, owner=owner_two) is False

    # Releasing the second owner is a no-op: it must not delete owner_one's claim.
    await coordinator.release(account_id, owner=owner_two)
    assert await coordinator.try_acquire(account_id, ttl_seconds=30, owner=owner_two) is False

    # Only the holding owner's release frees the claim.
    await coordinator.release(account_id, owner=owner_one)
    assert await coordinator.try_acquire(account_id, ttl_seconds=30, owner=owner_two) is True
    await coordinator.release(account_id, owner=owner_two)

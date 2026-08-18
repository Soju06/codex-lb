"""Background (chunked) account deletion: drain, fold interleave, restart,
idempotency, and supersede semantics for both delete_history variants."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import timedelta
from typing import cast

import pytest
from sqlalchemy import Table, func, select, text, update
from sqlalchemy.sql import Select

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import (
    Account,
    AccountStatus,
    AccountUsageRollup,
    RequestDemandQuarterRollup,
    RequestLog,
    RequestUsageHourlyRollup,
    StickySession,
    StickySessionKind,
    UsageHistory,
)
from app.db.session import SessionLocal, get_background_session, sqlite_writer_section
from app.modules.accounts.deletion import _request_logs_chunk, run_account_deletion_pass
from app.modules.accounts.repository import ACCOUNT_PENDING_DELETION_REASON, AccountsRepository
from app.modules.accounts.usage_rollup import run_fold_pass
from app.modules.accounts.usage_time_rollup import run_hourly_fold_pass, to_dimension
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration

_ORPHAN_DIMENSION = to_dimension(None)


@pytest.fixture(autouse=True)
def _no_background_wake(monkeypatch):
    """Keep the drain under explicit test control.

    The suite's stand-in leader election runs scheduler bodies inline, so the
    delete API's worker wake would drain accounts concurrently with (and race)
    the passes these tests drive step by step. The scheduler's own tick (one
    pass at startup plus every interval) is neutralized as well: a tick firing
    between a DELETE and the assertions would drain the account these tests
    expect to still be marked.
    """
    monkeypatch.setattr("app.modules.accounts.service.request_account_deletion_run", lambda: None)

    async def _no_tick(self) -> None:
        return None

    monkeypatch.setattr("app.modules.accounts.deletion.AccountDeletionScheduler._run_once", _no_tick)


def _make_account(account_id: str, email: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email,
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


async def _add_log(logs_repo: RequestLogsRepository, *, account_id: str, request_id: str, requested_at) -> None:
    await logs_repo.add_log(
        account_id=account_id,
        request_id=request_id,
        model="gpt-5.1-codex",
        input_tokens=100,
        output_tokens=50,
        latency_ms=100,
        status="success",
        error_code=None,
        requested_at=requested_at,
        cost_usd=0.01,
    )


async def _seed_account(account_id: str, *, log_count: int, usage_count: int = 0, requested_at=None) -> None:
    requested_at = requested_at or (utcnow() - timedelta(days=2))
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        logs_repo = RequestLogsRepository(session)
        usage_repo = UsageRepository(session)
        await accounts_repo.upsert(_make_account(account_id, f"{account_id}@example.com"))
        for index in range(log_count):
            await _add_log(
                logs_repo,
                account_id=account_id,
                request_id=f"req_{account_id}_{index}",
                requested_at=requested_at,
            )
        for index in range(usage_count):
            await usage_repo.add_entry(account_id, float(index), window="primary")


async def _account_row(account_id: str) -> Account | None:
    async with SessionLocal() as session:
        return await session.get(Account, account_id)


async def _attached_log_count(account_id: str) -> int:
    async with SessionLocal() as session:
        return (
            await session.execute(select(func.count(RequestLog.id)).where(RequestLog.account_id == account_id))
        ).scalar_one()


async def _log_rows(prefix: str) -> list[RequestLog]:
    async with SessionLocal() as session:
        return list(
            (await session.execute(select(RequestLog).where(RequestLog.request_id.like(f"req_{prefix}%"))))
            .scalars()
            .all()
        )


async def _hourly_rows_for_dimension(dimension: str) -> list[RequestUsageHourlyRollup]:
    async with SessionLocal() as session:
        return list(
            (
                await session.execute(
                    select(RequestUsageHourlyRollup).where(RequestUsageHourlyRollup.account_id == dimension)
                )
            )
            .scalars()
            .all()
        )


async def _demand_rows_for_dimension(dimension: str) -> list[RequestDemandQuarterRollup]:
    async with SessionLocal() as session:
        return list(
            (
                await session.execute(
                    select(RequestDemandQuarterRollup).where(RequestDemandQuarterRollup.account_id == dimension)
                )
            )
            .scalars()
            .all()
        )


async def _lifetime_rollup(account_id: str) -> AccountUsageRollup | None:
    async with SessionLocal() as session:
        return await session.get(AccountUsageRollup, account_id)


async def _run_one_detach_chunk(account_id: str, *, batch_size: int, delete_history: bool = False) -> int:
    async with get_background_session() as session:
        async with sqlite_writer_section():
            affected = await _request_logs_chunk(
                session, account_id, delete_history=delete_history, batch_size=batch_size
            )
            await session.commit()
    return affected


@pytest.mark.asyncio
async def test_delete_api_marks_and_hides_immediately(async_client, db_setup):
    await _seed_account("acc_bg_mark", log_count=2, usage_count=2)

    delete = await async_client.delete("/api/accounts/acc_bg_mark")
    assert delete.status_code == 200
    assert delete.json()["status"] == "deleted"

    # Hidden from the listing immediately, before any background work ran.
    accounts = await async_client.get("/api/accounts")
    assert accounts.status_code == 200
    assert all(entry["accountId"] != "acc_bg_mark" for entry in accounts.json()["accounts"])

    # The row itself survives, terminal and marked, until the worker drains it.
    row = await _account_row("acc_bg_mark")
    assert row is not None
    assert row.status is AccountStatus.DEACTIVATED
    assert row.deactivation_reason == ACCOUNT_PENDING_DELETION_REASON
    assert row.delete_requested_at is not None
    assert await _attached_log_count("acc_bg_mark") == 2

    # Repeat request is idempotent and does not escalate the frozen variant.
    repeat = await async_client.delete("/api/accounts/acc_bg_mark?delete_history=true")
    assert repeat.status_code == 200
    row = await _account_row("acc_bg_mark")
    assert row is not None
    assert row.delete_history_requested is False

    # A marked account is gone from the operator's perspective: reactivation
    # reports not-found instead of racing the deletion worker.
    reactivate = await async_client.post("/api/accounts/acc_bg_mark/reactivate")
    assert reactivate.status_code == 404

    # Credential exports must not keep serving decrypted tokens during the
    # drain window: the synchronous delete 404'd here immediately.
    for export_path in ("export", "export/auth", "export/opencode-auth"):
        export = await async_client.post(f"/api/accounts/acc_bg_mark/{export_path}")
        assert export.status_code == 404, export_path

    # Every other ID-based account route treats the marked row as gone too —
    # the synchronous delete returned 404 on all of them once the row was
    # removed.
    base = "/api/accounts/acc_bg_mark"
    assert (await async_client.get(f"{base}/trends")).status_code == 404
    assert (await async_client.get(f"{base}/usage-reset-credits")).status_code == 404
    assert (await async_client.post(f"{base}/usage-reset-credits/consume")).status_code == 404
    assert (await async_client.post(f"{base}/probe")).status_code == 404
    assert (await async_client.post(f"{base}/pause")).status_code == 404
    assert (await async_client.patch(base, json={"securityWorkAuthorized": True})).status_code == 404
    assert (await async_client.put(f"{base}/alias", json={"alias": "ghost"})).status_code == 404
    assert (await async_client.put(f"{base}/limit-warmup", json={"enabled": True})).status_code == 404
    assert (await async_client.put(f"{base}/routing-policy", json={"routingPolicy": "preserve"})).status_code == 404


def _fake_id_token(payload: dict) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"header.{encoded}.signature"


@pytest.mark.asyncio
async def test_begin_delete_preserves_seat_identity_before_token_wipe(db_setup):
    """Legacy rows carry their seat identity only inside the id-token claims;
    targeted reauthentication (a supersede path) verifies the seat against
    chatgpt_user_id or those claims, so the wipe must backfill the non-secret
    identity first."""
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        account = _make_account("acc_bg_seat", "acc_bg_seat@example.com")
        assert account.chatgpt_user_id is None
        account.id_token_encrypted = encryptor.encrypt(_fake_id_token({"sub": "user-legacy-seat"}))
        await AccountsRepository(session).upsert(account)

    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_seat")

    row = await _account_row("acc_bg_seat")
    assert row is not None
    # The ciphertext is wiped (no usable credentials remain on the row)...
    assert encryptor.decrypt(row.id_token_encrypted) == ""
    # ...but the seat identity survives in the non-secret column.
    assert row.chatgpt_user_id == "user-legacy-seat"


@pytest.mark.asyncio
async def test_marked_account_wipes_tokens_and_rejects_stale_status_writes(db_setup):
    await _seed_account("acc_bg_fence", log_count=1)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_fence")

    # The surviving row must not carry usable credentials: readers that do
    # not know the marker (pre-upgrade replicas during a rolling deploy) can
    # only produce empty credentials from it.
    row = await _account_row("acc_bg_fence")
    assert row is not None
    encryptor = TokenEncryptor()
    assert encryptor.decrypt(row.access_token_encrypted) == ""
    assert encryptor.decrypt(row.refresh_token_encrypted) == ""
    assert encryptor.decrypt(row.id_token_encrypted) == ""

    # Stale in-flight settlements (e.g. a late 429 for a request selected
    # before the DELETE) must not replace the terminal state and make the
    # account selectable again mid-drain.
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        assert await repo.update_status("acc_bg_fence", AccountStatus.RATE_LIMITED, "rate_limited") is False
        assert (
            await repo.update_status_if_current(
                "acc_bg_fence",
                AccountStatus.RATE_LIMITED,
                "rate_limited",
                expected_status=AccountStatus.DEACTIVATED,
                expected_deactivation_reason=ACCOUNT_PENDING_DELETION_REASON,
            )
            is False
        )
    row = await _account_row("acc_bg_fence")
    assert row is not None
    assert row.status is AccountStatus.DEACTIVATED
    assert row.deactivation_reason == ACCOUNT_PENDING_DELETION_REASON
    assert row.delete_requested_at is not None


@pytest.mark.asyncio
async def test_chunked_soft_delete_drains_across_chunk_boundaries(db_setup):
    await _seed_account("acc_bg_soft", log_count=7, usage_count=5)
    async with SessionLocal() as session:
        session.add(
            StickySession(
                key="sticky_bg_soft",
                kind=StickySessionKind.CODEX_SESSION,
                account_id="acc_bg_soft",
            )
        )
        await session.commit()

    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_soft")

    outcomes = await run_account_deletion_pass(batch_size=3)
    assert outcomes == {"acc_bg_soft": "finalized"}

    assert await _account_row("acc_bg_soft") is None
    logs = await _log_rows("acc_bg_soft")
    assert len(logs) == 7
    assert all(row.account_id is None and row.deleted_at is not None for row in logs)
    async with SessionLocal() as session:
        usage_left = (
            await session.execute(select(func.count(UsageHistory.id)).where(UsageHistory.account_id == "acc_bg_soft"))
        ).scalar_one()
        sticky_left = (
            await session.execute(
                select(func.count(StickySession.key)).where(StickySession.account_id == "acc_bg_soft")
            )
        ).scalar_one()
    assert usage_left == 0
    assert sticky_left == 0
    assert await _lifetime_rollup("acc_bg_soft") is None

    # Idempotent: a second pass finds nothing to do.
    assert await run_account_deletion_pass(batch_size=3) == {}


@pytest.mark.asyncio
async def test_chunked_hard_delete_removes_history(db_setup):
    await _seed_account("acc_bg_hard", log_count=5, usage_count=2)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_hard", delete_history=True)

    outcomes = await run_account_deletion_pass(batch_size=2)
    assert outcomes == {"acc_bg_hard": "finalized"}

    assert await _account_row("acc_bg_hard") is None
    assert await _log_rows("acc_bg_hard") == []


@pytest.mark.asyncio
async def test_fold_interleaved_between_chunks_does_not_resurrect_soft(db_setup):
    """A fold slice committing between detach chunks re-attributes still-
    attached rows to the account; finalization's fold-locked mirrors must
    move ALL of it to the orphaned-deleted dimension."""
    now = utcnow()
    account_dimension = to_dimension("acc_bg_fold")
    # Group A (2 rows) old enough for the first fold; group B (2 rows) folded
    # only by the interleaved fold below.
    await _seed_account("acc_bg_fold", log_count=2, requested_at=now - timedelta(days=5))
    async with SessionLocal() as session:
        logs_repo = RequestLogsRepository(session)
        for index in range(2):
            await _add_log(
                logs_repo,
                account_id="acc_bg_fold",
                request_id=f"req_acc_bg_fold_b{index}",
                requested_at=now - timedelta(days=2),
            )

    # First fold covers only group A (target = now-3d - FOLD_LAG).
    await run_fold_pass(now=now - timedelta(days=3))
    await run_hourly_fold_pass(now=now - timedelta(days=3))
    assert await _hourly_rows_for_dimension(account_dimension) != []

    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_fold")

    # One chunk detaches the two oldest (group A) rows; group B stays attached.
    assert await _run_one_detach_chunk("acc_bg_fold", batch_size=2) == 2
    assert await _attached_log_count("acc_bg_fold") == 2

    # Interleaved folds aggregate group B while it is still attributed.
    await run_fold_pass(now=now)
    await run_hourly_fold_pass(now=now)
    assert await _lifetime_rollup("acc_bg_fold") is not None
    interleaved_hourly = await _hourly_rows_for_dimension(account_dimension)
    assert sum(row.request_count for row in interleaved_hourly if not row.is_deleted) >= 2

    # Resume and finish the deletion.
    outcomes = await run_account_deletion_pass(batch_size=2)
    assert outcomes == {"acc_bg_fold": "finalized"}

    # No folded row anywhere still carries the account dimension...
    assert await _hourly_rows_for_dimension(account_dimension) == []
    assert await _demand_rows_for_dimension(account_dimension) == []
    assert await _lifetime_rollup("acc_bg_fold") is None
    # ...and the orphaned-deleted dimension preserves the full folded history.
    orphan_hourly = await _hourly_rows_for_dimension(_ORPHAN_DIMENSION)
    assert sum(row.request_count for row in orphan_hourly if row.is_deleted) == 4
    logs = await _log_rows("acc_bg_fold")
    assert len(logs) == 4
    assert all(row.account_id is None and row.deleted_at is not None for row in logs)

    # Folds after finalization see only detached raw rows: nothing new may
    # appear under the account dimension.
    await run_hourly_fold_pass(now=now + timedelta(days=1))
    await run_fold_pass(now=now + timedelta(days=1))
    assert await _hourly_rows_for_dimension(account_dimension) == []
    assert await _lifetime_rollup("acc_bg_fold") is None


@pytest.mark.asyncio
async def test_fold_interleaved_between_chunks_does_not_resurrect_hard(db_setup):
    now = utcnow()
    account_dimension = to_dimension("acc_bg_fhard")
    await _seed_account("acc_bg_fhard", log_count=2, requested_at=now - timedelta(days=5))
    async with SessionLocal() as session:
        logs_repo = RequestLogsRepository(session)
        for index in range(2):
            await _add_log(
                logs_repo,
                account_id="acc_bg_fhard",
                request_id=f"req_acc_bg_fhard_b{index}",
                requested_at=now - timedelta(days=2),
            )
    await run_hourly_fold_pass(now=now - timedelta(days=3))

    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_fhard", delete_history=True)

    assert await _run_one_detach_chunk("acc_bg_fhard", batch_size=2, delete_history=True) == 2
    await run_hourly_fold_pass(now=now)

    outcomes = await run_account_deletion_pass(batch_size=2)
    assert outcomes == {"acc_bg_fhard": "finalized"}

    assert await _hourly_rows_for_dimension(account_dimension) == []
    assert await _demand_rows_for_dimension(account_dimension) == []
    assert await _log_rows("acc_bg_fhard") == []


@pytest.mark.asyncio
async def test_pass_round_robins_chunks_across_pending_accounts(db_setup, monkeypatch):
    """One account's long drain must not starve another: each round advances
    every pending account by at most one full chunk."""
    from app.modules.accounts import deletion

    await _seed_account("acc_bg_rr_a", log_count=3)
    await _seed_account("acc_bg_rr_b", log_count=3)
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        assert await repo.begin_delete("acc_bg_rr_a")
        assert await repo.begin_delete("acc_bg_rr_b")

    chunk_calls: list[str] = []
    original_chunk = deletion._request_logs_chunk

    async def spy_chunk(session, account_id, *, delete_history, batch_size):
        chunk_calls.append(account_id)
        return await original_chunk(session, account_id, delete_history=delete_history, batch_size=batch_size)

    monkeypatch.setattr(deletion, "_request_logs_chunk", spy_chunk)

    outcomes = await run_account_deletion_pass(batch_size=1)
    assert outcomes == {"acc_bg_rr_a": "finalized", "acc_bg_rr_b": "finalized"}
    # Full chunks alternate between the two accounts instead of draining one
    # account to completion first.
    assert chunk_calls[:6] == [
        "acc_bg_rr_a",
        "acc_bg_rr_b",
        "acc_bg_rr_a",
        "acc_bg_rr_b",
        "acc_bg_rr_a",
        "acc_bg_rr_b",
    ]
    assert await _account_row("acc_bg_rr_a") is None
    assert await _account_row("acc_bg_rr_b") is None


@pytest.mark.asyncio
async def test_pass_picks_up_account_marked_mid_pass(db_setup, monkeypatch):
    """A DELETE that lands while a pass is draining another account is picked
    up by the between-rounds re-scan, not deferred to the next tick."""
    from app.modules.accounts import deletion

    await _seed_account("acc_bg_mid_a", log_count=2)
    await _seed_account("acc_bg_mid_b", log_count=1)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_mid_a")

    original_advance = deletion._advance_account
    marked_second = False

    async def advance_and_mark(account_id, *, batch_size, drained=None):
        nonlocal marked_second
        if not marked_second:
            marked_second = True
            async with SessionLocal() as session:
                assert await AccountsRepository(session).begin_delete("acc_bg_mid_b")
        return await original_advance(account_id, batch_size=batch_size, drained=drained)

    monkeypatch.setattr(deletion, "_advance_account", advance_and_mark)

    outcomes = await run_account_deletion_pass(batch_size=1)
    assert outcomes == {"acc_bg_mid_a": "finalized", "acc_bg_mid_b": "finalized"}
    assert await _account_row("acc_bg_mid_a") is None
    assert await _account_row("acc_bg_mid_b") is None


@pytest.mark.asyncio
async def test_restart_resumes_partial_drain(db_setup):
    await _seed_account("acc_bg_resume", log_count=5, usage_count=3)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_resume")

    # Simulate a crash after one detach chunk: progress lives in the rows.
    assert await _run_one_detach_chunk("acc_bg_resume", batch_size=2) == 2
    assert await _attached_log_count("acc_bg_resume") == 3

    # A fresh pass (restarted leader) resumes from the database state.
    outcomes = await run_account_deletion_pass(batch_size=2)
    assert outcomes == {"acc_bg_resume": "finalized"}
    assert await _account_row("acc_bg_resume") is None
    logs = await _log_rows("acc_bg_resume")
    assert len(logs) == 5
    assert all(row.account_id is None for row in logs)


@pytest.mark.asyncio
async def test_straggler_row_settled_mid_drain_is_finalized(db_setup):
    """A stream that settles its request-log row after the drain chunks ran
    is caught by finalization's residual sweep."""
    await _seed_account("acc_bg_late", log_count=3)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_late")
    assert await _run_one_detach_chunk("acc_bg_late", batch_size=10) == 3

    async with SessionLocal() as session:
        await _add_log(
            RequestLogsRepository(session),
            account_id="acc_bg_late",
            request_id="req_acc_bg_late_straggler",
            requested_at=utcnow() - timedelta(hours=1),
        )

    outcomes = await run_account_deletion_pass(batch_size=10)
    assert outcomes == {"acc_bg_late": "finalized"}
    logs = await _log_rows("acc_bg_late")
    assert len(logs) == 4
    assert all(row.account_id is None and row.deleted_at is not None for row in logs)


@pytest.mark.asyncio
async def test_credential_replacement_supersedes_pending_deletion(db_setup):
    await _seed_account("acc_bg_super", log_count=4)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_super")
    assert await _run_one_detach_chunk("acc_bg_super", batch_size=2) == 2

    # Re-import lands on the same row via the slot-identity path and clears
    # the marker (credential replacement supersedes the deletion).
    async with SessionLocal() as session:
        replacement = _make_account("acc_bg_super", "acc_bg_super@example.com")
        saved = await AccountsRepository(session).upsert(replacement, merge_by_email=True)
        assert saved.id == "acc_bg_super"

    row = await _account_row("acc_bg_super")
    assert row is not None
    assert row.delete_requested_at is None
    assert row.status is AccountStatus.ACTIVE

    outcomes = await run_account_deletion_pass(batch_size=2)
    assert outcomes == {}
    assert await _account_row("acc_bg_super") is not None
    # Rows detached before the supersede stay detached; the rest survive.
    assert await _attached_log_count("acc_bg_super") == 2


async def _legacy_replace_credentials(account_id: str, encryptor: TokenEncryptor) -> None:
    """Mimic a credential replacement by a pre-upgrade replica: fresh
    ciphertext and status, but the marker columns its ORM does not know stay
    untouched."""
    async with SessionLocal() as session:
        await session.execute(
            update(Account)
            .where(Account.id == account_id)
            .values(
                access_token_encrypted=encryptor.encrypt("fresh-access"),
                refresh_token_encrypted=encryptor.encrypt("fresh-refresh"),
                id_token_encrypted=encryptor.encrypt("fresh-id"),
                status=AccountStatus.ACTIVE,
                deactivation_reason=None,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_legacy_replica_replacement_supersedes_mid_drain(db_setup):
    """A replacement handled by a pre-upgrade replica cannot clear the marker;
    fresh (non-wiped) ciphertext on a marked row must itself supersede."""
    encryptor = TokenEncryptor()
    await _seed_account("acc_bg_legacy", log_count=3)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_legacy")
    assert await _run_one_detach_chunk("acc_bg_legacy", batch_size=2) == 2

    await _legacy_replace_credentials("acc_bg_legacy", encryptor)

    outcomes = await run_account_deletion_pass(batch_size=2)
    assert outcomes == {"acc_bg_legacy": "superseded"}
    row = await _account_row("acc_bg_legacy")
    assert row is not None
    # The worker cleared the marker itself and preserved the fresh material.
    assert row.delete_requested_at is None
    assert encryptor.decrypt(row.refresh_token_encrypted) == "fresh-refresh"
    # Rows detached before the replacement stay detached; the rest survive.
    assert await _attached_log_count("acc_bg_legacy") == 1
    # The account is no longer rescanned on later passes.
    assert await run_account_deletion_pass(batch_size=2) == {}


@pytest.mark.asyncio
async def test_legacy_replacement_with_empty_refresh_token_supersedes(db_setup):
    """A legal replacement may carry an empty refresh token while providing
    fresh access/id material; a refresh-only wipe check would mistake it for
    the original wipe and finalize the freshly replaced account."""
    encryptor = TokenEncryptor()
    await _seed_account("acc_bg_legacy_er", log_count=1)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_legacy_er")
    assert await _run_one_detach_chunk("acc_bg_legacy_er", batch_size=10) == 1

    async with SessionLocal() as session:
        await session.execute(
            update(Account)
            .where(Account.id == "acc_bg_legacy_er")
            .values(
                access_token_encrypted=encryptor.encrypt("fresh-access"),
                refresh_token_encrypted=encryptor.encrypt(""),
                id_token_encrypted=encryptor.encrypt("fresh-id"),
                status=AccountStatus.ACTIVE,
                deactivation_reason=None,
            )
        )
        await session.commit()

    outcomes = await run_account_deletion_pass(batch_size=10)
    assert outcomes == {"acc_bg_legacy_er": "superseded"}
    row = await _account_row("acc_bg_legacy_er")
    assert row is not None
    assert row.delete_requested_at is None
    assert encryptor.decrypt(row.access_token_encrypted) == "fresh-access"


@pytest.mark.asyncio
async def test_legacy_replica_replacement_before_finalize_is_abandoned(db_setup):
    encryptor = TokenEncryptor()
    await _seed_account("acc_bg_legacy_fin", log_count=1)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_legacy_fin")
    assert await _run_one_detach_chunk("acc_bg_legacy_fin", batch_size=10) == 1

    await _legacy_replace_credentials("acc_bg_legacy_fin", encryptor)

    async with SessionLocal() as session:
        assert await AccountsRepository(session).delete("acc_bg_legacy_fin", only_pending=True) is False
    row = await _account_row("acc_bg_legacy_fin")
    assert row is not None
    assert row.delete_requested_at is None
    assert encryptor.decrypt(row.refresh_token_encrypted) == "fresh-refresh"


@pytest.mark.asyncio
async def test_repeat_delete_short_circuits_without_waiting_on_chunk_lock(db_setup):
    """A repeat DELETE must keep the millisecond contract even while a drain
    chunk transaction holds the account row lock."""
    import asyncio

    async with SessionLocal() as probe:
        if probe.get_bind().dialect.name != "postgresql":
            pytest.skip("row-lock wait behavior is PostgreSQL-specific")

    await _seed_account("acc_bg_repeat", log_count=1)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_repeat")

    async with SessionLocal() as locker:
        # Hold the same lock a drain chunk holds for its whole transaction.
        await locker.execute(select(Account.id).where(Account.id == "acc_bg_repeat").with_for_update(key_share=True))
        async with SessionLocal() as session:
            repeat = await asyncio.wait_for(AccountsRepository(session).begin_delete("acc_bg_repeat"), timeout=2.0)
        assert repeat is True
        await locker.rollback()


@pytest.mark.asyncio
async def test_chunk_self_heals_drift_from_unfenced_replicas(db_setup):
    """During a rolling deploy, pre-upgrade replicas' unfenced writers can
    replace the terminal status or recreate API-key assignments on a marked
    row; the next chunk transaction must re-fence both."""
    from app.db.models import ApiKey, ApiKeyAccountAssignment
    from app.modules.accounts import deletion

    await _seed_account("acc_bg_heal", log_count=2)
    async with SessionLocal() as session:
        session.add(
            ApiKey(
                id="key_bg_heal",
                name="heal-key",
                key_hash="hash_bg_heal",
                key_prefix="sk-heal",
                account_assignment_scope_enabled=True,
            )
        )
        await session.commit()
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_heal")

    # Old-replica drift: unfenced status write + unconditional assignment
    # insert (tokens stay wiped, so this is NOT a credential replacement).
    async with SessionLocal() as session:
        await session.execute(
            update(Account)
            .where(Account.id == "acc_bg_heal")
            .values(status=AccountStatus.RATE_LIMITED, deactivation_reason="rate_limited")
        )
        session.add(ApiKeyAccountAssignment(api_key_id="key_bg_heal", account_id="acc_bg_heal"))
        await session.commit()

    affected = await deletion._run_chunk(deletion._usage_history_chunk, "acc_bg_heal", batch_size=10)
    assert affected is not None

    row = await _account_row("acc_bg_heal")
    assert row is not None
    assert row.status is AccountStatus.DEACTIVATED
    assert row.deactivation_reason == ACCOUNT_PENDING_DELETION_REASON
    assert row.delete_requested_at is not None
    async with SessionLocal() as session:
        assigned = (
            await session.execute(
                select(func.count())
                .select_from(ApiKeyAccountAssignment)
                .where(ApiKeyAccountAssignment.account_id == "acc_bg_heal")
            )
        ).scalar_one()
    assert assigned == 0


@pytest.mark.asyncio
async def test_finalization_serializes_against_inflight_log_insert(db_setup):
    """PostgreSQL: an in-flight stream's request-log insert holds the FK KEY
    SHARE on the account row; finalization's FOR UPDATE row upgrade must wait
    for it, so the late row is swept instead of surviving as a live orphan
    via ON DELETE SET NULL."""
    import asyncio

    async with SessionLocal() as probe:
        if probe.get_bind().dialect.name != "postgresql":
            pytest.skip("FK KEY SHARE / FOR UPDATE interleaving is PostgreSQL-specific")

    await _seed_account("acc_bg_inflight", log_count=1)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_inflight")
    assert await _run_one_detach_chunk("acc_bg_inflight", batch_size=10) == 1

    async with SessionLocal() as inflight:
        # In-flight stream: the insert takes (and holds) FK KEY SHARE on the
        # account row until commit.
        inflight.add(
            RequestLog(
                account_id="acc_bg_inflight",
                request_id="req_acc_bg_inflight_late",
                requested_at=utcnow(),
                model="gpt-5.1-codex",
                status="success",
                input_tokens=1,
                output_tokens=1,
                cost_usd=0.0,
            )
        )
        await inflight.flush()

        pass_task = asyncio.create_task(run_account_deletion_pass(batch_size=10))
        # Finalization must block on the row upgrade while the insert is open.
        done, _ = await asyncio.wait({pass_task}, timeout=1.0)
        commit_first = not done
        await inflight.commit()
        outcomes = await pass_task

    assert commit_first, "finalization finished while an uncommitted FK insert held KEY SHARE"
    assert outcomes == {"acc_bg_inflight": "finalized"}
    assert await _account_row("acc_bg_inflight") is None
    logs = await _log_rows("acc_bg_inflight")
    assert len(logs) == 2
    # The late row was swept by the residual sweep, not orphaned live.
    assert all(row.account_id is None and row.deleted_at is not None for row in logs)


@pytest.mark.asyncio
async def test_assignment_insert_rechecks_marker_atomically(db_setup):
    """replace_account_assignments must skip marked accounts even when an
    earlier validation (different transaction) still believed they existed."""
    from app.db.models import ApiKey, ApiKeyAccountAssignment
    from app.modules.api_keys.repository import ApiKeysRepository

    await _seed_account("acc_bg_atomic", log_count=0)
    async with SessionLocal() as session:
        session.add(
            ApiKey(
                id="key_bg_atomic",
                name="atomic-recheck-key",
                key_hash="hash_bg_atomic",
                key_prefix="sk-atomic",
                account_assignment_scope_enabled=True,
            )
        )
        await session.commit()

    # DELETE lands after validation would have passed.
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_atomic")

    async with SessionLocal() as session:
        await ApiKeysRepository(session).replace_account_assignments("key_bg_atomic", ["acc_bg_atomic"])

    async with SessionLocal() as session:
        assigned = (
            await session.execute(
                select(func.count())
                .select_from(ApiKeyAccountAssignment)
                .where(ApiKeyAccountAssignment.api_key_id == "key_bg_atomic")
            )
        ).scalar_one()
    assert assigned == 0


@pytest.mark.asyncio
async def test_marked_account_cannot_be_assigned_to_api_key(async_client, db_setup):
    await _seed_account("acc_bg_assign", log_count=1)

    delete = await async_client.delete("/api/accounts/acc_bg_assign")
    assert delete.status_code == 200

    # A key update racing (or following) the DELETE must not recreate an
    # assignment that would re-surface the deleted account in key listings.
    create = await async_client.post("/api/api-keys/", json={"name": "post-delete-key"})
    assert create.status_code == 200
    key_id = create.json()["id"]
    update_resp = await async_client.patch(
        f"/api/api-keys/{key_id}",
        json={"assignedAccountIds": ["acc_bg_assign"]},
    )
    assert update_resp.status_code == 400
    assert update_resp.json()["error"]["code"] == "invalid_api_key_payload"


@pytest.mark.asyncio
async def test_supersede_after_partial_drain_preserves_folded_attribution(db_setup):
    """Rows drained before a supersede stay drained, and folded rollups keep
    attributing that traffic to the revived account — with no double count
    from later folds (drained below-watermark rows are never re-folded)."""
    now = utcnow()
    account_dimension = to_dimension("acc_bg_sfold")
    await _seed_account("acc_bg_sfold", log_count=2, requested_at=now - timedelta(days=5))

    # Fold the two rows under the account dimension first.
    await run_fold_pass(now=now - timedelta(days=3))
    await run_hourly_fold_pass(now=now - timedelta(days=3))
    folded_before = sum(row.request_count for row in await _hourly_rows_for_dimension(account_dimension))
    assert folded_before == 2

    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_sfold")
    # One chunk detaches both already-folded rows.
    assert await _run_one_detach_chunk("acc_bg_sfold", batch_size=10) == 2

    # Re-import supersedes before finalization ever runs.
    async with SessionLocal() as session:
        saved = await AccountsRepository(session).upsert(
            _make_account("acc_bg_sfold", "acc_bg_sfold@example.com"), merge_by_email=True
        )
        assert saved.id == "acc_bg_sfold"
    assert await run_account_deletion_pass(batch_size=10) == {}

    # Folded attribution is the permanent end state: unchanged by later
    # folds (no loss, no double count), while raw rows stay detached.
    await run_fold_pass(now=now)
    await run_hourly_fold_pass(now=now)
    folded_after = sum(row.request_count for row in await _hourly_rows_for_dimension(account_dimension))
    assert folded_after == folded_before
    assert await _lifetime_rollup("acc_bg_sfold") is not None
    logs = await _log_rows("acc_bg_sfold")
    assert len(logs) == 2
    assert all(row.account_id is None and row.deleted_at is not None for row in logs)


@pytest.mark.asyncio
async def test_supersede_between_drain_and_finalize_is_abandoned(db_setup):
    await _seed_account("acc_bg_race", log_count=2)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_race")
    assert await _run_one_detach_chunk("acc_bg_race", batch_size=10) == 2

    # Marker cleared right before finalization (replacement won the race).
    async with SessionLocal() as session:
        await session.execute(
            update(Account)
            .where(Account.id == "acc_bg_race")
            .values(delete_requested_at=None, delete_history_requested=False)
        )
        await session.commit()

    async with SessionLocal() as session:
        assert await AccountsRepository(session).delete("acc_bg_race", only_pending=True) is False
    assert await _account_row("acc_bg_race") is not None


def _batch_pinning_cases() -> tuple[tuple[Callable[[str, int], Select[tuple[int]]], Table, str], ...]:
    from app.db.models import AdditionalUsageHistory
    from app.modules.accounts import deletion

    return (
        (deletion._usage_history_batch, cast("Table", UsageHistory.__table__), "idx_usage_account_time"),
        (
            deletion._additional_usage_history_batch,
            cast("Table", AdditionalUsageHistory.__table__),
            "ix_additional_usage_distinct_labels",
        ),
        (deletion._request_logs_batch, cast("Table", RequestLog.__table__), "idx_logs_account_kind_deleted_latest"),
    )


def test_chunk_batch_statements_pin_account_leading_index_order():
    """The chunk batch shape is what keeps the planner off sequential scans.

    An ``account_id = :id LIMIT n`` subquery plans as a LIMIT-terminated Seq
    Scan on the production planner for exactly the large accounts the drain
    targets (equality folds account_id out of the sort pathkeys). The batch
    builders must keep (a) the range predicate pair (never plain equality)
    and (b) an ORDER BY that lists the target account-leading index's exact
    column order, so that index is the only sort-free plan.
    """
    from sqlalchemy.dialects import postgresql as postgresql_dialect

    for batch_fn, table, index_name in _batch_pinning_cases():
        index = next(idx for idx in table.indexes if idx.name == index_name)
        sql = str(batch_fn("acc_bg_pin", 50).compile(dialect=postgresql_dialect.dialect()))
        expected_order = ", ".join(f"{table.name}.{column.name}" for column in index.columns)
        assert f"ORDER BY {expected_order}" in sql, sql
        assert f"{table.name}.account_id >= " in sql, sql
        assert f"{table.name}.account_id <= " in sql, sql
        assert f"{table.name}.account_id = " not in sql, sql


@pytest.mark.asyncio
async def test_chunk_batch_query_plan_uses_account_leading_indexes_postgresql(db_setup):
    """The batch ORDER BY must be served by the pinned index, not a sort.

    Sequential/bitmap scans and (incremental) sorts are disabled so the
    planner has to surface an ordered index path for the batch shape; the
    only index that can provide the ORDER BY after the leading account_id
    range is the pinned account-leading index. A drained (or missing)
    account's probe must terminate on the same index instead of falling
    back to a heap scan.
    """
    await _seed_account("acc_bg_plan", log_count=8, usage_count=8)
    async with SessionLocal() as session:
        if session.get_bind().dialect.name != "postgresql":
            pytest.skip("PostgreSQL-only query plan test")

        from app.db.models import AdditionalUsageHistory

        session.add_all(
            AdditionalUsageHistory(
                account_id="acc_bg_plan",
                quota_key="codex_spark",
                limit_name="GPT-5.3-Codex-Spark",
                metered_feature="codex_bengalfox",
                window="primary",
                used_percent=float(index),
            )
            for index in range(8)
        )
        await session.commit()

        await session.execute(text("SET enable_seqscan = off"))
        await session.execute(text("SET enable_bitmapscan = off"))
        await session.execute(text("SET enable_sort = off"))
        await session.execute(text("SET enable_incremental_sort = off"))
        for batch_fn, _table, index_name in _batch_pinning_cases():
            for account_id in ("acc_bg_plan", "acc_bg_plan_drained_probe"):
                compiled = batch_fn(account_id, 5).compile(
                    dialect=session.get_bind().dialect, compile_kwargs={"literal_binds": True}
                )
                plan = (await session.execute(text(f"EXPLAIN (FORMAT JSON) {compiled}"))).scalar_one()
                plan_json = json.dumps(plan)
                assert index_name in plan_json, (account_id, plan_json)
                assert "Seq Scan" not in plan_json, (account_id, plan_json)
                assert "Sort Key" not in plan_json, (account_id, plan_json)


@pytest.mark.asyncio
async def test_pass_probes_drained_tables_once_per_pass(db_setup, monkeypatch):
    """A table observed empty for an account is not re-probed on later rounds
    of the same pass: each probe is a full account-row-locking transaction,
    and per-account statistics can go stale exactly during the churn window."""
    from app.modules.accounts import deletion

    await _seed_account("acc_bg_memo", log_count=3)
    async with SessionLocal() as session:
        assert await AccountsRepository(session).begin_delete("acc_bg_memo")

    calls = {"usage_history": 0, "additional_usage_history": 0, "request_logs": 0}
    for attr, key in (
        ("_usage_history_chunk", "usage_history"),
        ("_additional_usage_history_chunk", "additional_usage_history"),
        ("_request_logs_chunk", "request_logs"),
    ):
        original = getattr(deletion, attr)

        def _make_spy(original=original, key=key):
            async def spy(session, account_id, *, delete_history, batch_size):
                calls[key] += 1
                return await original(session, account_id, delete_history=delete_history, batch_size=batch_size)

            return spy

        monkeypatch.setattr(deletion, attr, _make_spy())

    outcomes = await run_account_deletion_pass(batch_size=1)
    assert outcomes == {"acc_bg_memo": "finalized"}
    # usage tables: exactly one (empty) probe in round 1, then skipped while
    # rounds 2-4 drain the logs; request_logs: three one-row chunks plus the
    # final empty probe that lets the pass finalize.
    assert calls == {"usage_history": 1, "additional_usage_history": 1, "request_logs": 4}
    assert await _account_row("acc_bg_memo") is None

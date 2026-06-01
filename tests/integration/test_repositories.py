from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import (
    Account,
    AccountLimitWarmup,
    AccountStatus,
    AdditionalUsageHistory,
    ApiKey,
    ApiKeyAccountAssignment,
    HttpBridgeSessionRecord,
    RequestLog,
    StickySession,
    StickySessionKind,
    UsageHistory,
)
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountIdentityConflictError, AccountsRepository
from app.modules.request_logs.repository import RequestLogsRepository
from app.modules.usage.repository import UsageRepository

pytestmark = pytest.mark.integration


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


@pytest.mark.asyncio
async def test_accounts_upsert_updates_existing_by_email(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_make_account("acc1", "dup@example.com"))

        updated = _make_account("acc2", "dup@example.com")
        updated.plan_type = "team"
        updated.status = AccountStatus.PAUSED
        updated.deactivation_reason = "reauth"
        await repo.upsert(updated)

        result = await session.execute(select(Account).where(Account.email == "dup@example.com"))
        stored = result.scalar_one()
        assert stored.id == "acc1"
        assert stored.plan_type == "team"
        assert stored.status == AccountStatus.PAUSED
        assert stored.deactivation_reason == "reauth"

        all_accounts = await session.execute(select(Account))
        assert len(list(all_accounts.scalars().all())) == 1


@pytest.mark.asyncio
async def test_accounts_upsert_with_merge_disabled_keeps_duplicate_identity(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        first = await repo.upsert(_make_account("acc_same", "dup@example.com"), merge_by_email=False)

        updated = _make_account("acc_same", "dup@example.com")
        updated.plan_type = "team"
        second = await repo.upsert(updated, merge_by_email=False)

        assert first.id == "acc_same"
        assert second.id != first.id
        assert second.id.startswith("acc_same__copy")

        result = await session.execute(select(Account).where(Account.email == "dup@example.com"))
        rows = list(result.scalars().all())
        assert len(rows) == 2
        row_ids = {row.id for row in rows}
        assert first.id in row_ids
        assert second.id in row_ids


@pytest.mark.asyncio
async def test_accounts_upsert_with_merge_enabled_raises_conflict_on_ambiguous_email(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_make_account("acc_same", "dup@example.com"), merge_by_email=False)
        await repo.upsert(_make_account("acc_same", "dup@example.com"), merge_by_email=False)

        incoming = _make_account("acc_new", "dup@example.com")
        with pytest.raises(AccountIdentityConflictError):
            await repo.upsert(incoming, merge_by_email=True)


@pytest.mark.asyncio
async def test_accounts_upsert_with_merge_enabled_serializes_concurrent_same_email(db_setup):
    email = "race@example.com"
    barrier = asyncio.Barrier(2)

    async def _worker(account_id: str, plan_type: str) -> str:
        async with SessionLocal() as session:
            repo = AccountsRepository(session)
            await barrier.wait()
            incoming = _make_account(account_id, email)
            incoming.plan_type = plan_type
            saved = await repo.upsert(incoming, merge_by_email=True)
            return saved.id

    first_id, second_id = await asyncio.gather(
        _worker("acc_race_a", "plus"),
        _worker("acc_race_b", "team"),
    )

    assert first_id in {"acc_race_a", "acc_race_b"}
    assert second_id in {"acc_race_a", "acc_race_b"}

    async with SessionLocal() as session:
        result = await session.execute(select(Account).where(Account.email == email))
        rows = list(result.scalars().all())
        assert len(rows) == 1
        assert rows[0].id in {"acc_race_a", "acc_race_b"}
        assert rows[0].plan_type in {"plus", "team"}


@pytest.mark.asyncio
async def test_accounts_upsert_with_merge_disabled_uses_identity_lock_on_postgresql(db_setup, monkeypatch):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        acquired_identity_locks: list[str] = []

        monkeypatch.setattr(repo, "_dialect_name", lambda: "postgresql")

        async def _record_identity_lock(account_id: str) -> None:
            acquired_identity_locks.append(account_id)

        async def _fail_merge_lock(_: str) -> None:
            raise AssertionError("merge lock should not be used when merge_by_email is disabled")

        monkeypatch.setattr(repo, "_acquire_postgresql_identity_lock", _record_identity_lock)
        monkeypatch.setattr(repo, "_acquire_postgresql_merge_lock", _fail_merge_lock)

        await repo.upsert(_make_account("acc_non_merge_lock", "non-merge-lock@example.com"), merge_by_email=False)

        assert acquired_identity_locks == ["acc_non_merge_lock"]


def _make_account_with_chatgpt_id(account_id: str, email: str, chatgpt_id: str) -> Account:
    account = _make_account(account_id, email)
    account.chatgpt_account_id = chatgpt_id
    return account


@pytest.mark.asyncio
async def test_accounts_upsert_merge_by_chatgpt_identity_reuses_deactivated_row(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        original = _make_account_with_chatgpt_id("acc_canonical", "reauth@example.com", "chatgpt_xyz")
        await repo.upsert(original, merge_by_email=False)

        await repo.update_status(
            "acc_canonical",
            AccountStatus.DEACTIVATED,
            "Refresh token was revoked - re-login required",
        )

        reauth = _make_account_with_chatgpt_id("acc_canonical", "reauth@example.com", "chatgpt_xyz")
        reauth.plan_type = "team"
        saved = await repo.upsert(reauth, merge_by_email=False, merge_by_chatgpt_identity=True)

        # Reauth must return the original deterministic id, with the new
        # plan and re-activated status, instead of an `__copyN` row.
        # merge_by_email=False simulates an operator with
        # `importWithoutOverwrite` enabled; identity-merge runs anyway
        # because reauth is governed by upstream identity, not by the
        # dashboard import setting.
        assert saved.id == "acc_canonical"
        assert saved.plan_type == "team"
        assert saved.status == AccountStatus.ACTIVE
        assert saved.deactivation_reason is None

        rows = list(
            (await session.execute(select(Account).where(Account.chatgpt_account_id == "chatgpt_xyz"))).scalars().all()
        )
        assert len(rows) == 1
        assert rows[0].id == "acc_canonical"


@pytest.mark.asyncio
async def test_accounts_upsert_merge_by_chatgpt_identity_picks_oldest_canonical(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        canonical = _make_account_with_chatgpt_id("acc_first", "shared@example.com", "chatgpt_dup")
        canonical.created_at = utcnow() - timedelta(days=30)
        await repo.upsert(canonical, merge_by_email=False)

        copy_row = _make_account_with_chatgpt_id("acc_first__copy2", "shared@example.com", "chatgpt_dup")
        copy_row.created_at = utcnow()
        await repo.upsert(copy_row, merge_by_email=False)

        reauth = _make_account_with_chatgpt_id("acc_first", "shared@example.com", "chatgpt_dup")
        reauth.plan_type = "team"
        saved = await repo.upsert(reauth, merge_by_email=False, merge_by_chatgpt_identity=True)

        # Reauth must land on the older canonical row, not the `__copy2`
        # row, so the long-term usage history stays attached.
        assert saved.id == "acc_first"
        assert saved.plan_type == "team"
        rows = list(
            (await session.execute(select(Account).where(Account.chatgpt_account_id == "chatgpt_dup"))).scalars().all()
        )
        assert len(rows) == 1
        assert rows[0].id == "acc_first"


@pytest.mark.asyncio
async def test_accounts_upsert_merge_by_chatgpt_identity_reconciles_duplicate_rows(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        canonical = _make_account_with_chatgpt_id("acc_merge_main", "merge@example.com", "chatgpt_merge")
        await repo.upsert(canonical, merge_by_email=False)

        duplicate = _make_account_with_chatgpt_id("acc_merge_main__copy2", "merge@example.com", "chatgpt_merge")
        await repo.upsert(duplicate, merge_by_email=False)

        duplicate_row = (
            await session.execute(select(Account).where(Account.id == "acc_merge_main__copy2"))
        ).scalar_one()

        api_key = ApiKey(
            id="api_merge_dupe",
            name="Merge Test",
            key_hash="merge-key-hash",
            key_prefix="mrg",
            apply_to_codex_model=False,
            account_assignment_scope_enabled=False,
        )
        session.add(api_key)
        session.add(ApiKeyAccountAssignment(api_key_id=api_key.id, account_id=duplicate_row.id))
        session.add(
            UsageHistory(
                account_id=duplicate_row.id,
                window="primary",
                used_percent=55.0,
                input_tokens=42,
                output_tokens=17,
                reset_at=1,
                window_minutes=300,
            )
        )
        session.add(
            AdditionalUsageHistory(
                account_id=duplicate_row.id,
                quota_key="gpt-5.1",
                limit_name="gpt-5.1",
                metered_feature="model",
                window="7d",
                used_percent=15.0,
            )
        )
        session.add(
            AccountLimitWarmup(
                account_id=duplicate_row.id,
                window="primary",
                reset_at=123,
                status="pending",
                model="gpt-5.1",
                attempted_at=utcnow(),
            )
        )
        session.add(
            StickySession(
                account_id=duplicate_row.id,
                key="sticky-dup",
                kind=StickySessionKind.STICKY_THREAD,
            )
        )
        session.add(
            HttpBridgeSessionRecord(
                account_id=duplicate_row.id,
                session_key_kind="http",
                session_key_value="turn:merge",
                session_key_hash="turn-merge-hash",
                api_key_scope="merge-scope",
            )
        )
        session.add(
            RequestLog(
                request_id="req_merge_dupe",
                account_id=duplicate_row.id,
                model="gpt-5",
                status="success",
                requested_at=utcnow(),
            )
        )
        await session.commit()

        reauth = _make_account_with_chatgpt_id("acc_merge_main", "merge@example.com", "chatgpt_merge")
        reauth.plan_type = "team"
        saved = await repo.upsert(reauth, merge_by_email=False, merge_by_chatgpt_identity=True)

        assert saved.id == "acc_merge_main"
        assert saved.plan_type == "team"

        rows = list(
            (await session.execute(select(Account).where(Account.chatgpt_account_id == "chatgpt_merge")))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].id == "acc_merge_main"

        account_histories = (
            (await session.execute(select(UsageHistory).where(UsageHistory.account_id == "acc_merge_main")))
            .scalars()
            .all()
        )
        assert len(account_histories) == 1
        assert account_histories[0].used_percent == 55.0

        additional_histories = (
            (
                await session.execute(
                    select(AdditionalUsageHistory).where(AdditionalUsageHistory.account_id == "acc_merge_main")
                )
            )
            .scalars()
            .all()
        )
        assert len(additional_histories) == 1
        assert additional_histories[0].quota_key == "gpt-5.1"

        account_warmups = (
            (await session.execute(select(AccountLimitWarmup).where(AccountLimitWarmup.account_id == "acc_merge_main")))
            .scalars()
            .all()
        )
        assert len(account_warmups) == 1
        assert account_warmups[0].status == "pending"

        sticky_sessions = (
            (await session.execute(select(StickySession).where(StickySession.account_id == "acc_merge_main")))
            .scalars()
            .all()
        )
        assert len(sticky_sessions) == 1
        assert sticky_sessions[0].key == "sticky-dup"

        bridge_sessions = (
            (
                await session.execute(
                    select(HttpBridgeSessionRecord).where(HttpBridgeSessionRecord.account_id == "acc_merge_main")
                )
            )
            .scalars()
            .all()
        )
        assert len(bridge_sessions) == 1
        assert bridge_sessions[0].session_key_value == "turn:merge"

        assignments = (
            (
                await session.execute(
                    select(ApiKeyAccountAssignment).where(ApiKeyAccountAssignment.account_id == "acc_merge_main")
                )
            )
            .scalars()
            .all()
        )
        assert len(assignments) == 1
        assert assignments[0].api_key_id == api_key.id

        duplicate_request_logs = (
            (await session.execute(select(RequestLog).where(RequestLog.account_id == "acc_merge_main__copy2")))
            .scalars()
            .all()
        )
        assert len(duplicate_request_logs) == 0


@pytest.mark.asyncio
async def test_accounts_upsert_merge_by_chatgpt_identity_creates_when_missing(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        incoming = _make_account_with_chatgpt_id("acc_new", "new@example.com", "chatgpt_fresh")
        saved = await repo.upsert(incoming, merge_by_email=False, merge_by_chatgpt_identity=True)

        assert saved.id == "acc_new"
        assert saved.chatgpt_account_id == "chatgpt_fresh"


@pytest.mark.asyncio
async def test_accounts_upsert_merge_by_chatgpt_identity_skips_without_upstream_id(db_setup):
    """Falls back to deterministic-id behavior when the incoming row has
    no ``chatgpt_account_id`` — e.g. legacy local accounts that were
    seeded before the field was populated.
    """

    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        first = _make_account("acc_no_id", "noid@example.com")
        await repo.upsert(first, merge_by_email=False, merge_by_chatgpt_identity=True)

        again = _make_account("acc_no_id", "noid@example.com")
        again.plan_type = "team"
        saved = await repo.upsert(again, merge_by_email=False, merge_by_chatgpt_identity=True)

        # No upstream id means identity-merge has nothing to key on, so
        # the standard side-by-side path runs and creates `__copy2`.
        assert saved.id.startswith("acc_no_id__copy")


@pytest.mark.asyncio
async def test_usage_repository_aggregate(db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        repo = UsageRepository(session)
        await accounts_repo.upsert(_make_account("acc1", "acc1@example.com"))
        await accounts_repo.upsert(_make_account("acc2", "acc2@example.com"))
        now = utcnow()
        await repo.add_entry("acc1", 10.0, recorded_at=now - timedelta(hours=1))
        await repo.add_entry("acc1", 30.0, recorded_at=now - timedelta(minutes=30))
        await repo.add_entry("acc2", 50.0, recorded_at=now - timedelta(minutes=10))

        rows = await repo.aggregate_since(now - timedelta(hours=5))
        row_map = {row.account_id: row for row in rows}
        assert row_map["acc1"].used_percent_avg == pytest.approx(20.0)
        assert row_map["acc2"].used_percent_avg == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_request_logs_repository_filters(db_setup):
    async with SessionLocal() as session:
        accounts_repo = AccountsRepository(session)
        repo = RequestLogsRepository(session)
        await accounts_repo.upsert(_make_account("acc1", "acc1@example.com"))
        await accounts_repo.upsert(_make_account("acc2", "acc2@example.com"))
        now = utcnow()
        await repo.add_log(
            account_id="acc1",
            request_id="req_repo_1",
            model="gpt-5.1",
            input_tokens=10,
            output_tokens=20,
            latency_ms=100,
            status="success",
            error_code=None,
            requested_at=now - timedelta(minutes=10),
        )
        await repo.add_log(
            account_id="acc2",
            request_id="req_repo_2",
            model="gpt-5.1",
            input_tokens=5,
            output_tokens=5,
            latency_ms=50,
            status="error",
            error_code="rate_limit_exceeded",
            requested_at=now - timedelta(minutes=5),
        )

        results, total = await repo.list_recent(limit=0, account_ids=["acc1"])
        assert len(results) == 1
        assert results[0].account_id == "acc1"
        assert total == 1

        results, total = await repo.list_recent(limit=0, include_success=False)
        assert len(results) == 1
        assert results[0].error_code == "rate_limit_exceeded"
        assert total == 1

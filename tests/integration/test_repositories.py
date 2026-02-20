from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
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

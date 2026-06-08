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
from app.modules.accounts.repository import (
    AccountIdentityConflictError,
    AccountsRepository,
    _slot_lock_key,
    _slot_lock_keys,
)
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
async def test_accounts_upsert_reauthorized_heals_deactivated_identity_even_when_merge_disabled(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        existing = _make_account("acc_reauth", "reauth@example.com")
        existing.status = AccountStatus.DEACTIVATED
        existing.deactivation_reason = "refresh_failed"
        existing.routing_policy = "preserve"
        await repo.upsert(existing, merge_by_email=False)

        incoming = _make_account("acc_reauth", "reauth@example.com")
        incoming.plan_type = "team"
        saved = await repo.upsert_reauthorized(incoming)

        assert saved.id == "acc_reauth"
        assert saved.status == AccountStatus.ACTIVE
        assert saved.deactivation_reason is None
        assert saved.plan_type == "team"
        assert saved.routing_policy == "preserve"

        result = await session.execute(select(Account).where(Account.email == "reauth@example.com"))
        rows = list(result.scalars().all())
        assert len(rows) == 1


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


def test_slot_lock_key_serializes_unknown_workspace_overwrite_by_email():
    first = _make_account("acc_unknown_a", "unknown-workspace@example.com")
    second = _make_account("acc_unknown_b", "unknown-workspace@example.com")

    assert _slot_lock_key(first, preserve_unknown_workspace_duplicates=False) == _slot_lock_key(
        second,
        preserve_unknown_workspace_duplicates=False,
    )
    assert _slot_lock_key(first, preserve_unknown_workspace_duplicates=True) != _slot_lock_key(
        second,
        preserve_unknown_workspace_duplicates=True,
    )


def test_slot_lock_key_serializes_email_workspace_slot_when_account_id_appears_later():
    email_only = _make_account("acc_generated", "late-id@example.com")
    email_only.workspace_id = "ws_late"
    with_account_id = _make_account("acc_raw", "late-id@example.com")
    with_account_id.chatgpt_account_id = "raw_account_id"
    with_account_id.workspace_id = "ws_late"

    assert set(_slot_lock_keys(email_only)) & set(_slot_lock_keys(with_account_id)) == {
        "slot-email:late-id@example.com:ws_late",
    }


def test_slot_lock_key_serializes_legacy_unknown_workspace_upgrade():
    legacy = _make_account("acc_legacy", "legacy-workspace@example.com")
    upgraded = _make_account("acc_upgraded", "legacy-workspace@example.com")
    upgraded.chatgpt_account_id = "raw_account_id"
    upgraded.workspace_id = "ws_late"

    assert set(_slot_lock_keys(legacy, preserve_unknown_workspace_duplicates=False)) & set(
        _slot_lock_keys(upgraded, preserve_unknown_workspace_duplicates=False)
    ) == {"slot-email-unknown:legacy-workspace@example.com"}


def test_slot_lock_key_serializes_account_workspace_slot_when_email_changes():
    old_email = _make_account("acc_generated_old", "old-email@example.com")
    old_email.chatgpt_account_id = "raw_account_id"
    old_email.workspace_id = "ws_late"
    new_email = _make_account("acc_generated_new", "new-email@example.com")
    new_email.chatgpt_account_id = "raw_account_id"
    new_email.workspace_id = "ws_late"

    assert set(_slot_lock_keys(old_email)) & set(_slot_lock_keys(new_email)) == {
        "slot:raw_account_id:ws_late",
    }


@pytest.mark.asyncio
async def test_account_slot_upgrades_single_legacy_unknown_workspace_row(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        legacy = await repo.upsert_account_slot(
            _make_account("acc_legacy_workspace", "legacy-workspace-row@example.com"),
            preserve_unknown_workspace_duplicates=False,
        )
        await RequestLogsRepository(session).add_log(
            account_id=legacy.id,
            request_id="req_legacy_workspace",
            model="gpt-5.1",
            input_tokens=7,
            output_tokens=11,
            latency_ms=50,
            status="ok",
            error_code=None,
        )

        upgraded = _make_account("acc_raw_workspace", "legacy-workspace-row@example.com")
        upgraded.chatgpt_account_id = "raw_workspace_account"
        upgraded.workspace_id = "ws_legacy_team"
        upgraded.workspace_label = "Legacy Team"
        upgraded.plan_type = "team"

        stored = await repo.upsert_account_slot(upgraded, preserve_unknown_workspace_duplicates=False)

        assert stored.id == legacy.id
        assert stored.chatgpt_account_id == "raw_workspace_account"
        assert stored.workspace_id == "ws_legacy_team"
        assert stored.workspace_label == "Legacy Team"
        assert stored.plan_type == "team"

        accounts = list((await session.execute(select(Account))).scalars().all())
        assert [account.id for account in accounts] == [legacy.id]

        usage = await repo.list_request_usage_summary_by_account([stored.id])
        assert usage[stored.id].request_count == 1


@pytest.mark.asyncio
async def test_workspace_slot_taken_ignores_same_email_workspace_when_chatgpt_identity_differs(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        first = _make_account("acc_first_slot", "slot-taken@example.com")
        first.chatgpt_account_id = "raw_first_slot"
        first.workspace_id = "ws_shared_slot"
        await repo.upsert_account_slot(first, preserve_unknown_workspace_duplicates=False)

        assert (
            await repo.workspace_slot_taken(
                account_id="acc_second_slot",
                email="slot-taken@example.com",
                chatgpt_account_id="raw_second_slot",
                workspace_id="ws_shared_slot",
            )
            is False
        )
        assert (
            await repo.workspace_slot_taken(
                account_id="acc_second_slot",
                email="slot-taken@example.com",
                chatgpt_account_id="raw_first_slot",
                workspace_id="ws_shared_slot",
            )
            is True
        )
        assert (
            await repo.workspace_slot_taken(
                account_id="acc_second_slot",
                email="slot-taken@example.com",
                chatgpt_account_id=None,
                workspace_id="ws_shared_slot",
            )
            is True
        )


@pytest.mark.asyncio
async def test_account_slot_keeps_distinct_workspace_chatgpt_identities(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        first = _make_account("acc_first", "shared-workspace@example.com")
        first.chatgpt_account_id = "raw_first"
        first.workspace_id = "ws_shared"
        await repo.upsert_account_slot(first, preserve_unknown_workspace_duplicates=False)

        second = _make_account("acc_second", "shared-workspace@example.com")
        second.chatgpt_account_id = "raw_second"
        second.workspace_id = "ws_shared"
        saved = await repo.upsert_account_slot(second, preserve_unknown_workspace_duplicates=False)

        assert saved.id == "acc_second"

        accounts = list((await session.execute(select(Account).order_by(Account.id.asc()))).scalars().all())
        assert [(account.id, account.chatgpt_account_id) for account in accounts] == [
            ("acc_first", "raw_first"),
            ("acc_second", "raw_second"),
        ]


@pytest.mark.asyncio
async def test_account_slot_preserves_known_chatgpt_identity_on_email_fallback(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        known = _make_account("acc_known", "known-workspace@example.com")
        known.chatgpt_account_id = "raw_known"
        known.workspace_id = "ws_known"
        known.workspace_label = "Known Team"
        await repo.upsert_account_slot(known, preserve_unknown_workspace_duplicates=False)

        imported = _make_account("acc_imported", "known-workspace@example.com")
        imported.workspace_id = "ws_known"
        imported.workspace_label = "Known Team Updated"
        imported.plan_type = "team"
        saved = await repo.upsert_account_slot(imported, preserve_unknown_workspace_duplicates=False)

        assert saved.id == "acc_known"
        assert saved.chatgpt_account_id == "raw_known"
        assert saved.workspace_id == "ws_known"
        assert saved.workspace_label == "Known Team Updated"
        assert saved.plan_type == "team"

        accounts = list((await session.execute(select(Account))).scalars().all())
        assert [(account.id, account.chatgpt_account_id) for account in accounts] == [("acc_known", "raw_known")]


@pytest.mark.asyncio
async def test_account_slot_does_not_promote_mismatched_legacy_row_by_email(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        legacy = _make_account("acc_legacy", "legacy-shared@example.com")
        legacy.chatgpt_account_id = "raw_legacy"
        await repo.upsert(legacy, merge_by_email=False)

        workspace = _make_account("acc_workspace", "legacy-shared@example.com")
        workspace.chatgpt_account_id = "raw_workspace"
        workspace.workspace_id = "ws_new"
        saved = await repo.upsert_account_slot(workspace, preserve_unknown_workspace_duplicates=False)

        assert saved.id == "acc_workspace"

        accounts = list((await session.execute(select(Account).order_by(Account.id.asc()))).scalars().all())
        assert [(account.id, account.chatgpt_account_id, account.workspace_id) for account in accounts] == [
            ("acc_legacy", "raw_legacy", None),
            ("acc_workspace", "raw_workspace", "ws_new"),
        ]


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
async def test_accounts_upsert_merge_by_chatgpt_identity_does_not_clear_workspace_on_workspace_less_reauth(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        workspace = _make_account_with_chatgpt_id("acc_workspace", "shared@example.com", "chatgpt_workspace_less")
        workspace.workspace_id = "ws_business"
        workspace.workspace_label = "Business"
        await repo.upsert(workspace, merge_by_email=False)

        reauth = _make_account_with_chatgpt_id("acc_reauth", "shared@example.com", "chatgpt_workspace_less")
        reauth.workspace_id = None
        reauth.workspace_label = None
        saved = await repo.upsert(reauth, merge_by_email=False, merge_by_chatgpt_identity=True)

        assert saved.id == "acc_reauth"
        assert saved.workspace_id is None

        stored_workspace = (await session.execute(select(Account).where(Account.id == "acc_workspace"))).scalar_one()
        assert stored_workspace.workspace_id == "ws_business"
        assert stored_workspace.workspace_label == "Business"


@pytest.mark.asyncio
async def test_accounts_upsert_merge_by_chatgpt_identity_preserves_workspace_when_deterministic_id_matches(
    db_setup,
):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        workspace = _make_account_with_chatgpt_id("acc_reauth", "same-id-workspace@example.com", "chatgpt_same_id")
        workspace.workspace_id = "ws_learned"
        workspace.workspace_label = "Learned Workspace"
        workspace.seat_type = "member"
        workspace.plan_type = "team"
        await repo.upsert(workspace, merge_by_email=False)

        reauth = _make_account_with_chatgpt_id("acc_reauth", "same-id-workspace@example.com", "chatgpt_same_id")
        reauth.workspace_id = None
        reauth.workspace_label = None
        reauth.seat_type = None
        reauth.plan_type = "plus"
        saved = await repo.upsert(reauth, merge_by_email=False, merge_by_chatgpt_identity=True)

        assert saved.id == "acc_reauth"
        assert saved.workspace_id == "ws_learned"
        assert saved.workspace_label == "Learned Workspace"
        assert saved.seat_type == "member"
        assert saved.plan_type == "plus"

        accounts = list((await session.execute(select(Account))).scalars().all())
        assert [(account.id, account.workspace_id) for account in accounts] == [("acc_reauth", "ws_learned")]


@pytest.mark.asyncio
async def test_accounts_upsert_merge_by_chatgpt_identity_workspace_less_reauth_uses_unknown_row(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        unknown = _make_account_with_chatgpt_id("acc_unknown", "shared@example.com", "chatgpt_unknown_reauth")
        unknown.created_at = utcnow() - timedelta(days=30)
        await repo.upsert(unknown, merge_by_email=False)

        workspace = _make_account_with_chatgpt_id("acc_workspace", "shared@example.com", "chatgpt_unknown_reauth")
        workspace.workspace_id = "ws_business"
        workspace.created_at = utcnow() - timedelta(days=10)
        await repo.upsert(workspace, merge_by_email=False)

        reauth = _make_account_with_chatgpt_id("acc_reauth", "shared@example.com", "chatgpt_unknown_reauth")
        reauth.plan_type = "team"
        saved = await repo.upsert(reauth, merge_by_email=False, merge_by_chatgpt_identity=True)

        assert saved.id == "acc_unknown"
        assert saved.plan_type == "team"
        assert saved.workspace_id is None

        stored_workspace = (await session.execute(select(Account).where(Account.id == "acc_workspace"))).scalar_one()
        assert stored_workspace.workspace_id == "ws_business"


@pytest.mark.asyncio
async def test_accounts_upsert_merge_by_chatgpt_identity_prefers_matching_workspace_row(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        unknown = _make_account_with_chatgpt_id("acc_unknown", "shared@example.com", "chatgpt_workspace_reauth")
        unknown.created_at = utcnow() - timedelta(days=30)
        await repo.upsert(unknown, merge_by_email=False)

        workspace = _make_account_with_chatgpt_id("acc_workspace", "shared@example.com", "chatgpt_workspace_reauth")
        workspace.workspace_id = "ws_business"
        workspace.created_at = utcnow() - timedelta(days=10)
        await repo.upsert(workspace, merge_by_email=False)

        reauth = _make_account_with_chatgpt_id("acc_reauth", "shared@example.com", "chatgpt_workspace_reauth")
        reauth.workspace_id = "ws_business"
        reauth.plan_type = "team"
        saved = await repo.upsert(reauth, merge_by_email=False, merge_by_chatgpt_identity=True)

        assert saved.id == "acc_workspace"
        assert saved.plan_type == "team"
        assert saved.workspace_id == "ws_business"

        stored_unknown = (await session.execute(select(Account).where(Account.id == "acc_unknown"))).scalar_one()
        assert stored_unknown.workspace_id is None


@pytest.mark.asyncio
async def test_accounts_upsert_merge_by_chatgpt_identity_reconciles_duplicate_rows(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)

        canonical = _make_account_with_chatgpt_id("acc_merge_main", "merge@example.com", "chatgpt_merge")
        await repo.upsert(canonical, merge_by_email=False)

        duplicate = _make_account_with_chatgpt_id("acc_merge_main__copy2", "merge@example.com", "chatgpt_merge")
        await repo.upsert(duplicate, merge_by_email=False)
        duplicate_again = _make_account_with_chatgpt_id("acc_merge_main__copy3", "merge@example.com", "chatgpt_merge")
        await repo.upsert(duplicate_again, merge_by_email=False)

        duplicate_row = (
            await session.execute(select(Account).where(Account.id == "acc_merge_main__copy2"))
        ).scalar_one()
        duplicate_again_row = (
            await session.execute(select(Account).where(Account.id == "acc_merge_main__copy3"))
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
        session.add(ApiKeyAccountAssignment(api_key_id=api_key.id, account_id=duplicate_again_row.id))
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
                account_id=canonical.id,
                window="primary",
                reset_at=123,
                status="completed",
                model="gpt-5.1",
                attempted_at=utcnow() - timedelta(minutes=5),
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
        assert account_warmups[0].status == "completed"

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


@pytest.mark.asyncio
async def test_request_logs_repository_persists_useragent_fields(db_setup):
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)

        await repo.add_log(
            account_id=None,
            request_id="req_useragent_full",
            model="gpt-5.1",
            input_tokens=3,
            output_tokens=7,
            latency_ms=42,
            status="success",
            error_code=None,
            useragent="opencode/1.15.13 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14",
            useragent_group="opencode",
        )

        result = await session.execute(select(RequestLog).where(RequestLog.request_id == "req_useragent_full"))
        stored = result.scalar_one()
        assert stored.useragent == "opencode/1.15.13 ai-sdk/provider-utils/4.0.23 runtime/bun/1.3.14"
        assert stored.useragent_group == "opencode"


@pytest.mark.asyncio
async def test_request_logs_repository_preserves_null_useragent_fields(db_setup):
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)

        await repo.add_log(
            account_id=None,
            request_id="req_useragent_null",
            model="gpt-5.1",
            input_tokens=3,
            output_tokens=7,
            latency_ms=42,
            status="success",
            error_code=None,
            useragent="",
            useragent_group="",
        )

        result = await session.execute(select(RequestLog).where(RequestLog.request_id == "req_useragent_null"))
        stored = result.scalar_one()
        assert stored.useragent is None
        assert stored.useragent_group is None


@pytest.mark.asyncio
async def test_request_logs_repository_persists_null_useragent_fields_when_omitted(db_setup):
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)

        await repo.add_log(
            account_id=None,
            request_id="req_useragent_missing",
            model="gpt-5.1",
            input_tokens=3,
            output_tokens=7,
            latency_ms=42,
            status="success",
            error_code=None,
        )

        result = await session.execute(select(RequestLog).where(RequestLog.request_id == "req_useragent_missing"))
        stored = result.scalar_one()
        assert stored.useragent is None
        assert stored.useragent_group is None


@pytest.mark.asyncio
async def test_request_logs_repository_normalizes_whitespace_only_useragent_fields_to_null(db_setup):
    async with SessionLocal() as session:
        repo = RequestLogsRepository(session)

        await repo.add_log(
            account_id=None,
            request_id="req_useragent_whitespace",
            model="gpt-5.1",
            input_tokens=3,
            output_tokens=7,
            latency_ms=42,
            status="success",
            error_code=None,
            useragent=" \t\n ",
            useragent_group="   ",
        )

        result = await session.execute(select(RequestLog).where(RequestLog.request_id == "req_useragent_whitespace"))
        stored = result.scalar_one()
        assert stored.useragent is None
        assert stored.useragent_group is None

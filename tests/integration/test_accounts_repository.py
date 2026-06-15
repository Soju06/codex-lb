from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from app.core.utils.time import utcnow
from app.db.models import Account, AccountRateLimitResetCredit, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountRateLimitResetCreditRecord, AccountsRepository


def _account(
    account_id: str = "acc_refresh",
    *,
    chatgpt_account_id: str = "chatgpt_refresh",
    email: str | None = None,
    workspace_id: str | None = None,
    workspace_label: str | None = None,
) -> Account:
    return Account(
        id=account_id,
        chatgpt_account_id=chatgpt_account_id,
        email=email or f"{account_id}@example.com",
        workspace_id=workspace_id,
        workspace_label=workspace_label,
        plan_type="plus",
        access_token_encrypted=b"access",
        refresh_token_encrypted=b"refresh",
        id_token_encrypted=b"id",
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
        limit_warmup_enabled=True,
    )


@pytest.mark.asyncio
async def test_list_accounts_refresh_existing_reloads_identity_map(db_setup):
    del db_setup
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_account())

    async with SessionLocal() as reader_session:
        reader_repo = AccountsRepository(reader_session)
        loaded = (await reader_repo.list_accounts())[0]
        assert loaded.limit_warmup_enabled is True
        await reader_session.commit()

        async with SessionLocal() as writer_session:
            writer_repo = AccountsRepository(writer_session)
            assert await writer_repo.update_limit_warmup_enabled("acc_refresh", False) is True

        stale = (await reader_repo.list_accounts())[0]
        assert stale is loaded
        assert stale.limit_warmup_enabled is True

        refreshed = (await reader_repo.list_accounts(refresh_existing=True))[0]
        assert refreshed is loaded
        assert refreshed.limit_warmup_enabled is False


@pytest.mark.asyncio
async def test_upsert_account_slot_preserves_emails_sharing_workspace_identity(db_setup):
    del db_setup
    shared_chatgpt_id = "chatgpt_workspace_shared"
    shared_workspace_id = "workspace_shared"
    first = _account(
        "first_slot",
        chatgpt_account_id=shared_chatgpt_id,
        email="first@example.com",
        workspace_id=shared_workspace_id,
    )
    second = _account(
        "second_slot",
        chatgpt_account_id=shared_chatgpt_id,
        email="second@example.com",
        workspace_id=shared_workspace_id,
    )

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        saved_first = await repo.upsert_account_slot(first, preserve_unknown_workspace_duplicates=False)
        saved_second = await repo.upsert_account_slot(second, preserve_unknown_workspace_duplicates=False)

        assert saved_first.id == "first_slot"
        assert saved_second.id == "second_slot"
        accounts = await repo.list_accounts()

    assert [(account.id, account.email) for account in accounts] == [
        ("first_slot", "first@example.com"),
        ("second_slot", "second@example.com"),
    ]


@pytest.mark.asyncio
async def test_upsert_account_slot_preserves_emails_sharing_workspace_less_identity(db_setup):
    del db_setup
    shared_chatgpt_id = "chatgpt_workspace_less_shared"
    first = _account(
        "workspace_less_first",
        chatgpt_account_id=shared_chatgpt_id,
        email="first@example.com",
    )
    second = _account(
        "workspace_less_second",
        chatgpt_account_id=shared_chatgpt_id,
        email="second@example.com",
    )

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        saved_first = await repo.upsert_account_slot(first, preserve_unknown_workspace_duplicates=False)
        saved_second = await repo.upsert_account_slot(second, preserve_unknown_workspace_duplicates=False)

        assert saved_first.id == "workspace_less_first"
        assert saved_second.id == "workspace_less_second"
        accounts = await repo.list_accounts()

    assert [(account.id, account.email) for account in accounts] == [
        ("workspace_less_first", "first@example.com"),
        ("workspace_less_second", "second@example.com"),
    ]


@pytest.mark.asyncio
async def test_upsert_account_slot_preserves_same_chatgpt_id_across_workspace_ids(db_setup):
    del db_setup
    first = _account(
        "mavos_primary",
        chatgpt_account_id="chatgpt_mavos_shared",
        email="operator@example.com",
        workspace_id="ws_mavos_primary",
    )
    second = _account(
        "mavos_secondary",
        chatgpt_account_id="chatgpt_mavos_shared",
        email="operator@example.com",
        workspace_id="ws_mavos_secondary",
    )

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        saved_first = await repo.upsert_account_slot(first, preserve_unknown_workspace_duplicates=False)
        saved_second = await repo.upsert_account_slot(second, preserve_unknown_workspace_duplicates=False)
        accounts = await repo.list_accounts()

    assert saved_first.id == "mavos_primary"
    assert saved_second.id == "mavos_secondary"
    assert [(account.id, account.workspace_id) for account in accounts] == [
        ("mavos_primary", "ws_mavos_primary"),
        ("mavos_secondary", "ws_mavos_secondary"),
    ]


@pytest.mark.asyncio
async def test_upsert_account_slot_adds_third_workspace_slot_for_same_email(db_setup):
    del db_setup
    first = _account(
        "mavos_primary",
        chatgpt_account_id="chatgpt_mavos_primary",
        email="operator@example.com",
        workspace_id="ws_mavos_primary",
    )
    second = _account(
        "mavos_secondary",
        chatgpt_account_id="chatgpt_mavos_secondary",
        email="operator@example.com",
        workspace_id="ws_mavos_secondary",
    )
    third = _account(
        "mavos_tertiary",
        chatgpt_account_id="chatgpt_mavos_tertiary",
        email="operator@example.com",
        workspace_id="ws_mavos_tertiary",
    )

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        saved_first = await repo.upsert_account_slot(first, preserve_unknown_workspace_duplicates=False)
        saved_second = await repo.upsert_account_slot(second, preserve_unknown_workspace_duplicates=False)
        saved_third = await repo.upsert_account_slot(third, preserve_unknown_workspace_duplicates=False)
        accounts = await repo.list_accounts()

    assert saved_first.id == "mavos_primary"
    assert saved_second.id == "mavos_secondary"
    assert saved_third.id == "mavos_tertiary"
    assert [(account.id, account.workspace_id) for account in accounts] == [
        ("mavos_primary", "ws_mavos_primary"),
        ("mavos_secondary", "ws_mavos_secondary"),
        ("mavos_tertiary", "ws_mavos_tertiary"),
    ]


@pytest.mark.asyncio
async def test_upsert_account_slot_preserves_same_chatgpt_id_across_workspace_labels(db_setup):
    del db_setup
    first = _account(
        "mavos_workspace",
        chatgpt_account_id="chatgpt_mavos_shared_label",
        email="operator@example.com",
        workspace_label="Mavos",
    )
    second = _account(
        "triton_workspace",
        chatgpt_account_id="chatgpt_mavos_shared_label",
        email="operator@example.com",
        workspace_label="Triton",
    )

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        saved_first = await repo.upsert_account_slot(first, preserve_unknown_workspace_duplicates=False)
        saved_second = await repo.upsert_account_slot(second, preserve_unknown_workspace_duplicates=False)
        accounts = await repo.list_accounts()

    assert saved_first.id == "mavos_workspace"
    assert saved_second.id == "triton_workspace"
    assert [(account.id, account.workspace_label) for account in accounts] == [
        ("mavos_workspace", "Mavos"),
        ("triton_workspace", "Triton"),
    ]


@pytest.mark.asyncio
async def test_upsert_account_slot_adds_third_label_only_workspace_for_same_email(db_setup):
    del db_setup
    first = _account(
        "mavos_workspace",
        chatgpt_account_id="chatgpt_label_slots",
        email="operator@example.com",
        workspace_label="Mavos",
    )
    second = _account(
        "triton_workspace",
        chatgpt_account_id="chatgpt_label_slots",
        email="operator@example.com",
        workspace_label="Triton",
    )
    third = _account(
        "atlas_workspace",
        chatgpt_account_id="chatgpt_label_slots",
        email="operator@example.com",
        workspace_label="Atlas",
    )

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        saved_first = await repo.upsert_account_slot(first, preserve_unknown_workspace_duplicates=False)
        saved_second = await repo.upsert_account_slot(second, preserve_unknown_workspace_duplicates=False)
        saved_third = await repo.upsert_account_slot(third, preserve_unknown_workspace_duplicates=False)
        accounts = await repo.list_accounts()

    assert saved_first.id == "mavos_workspace"
    assert saved_second.id == "triton_workspace"
    assert saved_third.id == "atlas_workspace"
    assert [(account.id, account.workspace_label) for account in accounts] == [
        ("mavos_workspace", "Mavos"),
        ("triton_workspace", "Triton"),
        ("atlas_workspace", "Atlas"),
    ]


@pytest.mark.asyncio
async def test_insert_rate_limit_reset_credits_if_missing_keeps_existing_rows(db_setup):
    del db_setup
    granted_at = utcnow() - timedelta(days=2)
    first_expires_at = utcnow() + timedelta(days=7)
    duplicate_expires_at = utcnow() + timedelta(days=14)

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_account("acc_reset_credit"))

        inserted = await repo.insert_rate_limit_reset_credits_if_missing(
            [
                AccountRateLimitResetCreditRecord(
                    account_id="acc_reset_credit",
                    credit_id="credit_one",
                    status="available",
                    granted_at=granted_at,
                    expires_at=first_expires_at,
                    redeemed_at=None,
                )
            ]
        )
        assert inserted == 1

        inserted_again = await repo.insert_rate_limit_reset_credits_if_missing(
            [
                AccountRateLimitResetCreditRecord(
                    account_id="acc_reset_credit",
                    credit_id="credit_one",
                    status="expired",
                    granted_at=granted_at + timedelta(days=1),
                    expires_at=duplicate_expires_at,
                    redeemed_at=granted_at + timedelta(days=3),
                ),
                AccountRateLimitResetCreditRecord(
                    account_id="acc_reset_credit",
                    credit_id="credit_two",
                    status="available",
                    granted_at=granted_at,
                    expires_at=duplicate_expires_at,
                    redeemed_at=None,
                ),
            ]
        )
        assert inserted_again == 1

        rows = (
            await session.execute(
                AccountRateLimitResetCredit.__table__.select().order_by(AccountRateLimitResetCredit.credit_id)
            )
        ).all()

    assert len(rows) == 2
    assert rows[0].credit_id == "credit_one"
    assert rows[0].status == "available"
    assert rows[0].granted_at == granted_at
    assert rows[0].expires_at == first_expires_at
    assert rows[0].redeemed_at is None
    assert rows[1].credit_id == "credit_two"


@pytest.mark.asyncio
async def test_expire_rate_limit_reset_credits_and_count_available_by_account(db_setup):
    del db_setup
    now = utcnow()

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_account("acc_reset_one"))
        await repo.upsert(_account("acc_reset_two"))
        await repo.insert_rate_limit_reset_credits_if_missing(
            [
                AccountRateLimitResetCreditRecord(
                    account_id="acc_reset_one",
                    credit_id="credit_expired",
                    status="available",
                    granted_at=now - timedelta(days=3),
                    expires_at=now - timedelta(minutes=1),
                    redeemed_at=None,
                ),
                AccountRateLimitResetCreditRecord(
                    account_id="acc_reset_one",
                    credit_id="credit_available",
                    status="available",
                    granted_at=now - timedelta(days=1),
                    expires_at=now + timedelta(days=1),
                    redeemed_at=None,
                ),
                AccountRateLimitResetCreditRecord(
                    account_id="acc_reset_two",
                    credit_id="credit_unavailable",
                    status="redeemed",
                    granted_at=now - timedelta(days=1),
                    expires_at=now + timedelta(days=1),
                    redeemed_at=now - timedelta(hours=1),
                ),
            ]
        )

        counts_before_expiry = await repo.count_available_rate_limit_reset_credits_by_account(
            ["acc_reset_one", "acc_reset_two"],
            now=now,
        )
        expired = await repo.expire_rate_limit_reset_credits(now=now)
        counts_after_expiry = await repo.count_available_rate_limit_reset_credits_by_account(
            ["acc_reset_one", "acc_reset_two"],
            now=now,
        )
        expired_row = await session.get(AccountRateLimitResetCredit, 1)

    assert counts_before_expiry == {"acc_reset_one": 1}
    assert expired == 1
    assert counts_after_expiry == {"acc_reset_one": 1}
    assert expired_row is not None
    assert expired_row.credit_id == "credit_expired"
    assert expired_row.status == "expired"


@pytest.mark.asyncio
async def test_rate_limit_reset_credit_at_expiry_boundary_stays_available_until_after_now(db_setup):
    del db_setup
    now = utcnow()

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_account("acc_reset_boundary"))
        await repo.insert_rate_limit_reset_credits_if_missing(
            [
                AccountRateLimitResetCreditRecord(
                    account_id="acc_reset_boundary",
                    credit_id="credit_boundary",
                    status="available",
                    granted_at=now - timedelta(days=1),
                    expires_at=now,
                    redeemed_at=None,
                )
            ]
        )

        counts_at_boundary = await repo.count_available_rate_limit_reset_credits_by_account(
            ["acc_reset_boundary"],
            now=now,
        )
        expired_at_boundary = await repo.expire_rate_limit_reset_credits(now=now)
        counts_after_boundary = await repo.count_available_rate_limit_reset_credits_by_account(
            ["acc_reset_boundary"],
            now=now + timedelta(microseconds=1),
        )
        expired_after_boundary = await repo.expire_rate_limit_reset_credits(now=now + timedelta(microseconds=1))
        boundary_row = await session.get(AccountRateLimitResetCredit, 1)

    assert counts_at_boundary == {"acc_reset_boundary": 1}
    assert expired_at_boundary == 0
    assert counts_after_boundary == {}
    assert expired_after_boundary == 1
    assert boundary_row is not None
    assert boundary_row.status == "expired"


@pytest.mark.asyncio
async def test_insert_rate_limit_reset_credits_if_missing_handles_commit_time_duplicate_conflict(db_setup, monkeypatch):
    del db_setup
    now = utcnow()

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_account("acc_reset_race"))
        credit = AccountRateLimitResetCreditRecord(
            account_id="acc_reset_race",
            credit_id="credit_race",
            status="available",
            granted_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=1),
            redeemed_at=None,
        )

        original_commit = session.commit
        conflict_injected = False

        async def commit_with_duplicate_conflict() -> None:
            nonlocal conflict_injected
            if conflict_injected:
                await original_commit()
                return

            conflict_injected = True
            async with SessionLocal() as competing_session:
                competing_session.add(
                    AccountRateLimitResetCredit(
                        account_id=credit.account_id,
                        credit_id=credit.credit_id,
                        status=credit.status,
                        granted_at=credit.granted_at,
                        expires_at=credit.expires_at,
                        redeemed_at=credit.redeemed_at,
                    )
                )
                await competing_session.commit()
            raise IntegrityError("INSERT", {}, Exception("duplicate"))

        monkeypatch.setattr(session, "commit", commit_with_duplicate_conflict)

        inserted = await repo.insert_rate_limit_reset_credits_if_missing([credit])
        counts = await repo.count_available_rate_limit_reset_credits_by_account(["acc_reset_race"], now=now)
        rows = (
            await session.execute(
                AccountRateLimitResetCredit.__table__.select().where(
                    AccountRateLimitResetCredit.account_id == "acc_reset_race"
                )
            )
        ).all()

    assert inserted == 0
    assert counts == {"acc_reset_race": 1}
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_insert_rate_limit_reset_credits_if_missing_retries_remaining_rows_after_mixed_batch_conflict(
    db_setup, monkeypatch
):
    del db_setup
    now = utcnow()

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_account("acc_reset_mixed_race"))
        conflicted_credit = AccountRateLimitResetCreditRecord(
            account_id="acc_reset_mixed_race",
            credit_id="credit_conflicted",
            status="available",
            granted_at=now - timedelta(days=2),
            expires_at=now + timedelta(days=2),
            redeemed_at=None,
        )
        remaining_credit = AccountRateLimitResetCreditRecord(
            account_id="acc_reset_mixed_race",
            credit_id="credit_remaining",
            status="available",
            granted_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=3),
            redeemed_at=None,
        )

        original_commit = session.commit
        conflict_injected = False

        async def commit_with_mixed_batch_conflict() -> None:
            nonlocal conflict_injected
            if conflict_injected:
                await original_commit()
                return

            conflict_injected = True
            async with SessionLocal() as competing_session:
                competing_session.add(
                    AccountRateLimitResetCredit(
                        account_id=conflicted_credit.account_id,
                        credit_id=conflicted_credit.credit_id,
                        status=conflicted_credit.status,
                        granted_at=conflicted_credit.granted_at,
                        expires_at=conflicted_credit.expires_at,
                        redeemed_at=conflicted_credit.redeemed_at,
                    )
                )
                await competing_session.commit()
            raise IntegrityError("INSERT", {}, Exception("duplicate"))

        monkeypatch.setattr(session, "commit", commit_with_mixed_batch_conflict)

        inserted = await repo.insert_rate_limit_reset_credits_if_missing([conflicted_credit, remaining_credit])
        counts = await repo.count_available_rate_limit_reset_credits_by_account(["acc_reset_mixed_race"], now=now)
        rows = (
            await session.execute(
                AccountRateLimitResetCredit.__table__.select()
                .where(AccountRateLimitResetCredit.account_id == "acc_reset_mixed_race")
                .order_by(AccountRateLimitResetCredit.credit_id)
            )
        ).all()

    assert inserted == 1
    assert counts == {"acc_reset_mixed_race": 2}
    assert [row.credit_id for row in rows] == ["credit_conflicted", "credit_remaining"]


@pytest.mark.asyncio
async def test_insert_rate_limit_reset_credits_if_missing_reraises_non_duplicate_integrity_error(db_setup, monkeypatch):
    del db_setup
    now = utcnow()

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_account("acc_reset_non_duplicate_error"))
        credit = AccountRateLimitResetCreditRecord(
            account_id="acc_reset_non_duplicate_error",
            credit_id="credit_non_duplicate_error",
            status="available",
            granted_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=1),
            redeemed_at=None,
        )

        original_execute = session.execute

        async def execute_without_existing_pairs(statement: Any, *args: Any, **kwargs: Any) -> Any:
            return await original_execute(statement, *args, **kwargs)

        async def commit_with_non_duplicate_integrity_error() -> None:
            raise IntegrityError("INSERT", {}, Exception("not-a-duplicate"))

        monkeypatch.setattr(session, "execute", execute_without_existing_pairs)
        monkeypatch.setattr(session, "commit", commit_with_non_duplicate_integrity_error)

        with pytest.raises(IntegrityError):
            await repo.insert_rate_limit_reset_credits_if_missing([credit])


@pytest.mark.asyncio
async def test_claim_nearest_expiry_available_credit_id_claims_credits_in_expiry_order(db_setup):
    del db_setup
    now = utcnow()

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_account("acc_claim_order"))
        await repo.insert_rate_limit_reset_credits_if_missing(
            [
                AccountRateLimitResetCreditRecord(
                    account_id="acc_claim_order",
                    credit_id="credit_soon",
                    status="available",
                    granted_at=now - timedelta(days=2),
                    expires_at=now + timedelta(hours=2),
                    redeemed_at=None,
                ),
                AccountRateLimitResetCreditRecord(
                    account_id="acc_claim_order",
                    credit_id="credit_later",
                    status="available",
                    granted_at=now - timedelta(days=1),
                    expires_at=now + timedelta(days=1),
                    redeemed_at=None,
                ),
            ]
        )

        first = await repo.claim_nearest_expiry_available_credit_id("acc_claim_order", now=now)
        second = await repo.claim_nearest_expiry_available_credit_id("acc_claim_order", now=now)
        third = await repo.claim_nearest_expiry_available_credit_id("acc_claim_order", now=now)
        rows = (
            await session.execute(
                AccountRateLimitResetCredit.__table__.select()
                .where(AccountRateLimitResetCredit.account_id == "acc_claim_order")
                .order_by(AccountRateLimitResetCredit.credit_id)
            )
        ).all()

    assert first == "credit_soon"
    assert second == "credit_later"
    assert third is None
    assert [(row.credit_id, row.status) for row in rows] == [
        ("credit_later", "redeeming"),
        ("credit_soon", "redeeming"),
    ]

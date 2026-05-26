from __future__ import annotations

import pytest

from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository


def _account(account_id: str = "acc_refresh") -> Account:
    return Account(
        id=account_id,
        chatgpt_account_id="chatgpt_refresh",
        email=f"{account_id}@example.com",
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

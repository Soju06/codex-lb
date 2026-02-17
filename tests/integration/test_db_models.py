from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal, get_session

pytestmark = pytest.mark.integration


def _make_account(account_id: str, email: str, status: AccountStatus) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email,
        plan_type="plus",
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=status,
        deactivation_reason=None,
    )


@pytest.mark.asyncio
async def test_duplicate_emails_allowed(db_setup):
    async with SessionLocal() as session:
        session.add(_make_account("acc1", "dup@example.com", AccountStatus.ACTIVE))
        await session.commit()

        session.add(_make_account("acc2", "dup@example.com", AccountStatus.ACTIVE))
        await session.commit()

        rows = await session.execute(select(Account).where(Account.email == "dup@example.com"))
        assert len(list(rows.scalars().all())) == 2


@pytest.mark.asyncio
async def test_status_enum_rejects_invalid_value(db_setup):
    async with SessionLocal() as session:
        account = _make_account("acc3", "enum@example.com", AccountStatus.ACTIVE)
        session.add(account)
        await session.commit()

        bad = _make_account("acc4", "enum2@example.com", AccountStatus.ACTIVE)
        bad.status = "invalid"  # type: ignore[assignment]
        session.add(bad)
        with pytest.raises((LookupError, StatementError)):
            await session.commit()


@pytest.mark.asyncio
async def test_get_session_rolls_back_on_error(db_setup, monkeypatch):
    called = {"rollback": False}
    original = AsyncSession.rollback

    async def wrapped(self):
        called["rollback"] = True
        await original(self)

    monkeypatch.setattr(AsyncSession, "rollback", wrapped)

    with pytest.raises(RuntimeError):
        async for session in get_session():
            session.add(_make_account("acc5", "rollback@example.com", AccountStatus.ACTIVE))
            raise RuntimeError("boom")

    async with SessionLocal() as session:
        result = await session.execute(select(Account).where(Account.id == "acc5"))
        assert result.scalar_one_or_none() is None

    assert called["rollback"] is True

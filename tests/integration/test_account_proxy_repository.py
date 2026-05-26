"""Integration tests for ``AccountsRepository`` SOCKS5 proxy CRUD methods."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountProxyRecord, AccountsRepository

pytestmark = pytest.mark.integration


def _proxy_auth_fixture(suffix: str = "primary") -> str:
    return f"proxy-fixture-value-{suffix}"


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
async def test_get_proxy_config_returns_none_when_no_proxy_set(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_make_account("acc_no_proxy", "noproxy@example.com"))

        assert await repo.get_proxy_config("acc_no_proxy") is None
        assert await repo.get_proxy_config("does_not_exist") is None


@pytest.mark.asyncio
async def test_update_proxy_persists_all_fields_and_round_trips(db_setup):
    encryptor = TokenEncryptor()
    encrypted_password = encryptor.encrypt(_proxy_auth_fixture())
    validated_at = utcnow()

    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_make_account("acc_proxy", "proxy@example.com"))

        ok = await repo.update_proxy(
            "acc_proxy",
            host="proxy.example.com",
            port=1080,
            username="proxy_user",
            password_encrypted=encrypted_password,
            remote_dns=False,
            label="house-1",
            last_validated_at=validated_at,
        )
        assert ok is True

        record = await repo.get_proxy_config("acc_proxy")
        assert record is not None
        assert record.password_encrypted is not None
        assert record == AccountProxyRecord(
            host="proxy.example.com",
            port=1080,
            username="proxy_user",
            password_encrypted=encrypted_password,
            remote_dns=False,
            label="house-1",
            last_validated_at=validated_at,
        )
        assert encryptor.decrypt(record.password_encrypted) == _proxy_auth_fixture()


@pytest.mark.asyncio
async def test_update_proxy_overwrites_previous_configuration(db_setup):
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_make_account("acc_swap", "swap@example.com"))

        await repo.update_proxy(
            "acc_swap",
            host="old.example.com",
            port=1080,
            username="old_user",
            password_encrypted=encryptor.encrypt("old"),
            remote_dns=True,
            label="old",
            last_validated_at=utcnow(),
        )

        ok = await repo.update_proxy(
            "acc_swap",
            host="new.example.com",
            port=1085,
            username=None,
            password_encrypted=None,
            remote_dns=False,
            label=None,
            last_validated_at=None,
        )
        assert ok is True

        record = await repo.get_proxy_config("acc_swap")
        assert record is not None
        assert record.host == "new.example.com"
        assert record.port == 1085
        assert record.username is None
        assert record.password_encrypted is None
        assert record.remote_dns is False
        assert record.label is None
        assert record.last_validated_at is None


@pytest.mark.asyncio
async def test_clear_proxy_resets_all_fields(db_setup):
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_make_account("acc_clear", "clear@example.com"))
        await repo.update_proxy(
            "acc_clear",
            host="proxy.example.com",
            port=1080,
            username="u",
            password_encrypted=encryptor.encrypt("p"),
            remote_dns=False,
            label="lbl",
            last_validated_at=utcnow(),
        )

        assert await repo.clear_proxy("acc_clear") is True
        assert await repo.get_proxy_config("acc_clear") is None

        # Confirm at the row level that proxy_remote_dns reset to True (the
        # NOT NULL default) and every other proxy column is NULL again.
        result = await session.execute(select(Account).where(Account.id == "acc_clear"))
        account = result.scalar_one()
        assert account.proxy_host is None
        assert account.proxy_port is None
        assert account.proxy_username is None
        assert account.proxy_password_encrypted is None
        assert account.proxy_remote_dns is True
        assert account.proxy_label is None
        assert account.proxy_last_validated_at is None


@pytest.mark.asyncio
async def test_update_proxy_rejects_invalid_input_at_repository_layer(db_setup):
    encryptor = TokenEncryptor()
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_make_account("acc_bad_input", "bad@example.com"))

        with pytest.raises(ValueError):
            await repo.update_proxy(
                "acc_bad_input",
                host="",
                port=1080,
                username=None,
                password_encrypted=None,
                remote_dns=True,
                label=None,
                last_validated_at=None,
            )

        with pytest.raises(ValueError):
            await repo.update_proxy(
                "acc_bad_input",
                host="proxy.example.com",
                port=0,
                username=None,
                password_encrypted=encryptor.encrypt("x"),
                remote_dns=True,
                label=None,
                last_validated_at=None,
            )


@pytest.mark.asyncio
async def test_update_proxy_returns_false_for_missing_account(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        ok = await repo.update_proxy(
            "nonexistent",
            host="proxy.example.com",
            port=1080,
            username=None,
            password_encrypted=None,
            remote_dns=True,
            label=None,
            last_validated_at=None,
        )
        assert ok is False
        assert await repo.clear_proxy("nonexistent") is False

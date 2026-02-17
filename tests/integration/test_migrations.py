from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.auth import DEFAULT_PLAN
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.migrations import MIGRATIONS, run_migrations
from app.db.migrations.versions import drop_accounts_email_unique
from app.db.models import Account, AccountStatus
from app.db.session import SessionLocal
from app.modules.accounts.repository import AccountsRepository

pytestmark = pytest.mark.integration


def _make_account(account_id: str, email: str, plan_type: str) -> Account:
    encryptor = TokenEncryptor()
    return Account(
        id=account_id,
        email=email,
        plan_type=plan_type,
        access_token_encrypted=encryptor.encrypt("access"),
        refresh_token_encrypted=encryptor.encrypt("refresh"),
        id_token_encrypted=encryptor.encrypt("id"),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )


@pytest.mark.asyncio
async def test_run_migrations_preserves_unknown_plan_types(db_setup):
    async with SessionLocal() as session:
        repo = AccountsRepository(session)
        await repo.upsert(_make_account("acc_one", "one@example.com", "education"))
        await repo.upsert(_make_account("acc_two", "two@example.com", "PRO"))
        await repo.upsert(_make_account("acc_three", "three@example.com", ""))

    async with SessionLocal() as session:
        applied = await run_migrations(session)
        assert applied == len(MIGRATIONS)

    async with SessionLocal() as session:
        acc_one = await session.get(Account, "acc_one")
        acc_two = await session.get(Account, "acc_two")
        acc_three = await session.get(Account, "acc_three")
        assert acc_one is not None
        assert acc_two is not None
        assert acc_three is not None
        assert acc_one.plan_type == "education"
        assert acc_two.plan_type == "pro"
        assert acc_three.plan_type == DEFAULT_PLAN

    async with SessionLocal() as session:
        applied = await run_migrations(session)
        assert applied == 0


@pytest.mark.asyncio
async def test_drop_accounts_email_unique_handles_non_cascade_foreign_keys(tmp_path):
    db_path = tmp_path / "legacy-no-cascade.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as session:
            await session.execute(text("PRAGMA foreign_keys=ON"))
            await session.execute(
                text(
                    """
                    CREATE TABLE accounts (
                        id VARCHAR NOT NULL PRIMARY KEY,
                        chatgpt_account_id VARCHAR,
                        email VARCHAR NOT NULL UNIQUE,
                        plan_type VARCHAR NOT NULL,
                        access_token_encrypted BLOB NOT NULL,
                        refresh_token_encrypted BLOB NOT NULL,
                        id_token_encrypted BLOB NOT NULL,
                        last_refresh DATETIME NOT NULL,
                        created_at DATETIME NOT NULL,
                        status VARCHAR(32) NOT NULL,
                        deactivation_reason TEXT,
                        reset_at INTEGER
                    )
                    """
                )
            )
            await session.execute(
                text(
                    """
                    CREATE TABLE usage_history (
                        id INTEGER PRIMARY KEY,
                        account_id VARCHAR NOT NULL REFERENCES accounts(id),
                        recorded_at DATETIME NOT NULL,
                        window VARCHAR,
                        used_percent FLOAT NOT NULL,
                        input_tokens INTEGER,
                        output_tokens INTEGER,
                        reset_at INTEGER,
                        window_minutes INTEGER,
                        credits_has BOOLEAN,
                        credits_unlimited BOOLEAN,
                        credits_balance FLOAT
                    )
                    """
                )
            )
            await session.execute(
                text(
                    """
                    CREATE TABLE request_logs (
                        id INTEGER PRIMARY KEY,
                        account_id VARCHAR NOT NULL REFERENCES accounts(id),
                        request_id VARCHAR NOT NULL,
                        requested_at DATETIME NOT NULL,
                        model VARCHAR NOT NULL,
                        input_tokens INTEGER,
                        output_tokens INTEGER,
                        cached_input_tokens INTEGER,
                        reasoning_tokens INTEGER,
                        reasoning_effort VARCHAR,
                        latency_ms INTEGER,
                        status VARCHAR NOT NULL,
                        error_code VARCHAR,
                        error_message TEXT
                    )
                    """
                )
            )
            await session.execute(
                text(
                    """
                    CREATE TABLE sticky_sessions (
                        key VARCHAR PRIMARY KEY,
                        account_id VARCHAR NOT NULL REFERENCES accounts(id),
                        created_at DATETIME NOT NULL,
                        updated_at DATETIME NOT NULL
                    )
                    """
                )
            )
            await session.execute(
                text(
                    """
                    INSERT INTO accounts (
                        id, chatgpt_account_id, email, plan_type,
                        access_token_encrypted, refresh_token_encrypted, id_token_encrypted,
                        last_refresh, created_at, status, deactivation_reason, reset_at
                    )
                    VALUES (
                        'acc_legacy', 'chatgpt_legacy', 'legacy@example.com', 'plus',
                        x'01', x'02', x'03',
                        '2026-01-01 00:00:00', '2026-01-01 00:00:00', 'active', NULL, NULL
                    )
                    """
                )
            )
            await session.execute(
                text(
                    """
                    INSERT INTO usage_history (
                        id, account_id, recorded_at, window, used_percent,
                        input_tokens, output_tokens, reset_at, window_minutes,
                        credits_has, credits_unlimited, credits_balance
                    )
                    VALUES (
                        1, 'acc_legacy', '2026-01-01 00:00:00', 'hour', 0.2,
                        10, 20, NULL, 60, 1, 0, 50.0
                    )
                    """
                )
            )
            await session.execute(
                text(
                    """
                    INSERT INTO request_logs (
                        id, account_id, request_id, requested_at, model, input_tokens, output_tokens,
                        cached_input_tokens, reasoning_tokens, reasoning_effort, latency_ms, status,
                        error_code, error_message
                    )
                    VALUES (
                        1, 'acc_legacy', 'req_1', '2026-01-01 00:00:00', 'gpt-4o', 10, 20,
                        0, 0, NULL, 100, 'ok', NULL, NULL
                    )
                    """
                )
            )
            await session.execute(
                text(
                    """
                    INSERT INTO sticky_sessions (key, account_id, created_at, updated_at)
                    VALUES ('sticky_1', 'acc_legacy', '2026-01-01 00:00:00', '2026-01-01 00:00:00')
                    """
                )
            )
            await session.commit()

        async with session_factory() as session:
            await session.execute(text("PRAGMA foreign_keys=ON"))
            await drop_accounts_email_unique.run(session)
            await session.commit()

        async with session_factory() as session:
            await session.execute(text("PRAGMA foreign_keys=ON"))
            await session.execute(
                text(
                    """
                    INSERT INTO accounts (
                        id, chatgpt_account_id, email, plan_type,
                        access_token_encrypted, refresh_token_encrypted, id_token_encrypted,
                        last_refresh, created_at, status, deactivation_reason, reset_at
                    )
                    VALUES (
                        'acc_legacy_2', 'chatgpt_legacy_2', 'legacy@example.com', 'team',
                        x'11', x'12', x'13',
                        '2026-01-01 00:00:00', '2026-01-01 00:00:00', 'active', NULL, NULL
                    )
                    """
                )
            )
            usage_count = (
                await session.execute(text("SELECT COUNT(*) FROM usage_history WHERE account_id='acc_legacy'"))
            ).scalar_one()
            logs_count = (
                await session.execute(text("SELECT COUNT(*) FROM request_logs WHERE account_id='acc_legacy'"))
            ).scalar_one()
            sticky_count = (
                await session.execute(text("SELECT COUNT(*) FROM sticky_sessions WHERE account_id='acc_legacy'"))
            ).scalar_one()
            await session.commit()

            assert usage_count == 1
            assert logs_count == 1
            assert sticky_count == 1
    finally:
        await engine.dispose()

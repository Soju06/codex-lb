from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.migrate import check_schema_drift, run_upgrade
from app.db.models import Account, AccountStatus, Base
from app.modules.accounts.repository import AccountsRepository

pytestmark = pytest.mark.integration
PARENT = "20260918_000000_merge_scim_and_overflow_heads"
REVISION = "20261002_000000_add_http_bridge_dispatch_generation"


def snapshot(path: Path) -> dict[str, tuple[int, str]]:
    """Compare every existing table without displaying credential contents."""
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            if row[0] != "alembic_version"
        ]
        result = {}
        for table in tables:
            columns = [
                row[1] for row in connection.execute(f'PRAGMA table_info("{table}")') if row[1] != "dispatch_generation"
            ]
            projection = ", ".join(f'"{column}"' for column in columns)
            rows = sorted(repr(row) for row in connection.execute(f'SELECT {projection} FROM "{table}"'))
            result[table] = (len(rows), hashlib.sha256("\n".join(rows).encode()).hexdigest())
        return result


@pytest.mark.asyncio
async def test_additive_upgrade_on_copy_preserves_accounts_credentials_and_all_tables(tmp_path) -> None:
    source = tmp_path / "source.sqlite"
    copy = tmp_path / "migrated-copy.sqlite"
    source_url = f"sqlite+aiosqlite:///{source}"
    run_upgrade(source_url, PARENT, bootstrap_legacy=False)
    encryptor = TokenEncryptor()
    engine = create_async_engine(source_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        for index in range(3):
            session.add(
                Account(
                    id=f"account-{index}",
                    email=f"account-{index}@example.test",
                    plan_type="pro",
                    chatgpt_account_id=f"oauth-workspace-{index}",
                    chatgpt_user_id=f"oauth-user-{index}",
                    access_token_encrypted=encryptor.encrypt(f"access-{index}"),
                    refresh_token_encrypted=encryptor.encrypt(f"refresh-{index}"),
                    id_token_encrypted=encryptor.encrypt(f"identity-{index}"),
                    last_refresh=utcnow(),
                    status=AccountStatus.ACTIVE,
                )
            )
        await session.commit()
        # Historical operations use the old schema, deliberately without any
        # generation or authority inferred from their state.
        await session.execute(
            text(
                "INSERT INTO http_bridge_sessions "
                "(id, session_key_kind, session_key_value, session_key_hash, api_key_scope, owner_epoch, state) "
                "VALUES ('session', 'session_header', 'history', 'history', '__anonymous__', 1, 'closed')"
            )
        )
        await session.execute(
            text(
                "INSERT INTO http_bridge_operations "
                "(operation_id,session_id,request_fingerprint,state,response_id,event_spool_complete) "
                "VALUES ('complete','session','complete','completed','historical-response',1), "
                "('ambiguous','session','ambiguous','unknown',NULL,0)"
            )
        )
        await session.execute(
            text(
                "INSERT INTO http_bridge_operation_events "
                "(event_id,operation_id,sequence_number,event_fingerprint,event_text) "
                "VALUES ('event','complete',1,'event','historical-terminal')"
            )
        )
        await session.execute(
            text("UPDATE http_bridge_operations SET event_bytes = 19 WHERE operation_id = 'complete'")
        )
        await session.commit()
    await engine.dispose()
    before = snapshot(source)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as original, sqlite3.connect(copy) as target:
        original.backup(target)
    copy_url = f"sqlite+aiosqlite:///{copy}"
    result = run_upgrade(copy_url, bootstrap_legacy=False)
    assert result.current_revision == REVISION
    assert snapshot(copy) == before
    assert snapshot(source) == before
    assert check_schema_drift(copy_url) == ()
    with sqlite3.connect(copy) as connection:
        assert connection.execute("SELECT dispatch_generation FROM http_bridge_operations").fetchall() == [
            (None,),
            (None,),
        ]
        column = next(
            row
            for row in connection.execute("PRAGMA table_info(http_bridge_operations)")
            if row[1] == "dispatch_generation"
        )
        assert column[3] == 0 and column[4] is None
    migrated_engine = create_async_engine(copy_url)
    async with async_sessionmaker(migrated_engine, expire_on_commit=False)() as session:
        accounts = await AccountsRepository(session).list_accounts()
        assert len(accounts) == 3
        for account in accounts:
            index = account.id.rsplit("-", 1)[1]
            assert account.chatgpt_account_id == f"oauth-workspace-{index}"
            assert account.chatgpt_user_id == f"oauth-user-{index}"
            assert encryptor.decrypt(account.access_token_encrypted) == f"access-{index}"
            assert encryptor.decrypt(account.refresh_token_encrypted) == f"refresh-{index}"
            assert encryptor.decrypt(account.id_token_encrypted) == f"identity-{index}"
        from app.modules.proxy.durable_bridge_repository import DurableBridgeRepository

        repository = DurableBridgeRepository(session)
        assert await repository.get_operation_events(operation_id="complete") == ["historical-terminal"]
        operation = await repository.get_operation(operation_id="complete")
        assert operation is not None and operation.dispatch_generation is None and operation.event_spool_complete
    await migrated_engine.dispose()


def test_upgrade_accepts_current_metadata_schema_without_granting_legacy_authority(tmp_path) -> None:
    path = tmp_path / "metadata.sqlite"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    from app.db.migrate import _build_alembic_config

    url = f"sqlite+aiosqlite:///{path}"
    command.stamp(_build_alembic_config(url), PARENT)
    before = snapshot(path)
    assert run_upgrade(url, bootstrap_legacy=False).current_revision == REVISION
    assert snapshot(path) == before
    assert check_schema_drift(url) == ()
    engine.dispose()


def test_dispatch_migration_downgrade_preserves_parent_spools(tmp_path) -> None:
    path = tmp_path / "roundtrip.sqlite"
    url = f"sqlite+aiosqlite:///{path}"
    run_upgrade(url, bootstrap_legacy=False)
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.execute(text("PRAGMA foreign_keys = ON"))
        connection.execute(
            text(
                "INSERT INTO http_bridge_sessions "
                "(id, session_key_kind, session_key_value, session_key_hash, api_key_scope, owner_epoch, state) "
                "VALUES ('session', 'session_header', 'history', 'history', '__anonymous__', 1, 'closed')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO http_bridge_operations "
                "(operation_id, session_id, request_fingerprint, state, event_bytes, event_spool_complete) "
                "VALUES ('complete', 'session', 'complete', 'completed', 19, 1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO http_bridge_operation_events "
                "(event_id, operation_id, sequence_number, event_fingerprint, event_text) "
                "VALUES ('event', 'complete', 1, 'event', 'historical-terminal')"
            )
        )
        # Exercise native SQLite DROP COLUMN without a parent-table rebuild.
        connection.execute(text("CREATE TABLE preservation_probe (value TEXT)"))
        connection.execute(text("INSERT INTO preservation_probe VALUES ('untouched')"))
    before = snapshot(path)
    from app.db.migrate import _build_alembic_config

    config = _build_alembic_config(url)
    command.downgrade(config, PARENT)
    assert snapshot(path) == before
    run_upgrade(url, bootstrap_legacy=False)
    assert snapshot(path) == before
    engine.dispose()

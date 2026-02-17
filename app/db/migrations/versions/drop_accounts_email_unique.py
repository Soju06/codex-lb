from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def run(session: AsyncSession) -> None:
    bind = session.get_bind()
    dialect = getattr(getattr(bind, "dialect", None), "name", None)
    if dialect == "sqlite":
        await _sqlite_drop_email_unique(session)
    elif dialect == "postgresql":
        await session.execute(text("ALTER TABLE accounts DROP CONSTRAINT IF EXISTS accounts_email_key"))


async def _sqlite_drop_email_unique(session: AsyncSession) -> None:
    if not await _sqlite_has_email_unique_index(session):
        return

    await session.execute(text("DROP TABLE IF EXISTS _backup_usage_history"))
    await session.execute(text("DROP TABLE IF EXISTS _backup_request_logs"))
    await session.execute(text("DROP TABLE IF EXISTS _backup_sticky_sessions"))
    await session.execute(text("DROP TABLE IF EXISTS accounts_new"))

    await session.execute(
        text(
            "CREATE TEMP TABLE _backup_usage_history AS "
            "SELECT id, account_id, recorded_at, window, used_percent, input_tokens, output_tokens, "
            "reset_at, window_minutes, credits_has, credits_unlimited, credits_balance "
            "FROM usage_history"
        ),
    )
    await session.execute(
        text(
            "CREATE TEMP TABLE _backup_request_logs AS "
            "SELECT id, account_id, request_id, requested_at, model, input_tokens, output_tokens, "
            "cached_input_tokens, reasoning_tokens, reasoning_effort, latency_ms, status, error_code, error_message "
            "FROM request_logs"
        ),
    )
    await session.execute(
        text(
            "CREATE TEMP TABLE _backup_sticky_sessions AS "
            "SELECT key, account_id, created_at, updated_at FROM sticky_sessions"
        ),
    )

    # Some older SQLite schemas may enforce RESTRICT/NO ACTION on these FKs.
    # Clear children after backup so dropping/replacing accounts is always allowed.
    await session.execute(text("DELETE FROM usage_history"))
    await session.execute(text("DELETE FROM request_logs"))
    await session.execute(text("DELETE FROM sticky_sessions"))

    await session.execute(
        text(
            """
            CREATE TABLE accounts_new (
                id VARCHAR NOT NULL PRIMARY KEY,
                chatgpt_account_id VARCHAR,
                email VARCHAR NOT NULL,
                plan_type VARCHAR NOT NULL,
                access_token_encrypted BLOB NOT NULL,
                refresh_token_encrypted BLOB NOT NULL,
                id_token_encrypted BLOB NOT NULL,
                last_refresh DATETIME NOT NULL,
                created_at DATETIME NOT NULL DEFAULT (CURRENT_TIMESTAMP),
                status VARCHAR(32) NOT NULL DEFAULT 'active',
                deactivation_reason TEXT,
                reset_at INTEGER
            )
            """
        ),
    )
    await session.execute(
        text(
            "INSERT INTO accounts_new ("
            "id, chatgpt_account_id, email, plan_type, access_token_encrypted, "
            "refresh_token_encrypted, id_token_encrypted, last_refresh, created_at, "
            "status, deactivation_reason, reset_at"
            ") "
            "SELECT "
            "id, chatgpt_account_id, email, plan_type, access_token_encrypted, "
            "refresh_token_encrypted, id_token_encrypted, last_refresh, created_at, "
            "status, deactivation_reason, reset_at "
            "FROM accounts"
        ),
    )

    await session.execute(text("DROP TABLE accounts"))
    await session.execute(text("ALTER TABLE accounts_new RENAME TO accounts"))

    await session.execute(
        text(
            "INSERT INTO usage_history ("
            "id, account_id, recorded_at, window, used_percent, input_tokens, output_tokens, "
            "reset_at, window_minutes, credits_has, credits_unlimited, credits_balance"
            ") "
            "SELECT "
            "id, account_id, recorded_at, window, used_percent, input_tokens, output_tokens, "
            "reset_at, window_minutes, credits_has, credits_unlimited, credits_balance "
            "FROM _backup_usage_history"
        ),
    )
    await session.execute(
        text(
            "INSERT INTO request_logs ("
            "id, account_id, request_id, requested_at, model, input_tokens, output_tokens, "
            "cached_input_tokens, reasoning_tokens, reasoning_effort, latency_ms, status, error_code, error_message"
            ") "
            "SELECT "
            "id, account_id, request_id, requested_at, model, input_tokens, output_tokens, "
            "cached_input_tokens, reasoning_tokens, reasoning_effort, latency_ms, status, error_code, error_message "
            "FROM _backup_request_logs"
        ),
    )
    await session.execute(
        text(
            "INSERT INTO sticky_sessions (key, account_id, created_at, updated_at) "
            "SELECT key, account_id, created_at, updated_at FROM _backup_sticky_sessions"
        ),
    )

    await session.execute(text("DROP TABLE _backup_usage_history"))
    await session.execute(text("DROP TABLE _backup_request_logs"))
    await session.execute(text("DROP TABLE _backup_sticky_sessions"))


async def _sqlite_has_email_unique_index(session: AsyncSession) -> bool:
    result = await session.execute(text("PRAGMA index_list(accounts)"))
    indexes = result.fetchall()
    for row in indexes:
        if len(row) < 3:
            continue
        index_name = row[1]
        is_unique = bool(row[2])
        if not is_unique:
            continue
        escaped_name = str(index_name).replace('"', '""')
        info_result = await session.execute(text(f'PRAGMA index_info("{escaped_name}")'))
        columns = [info[2] for info in info_result.fetchall() if len(info) > 2]
        if columns == ["email"]:
            return True
    return False

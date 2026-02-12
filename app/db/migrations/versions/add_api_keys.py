from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session


def _metadata(session: Session) -> tuple[set[str], set[str]]:
    inspector = inspect(session.connection())
    tables = set(inspector.get_table_names())
    request_logs_columns = set()
    if "request_logs" in tables:
        request_logs_columns = {column["name"] for column in inspector.get_columns("request_logs")}
    return tables, request_logs_columns


async def run(session: AsyncSession) -> None:
    tables, request_logs_columns = await session.run_sync(_metadata)

    if "api_keys" not in tables:
        await session.execute(
            text(
                """
                CREATE TABLE api_keys (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    key_hash TEXT NOT NULL UNIQUE,
                    key_prefix TEXT NOT NULL,
                    allowed_models TEXT,
                    weekly_token_limit INTEGER,
                    weekly_tokens_used INTEGER NOT NULL DEFAULT 0,
                    weekly_reset_at DATETIME NOT NULL,
                    expires_at DATETIME,
                    is_active BOOLEAN NOT NULL DEFAULT TRUE,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_used_at DATETIME
                )
                """
            )
        )
        await session.execute(text("CREATE INDEX idx_api_keys_hash ON api_keys (key_hash)"))

    if "request_logs" in tables and "api_key_id" not in request_logs_columns:
        await session.execute(text("ALTER TABLE request_logs ADD COLUMN api_key_id TEXT"))

from __future__ import annotations

from collections.abc import Collection
from hashlib import sha256

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def advisory_lock_key(scope: str, value: str) -> int:
    digest = sha256(f"{scope}:{value}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def account_identity_lock_key(chatgpt_account_id: str) -> int:
    """Return the existing PostgreSQL lock namespace for one upstream identity."""
    return advisory_lock_key("account-id", f"chatgpt:{chatgpt_account_id}")


async def lock_postgresql_account_identities(
    session: AsyncSession,
    chatgpt_account_ids: Collection[str | None],
) -> tuple[int, ...]:
    """Lock upstream identity membership in canonical transaction-scoped order."""
    bind = session.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return ()
    lock_keys = tuple(
        sorted(
            {
                account_identity_lock_key(chatgpt_account_id)
                for chatgpt_account_id in chatgpt_account_ids
                if chatgpt_account_id
            }
        )
    )
    for lock_key in lock_keys:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": lock_key},
        )
    return lock_keys

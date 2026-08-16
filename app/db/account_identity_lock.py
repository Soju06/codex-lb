from __future__ import annotations

from collections.abc import Collection
from hashlib import sha256

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_POSTGRES_ACCOUNT_IDENTITY_LOCK_TIMEOUT_MS = 30_000


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
    try:
        if lock_keys:
            # Match the database layer's existing 30-second contention budget.
            # Transaction-local scope bounds every subsequent lock in this unit
            # of work and PostgreSQL restores it at transaction end.
            await session.execute(
                text("SELECT set_config('lock_timeout', :timeout, true)"),
                {"timeout": f"{_POSTGRES_ACCOUNT_IDENTITY_LOCK_TIMEOUT_MS}ms"},
            )
        for lock_key in lock_keys:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": lock_key},
            )
    except BaseException:
        await session.rollback()
        raise
    return lock_keys

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.settings import get_settings
from app.core.crypto import get_or_create_key
from app.db.models import RuntimeSentinel

logger = logging.getLogger(__name__)

ENCRYPTION_KEY_FINGERPRINT_SENTINEL = "encryption_key_fingerprint"
_FINGERPRINT_PREFIX_CHARS = 12


class EncryptionKeyFingerprintMismatchError(RuntimeError):
    """Raised when this replica's encryption key differs from the shared sentinel."""


def compute_encryption_key_fingerprint(key_file: Path | None = None) -> str:
    """Return ``sha256:<hex>`` over the raw encryption key bytes."""
    key = get_or_create_key(key_file)
    return f"sha256:{hashlib.sha256(key).hexdigest()}"


def _fingerprint_prefix(fingerprint: str) -> str:
    return fingerprint[: len("sha256:") + _FINGERPRINT_PREFIX_CHARS]


async def _stamp_if_absent(session: AsyncSession, fingerprint: str) -> None:
    values = {"name": ENCRYPTION_KEY_FINGERPRINT_SENTINEL, "value": fingerprint}
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        stmt = pg_insert(RuntimeSentinel).values(**values).on_conflict_do_nothing(index_elements=[RuntimeSentinel.name])
        await session.execute(stmt)
    elif dialect == "sqlite":
        stmt = (
            sqlite_insert(RuntimeSentinel)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[RuntimeSentinel.name])
        )
        await session.execute(stmt)
    else:
        existing = await session.scalar(
            select(RuntimeSentinel).where(RuntimeSentinel.name == ENCRYPTION_KEY_FINGERPRINT_SENTINEL)
        )
        if existing is None:
            session.add(RuntimeSentinel(**values))
    await session.commit()


async def verify_encryption_key_fingerprint(
    session_factory: Callable[[], AsyncSession] | None = None,
    *,
    key_file: Path | None = None,
    mode: str | None = None,
) -> None:
    """Stamp or verify the encryption-key fingerprint sentinel in the shared database.

    The first replica to boot atomically stamps ``sha256(key)`` into
    ``runtime_sentinels`` (insert-if-absent); every replica then compares its
    local fingerprint against the stored value. A mismatch means the replicas
    do not share the same encryption key material, which fails
    replica-dependently at use time (dashboard cookie 401s, undecryptable
    proxy passwords, ``bridge_forward_invalid`` HMAC rejections), so the
    default mode refuses startup with a remediation message.
    """
    settings = get_settings()
    effective_mode = mode if mode is not None else settings.encryption_key_fingerprint_mode
    if effective_mode == "off":
        return

    if session_factory is None:
        from app.db.session import SessionLocal

        session_factory = SessionLocal

    local_fingerprint = compute_encryption_key_fingerprint(key_file)
    async with session_factory() as session:
        await _stamp_if_absent(session, local_fingerprint)
        stored = await session.scalar(
            select(RuntimeSentinel.value).where(RuntimeSentinel.name == ENCRYPTION_KEY_FINGERPRINT_SENTINEL)
        )

    if stored is None or stored == local_fingerprint:
        return

    message = (
        "Encryption key mismatch across replicas: this replica's key fingerprint "
        f"{_fingerprint_prefix(local_fingerprint)}... does not match the fingerprint "
        f"{_fingerprint_prefix(stored)}... stamped in the shared database. Every replica must "
        "mount the same encryption.key file. If you rotated the key intentionally, delete the "
        f"'{ENCRYPTION_KEY_FINGERPRINT_SENTINEL}' row from runtime_sentinels, or set "
        "CODEX_LB_ENCRYPTION_KEY_FINGERPRINT_MODE=warn to bypass this check."
    )
    if effective_mode == "warn":
        logger.error(message)
        return
    raise EncryptionKeyFingerprintMismatchError(message)

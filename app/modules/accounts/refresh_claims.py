"""Cross-replica serialization of OAuth token refresh.

OpenAI refresh tokens are rotating/single-use: when two replicas exchange the
same refresh token concurrently, the loser receives a permanent
``refresh_token_reused``/``invalid_grant`` error and (pre-hardening) knocked a
healthy account out of rotation. The :class:`RefreshClaimCoordinator` grants at
most one claimant per account the right to run the upstream exchange, using a
per-account row in ``account_refresh_claims``.

Claim acquisition is a single conditional-upsert statement that is atomic on
both backends:

- PostgreSQL: ``INSERT .. ON CONFLICT DO UPDATE .. WHERE`` serializes
  concurrent claimers on the row lock; exactly one statement's WHERE passes.
- SQLite: the identical statement is atomic under SQLite's database-level
  single-writer lock (safe across processes sharing one file via
  ``busy_timeout``), additionally wrapped in ``sqlite_writer_section`` for
  in-process serialization.

No database lock or transaction is ever held across upstream network I/O: the
claim is plain row state with a TTL (``claim_expires_at``) so a crashed
claimant can never block refresh for longer than the TTL.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.sql.dml import Insert

from app.core.config.settings import get_settings
from app.core.utils.time import utcnow
from app.db.models import AccountRefreshClaim
from app.db.session import get_background_session, sqlite_writer_section

_SQLITE_BUSY_RETRY_ATTEMPTS = 4
_SQLITE_BUSY_RETRY_BASE_SECONDS = 0.05

# Distinguishes workers/processes sharing one bridge instance id so a claim is
# always scoped to exactly one event loop's refresh task.
_PROCESS_SUFFIX = uuid.uuid4().hex[:12]

# Width of ``account_refresh_claims.claimed_by`` (String(128)). The stored value
# composes the claimant identity with a per-refresh owner token so that two
# distinct refreshes for the same account (see ``_compose_claimed_by``) never
# reuse each other's claim.
_CLAIMED_BY_COLUMN_LEN = 128
# Chars of the per-refresh owner (a refresh-token fingerprint) kept in the
# stored ``claimed_by`` value. 16 hex chars = 64 bits, more than enough to keep
# distinct concurrent token materials on one account from colliding.
_CLAIM_OWNER_TOKEN_LEN = 16
_CLAIM_OWNER_SEPARATOR = "#"
# Room reserved after the claimant identity for the owner suffix ("#" + token).
_CLAIM_OWNER_SUFFIX_LEN = len(_CLAIM_OWNER_SEPARATOR) + _CLAIM_OWNER_TOKEN_LEN
_CLAIMANT_ID_MAX_LEN = _CLAIMED_BY_COLUMN_LEN - _CLAIM_OWNER_SUFFIX_LEN


def _compose_claimed_by(claimant_id: str, owner: str) -> str:
    """Compose the stored ``claimed_by`` from the claimant identity and owner.

    Claim ownership is per-refresh, not process-wide: the owner token (a
    refresh-token fingerprint) discriminates distinct concurrent refreshes for
    the same account so a second refresh with different token material cannot
    piggyback on the first refresh's claim via the same-claimant re-entry
    predicate. The owner suffix is always preserved in full; only the claimant
    portion is truncated to fit the column so distinct owners never collide.
    """
    owner_token = owner[:_CLAIM_OWNER_TOKEN_LEN]
    suffix = f"{_CLAIM_OWNER_SEPARATOR}{owner_token}"
    prefix = claimant_id[: _CLAIMED_BY_COLUMN_LEN - len(suffix)]
    return f"{prefix}{suffix}"


@dataclass(frozen=True, slots=True)
class RefreshClaimSnapshot:
    claimed_by: str
    claimed_at: datetime
    claim_expires_at: datetime

    def is_expired(self, now: datetime) -> bool:
        return self.claim_expires_at < now


class RefreshClaimCoordinatorPort(Protocol):
    @property
    def claimant_id(self) -> str: ...

    async def try_acquire(self, account_id: str, *, ttl_seconds: float, owner: str) -> bool: ...

    async def release(self, account_id: str, *, owner: str) -> None: ...


def default_refresh_claimant_id() -> str:
    """Claimant id fitting ``account_refresh_claims.claimed_by`` (128 chars).

    Overly long bridge instance ids are truncated on the instance-id portion
    only; the per-process suffix is always preserved so two workers sharing one
    instance id can never collapse into the same claimant (which would make the
    re-entrant claim upsert grant both of them the claim concurrently).
    """
    instance_id = get_settings().http_responses_session_bridge_instance_id
    suffix = f":{_PROCESS_SUFFIX}"
    # Reserve room for the per-refresh owner suffix appended at claim time
    # (see ``_compose_claimed_by``) so the composed ``claimed_by`` fits the
    # column without ever truncating the process suffix or the owner token.
    budget = _CLAIMANT_ID_MAX_LEN - len(suffix)
    return f"{instance_id[:budget]}{suffix}"


class RefreshClaimCoordinator:
    """DB-backed per-account refresh claim shared by all replicas."""

    def __init__(self, *, claimant_id: str | None = None) -> None:
        self._claimant_id = claimant_id if claimant_id is not None else default_refresh_claimant_id()

    @property
    def claimant_id(self) -> str:
        return self._claimant_id

    async def try_acquire(self, account_id: str, *, ttl_seconds: float, owner: str) -> bool:
        """Claim ``account_id`` for this claimant's ``owner`` refresh.

        Succeeds when no claim row exists, the existing claim has expired, or
        the existing claim is already ours for the *same* ``owner`` (re-entrant
        refresh after a crash of the previous refresh task in this process).
        Claim ownership is per-refresh: a claim held for a different ``owner``
        (a distinct token fingerprint) — even by this same process — is foreign
        and cannot be taken over until it expires, so two concurrent refreshes
        for one account with different material actually serialize instead of
        one silently piggybacking on the other's claim.
        """
        claimed_by = _compose_claimed_by(self._claimant_id, owner)
        async with sqlite_writer_section():
            for attempt in range(_SQLITE_BUSY_RETRY_ATTEMPTS):
                try:
                    async with get_background_session() as session:
                        now = utcnow()
                        stmt = build_refresh_claim_upsert(
                            dialect_name=session.get_bind().dialect.name,
                            account_id=account_id,
                            claimed_by=claimed_by,
                            now=now,
                            claim_expires_at=now + timedelta(seconds=ttl_seconds),
                        )
                        result = await session.execute(stmt)
                        claimed = result.scalar_one_or_none() is not None
                        await session.commit()
                        return claimed
                except OperationalError as exc:
                    if not _is_sqlite_database_locked(exc) or attempt == _SQLITE_BUSY_RETRY_ATTEMPTS - 1:
                        raise
                    await asyncio.sleep(_SQLITE_BUSY_RETRY_BASE_SECONDS * (2**attempt))
            raise AssertionError("unreachable")

    async def release(self, account_id: str, *, owner: str) -> None:
        """Drop our claim for ``owner``; a foreign claim is left untouched.

        The delete is scoped to the exact composed ``claimed_by`` so releasing
        one refresh's claim can never delete a concurrent refresh's claim for
        the same account held by this process under a different ``owner``.
        """
        claimed_by = _compose_claimed_by(self._claimant_id, owner)
        async with sqlite_writer_section():
            async with get_background_session() as session:
                await session.execute(
                    delete(AccountRefreshClaim).where(
                        AccountRefreshClaim.account_id == account_id,
                        AccountRefreshClaim.claimed_by == claimed_by,
                    )
                )
                await session.commit()

    async def current_claim(self, account_id: str) -> RefreshClaimSnapshot | None:
        async with get_background_session() as session:
            result = await session.execute(
                select(
                    AccountRefreshClaim.claimed_by,
                    AccountRefreshClaim.claimed_at,
                    AccountRefreshClaim.claim_expires_at,
                ).where(AccountRefreshClaim.account_id == account_id)
            )
            row = result.one_or_none()
        if row is None:
            return None
        return RefreshClaimSnapshot(claimed_by=row[0], claimed_at=row[1], claim_expires_at=row[2])


def build_refresh_claim_upsert(
    *,
    dialect_name: str,
    account_id: str,
    claimed_by: str,
    now: datetime,
    claim_expires_at: datetime,
) -> Insert:
    """Conditional claim upsert; RETURNING yields a row iff the claim was won."""
    if dialect_name == "postgresql":
        insert_fn = pg_insert
    elif dialect_name == "sqlite":
        insert_fn = sqlite_insert
    else:
        raise RuntimeError(f"Refresh claims unsupported for dialect={dialect_name!r}")
    return (
        insert_fn(AccountRefreshClaim)
        .values(
            account_id=account_id,
            claimed_by=claimed_by,
            claimed_at=now,
            claim_expires_at=claim_expires_at,
        )
        .on_conflict_do_update(
            index_elements=["account_id"],
            set_={
                "claimed_by": claimed_by,
                "claimed_at": now,
                "claim_expires_at": claim_expires_at,
            },
            where=or_(
                AccountRefreshClaim.claim_expires_at < now,
                AccountRefreshClaim.claimed_by == claimed_by,
            ),
        )
        .returning(AccountRefreshClaim.account_id)
    )


def _is_sqlite_database_locked(exc: OperationalError) -> bool:
    return "database is locked" in str(exc).lower()


# Process-wide default coordinator. ``_default_initialized`` distinguishes
# "not yet initialized" from an explicit override of ``None`` (claims disabled
# — used by the test harness so DB-less unit tests keep exercising the legacy
# flow).
_default_coordinator: RefreshClaimCoordinatorPort | None = None
_default_initialized: bool = False


def get_refresh_claim_coordinator() -> RefreshClaimCoordinatorPort | None:
    global _default_coordinator, _default_initialized
    if not _default_initialized:
        _default_coordinator = RefreshClaimCoordinator()
        _default_initialized = True
    return _default_coordinator


def set_refresh_claim_coordinator(coordinator: RefreshClaimCoordinatorPort | None) -> None:
    """Override the process default (``None`` disables cross-replica claims)."""
    global _default_coordinator, _default_initialized
    _default_coordinator = coordinator
    _default_initialized = True


def reset_refresh_claim_coordinator() -> None:
    global _default_coordinator, _default_initialized
    _default_coordinator = None
    _default_initialized = False

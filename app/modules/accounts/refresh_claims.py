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
import os
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import Float, bindparam, delete, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.sql.elements import TextClause

from app.core.config.settings import get_settings
from app.db.models import AccountRefreshClaim
from app.db.session import get_background_session, sqlite_writer_section

_SQLITE_BUSY_RETRY_ATTEMPTS = 4
_SQLITE_BUSY_RETRY_BASE_SECONDS = 0.05

# The claim TTL lease is evaluated on the DATABASE server clock, not each
# replica's Python wall clock, so inter-replica NTP skew can never let one
# replica treat another's still-live claim as expired and steal it (which would
# let two replicas exchange the same single-use refresh token concurrently --
# the exact reuse race this module exists to prevent). This mirrors the
# clock-domain fix in ``app/core/scheduling/leader_election.py``.
#
# PostgreSQL: the stored ``claim_expires_at`` uses ``clock_timestamp()`` (the
# actual statement-execution time) so a claim that blocked on the row lock
# extends from the CURRENT time; the conflict-update path recomputes it in the
# ``DO UPDATE SET`` clause rather than copying ``excluded.*`` (the ``VALUES``
# tuple was evaluated before the statement blocked). The takeover predicate keeps
# the transaction-snapshot clock (``now()``): a waiter never over-eagerly steals
# a claim refreshed while it was blocked on the lock (``now()`` <=
# ``clock_timestamp()`` is the conservative choice).
_POSTGRES_CLAIM_UPSERT_SQL = text(
    """
    INSERT INTO account_refresh_claims (account_id, claimed_by, claimed_at, claim_expires_at)
    VALUES (:account_id, :claimed_by, clock_timestamp(), clock_timestamp() + make_interval(secs => :ttl))
    ON CONFLICT (account_id) DO UPDATE SET
        claimed_by = excluded.claimed_by,
        claimed_at = clock_timestamp(),
        claim_expires_at = clock_timestamp() + make_interval(secs => :ttl)
    WHERE account_refresh_claims.claim_expires_at < now()
       OR account_refresh_claims.claimed_by = :claimed_by
    RETURNING account_refresh_claims.account_id
    """
).bindparams(bindparam("ttl", type_=Float))

# SQLite evaluates the lease clock inside the statement using its OWN
# execution-time clock (``strftime(..., 'now')``), mirroring the PostgreSQL
# ``clock_timestamp()`` fix: a claim upsert that sat behind SQLite's single-
# writer lock is judged against the CURRENT instant, not a pre-wait Python one.
# ``strftime('%Y-%m-%d %H:%M:%f', 'now')`` renders 3-digit milliseconds;
# ``|| '000'`` pads to the 6-digit-microsecond WIDTH SQLAlchemy's ``DateTime``
# persists so stored strings stay lexicographically comparable across the
# acquire path. SQLite's ``'now'`` is UTC, matching the naive-UTC wall clock
# SQLAlchemy writes. Every ``'now'`` in one statement shares a single
# ``sqlite3_step()`` instant, so the stored expiry and the takeover predicate
# stay in one clock domain.
_SQLITE_NOW = "(strftime('%Y-%m-%d %H:%M:%f', 'now') || '000')"
_SQLITE_NOW_PLUS_TTL = "(strftime('%Y-%m-%d %H:%M:%f', 'now', '+' || :ttl || ' seconds') || '000')"
_SQLITE_CLAIM_UPSERT_SQL = text(
    f"""
    INSERT INTO account_refresh_claims (account_id, claimed_by, claimed_at, claim_expires_at)
    VALUES (:account_id, :claimed_by, {_SQLITE_NOW}, {_SQLITE_NOW_PLUS_TTL})
    ON CONFLICT (account_id) DO UPDATE SET
        claimed_by = excluded.claimed_by,
        claimed_at = {_SQLITE_NOW},
        claim_expires_at = {_SQLITE_NOW_PLUS_TTL}
    WHERE account_refresh_claims.claim_expires_at < {_SQLITE_NOW}
       OR account_refresh_claims.claimed_by = :claimed_by
    RETURNING account_id
    """
).bindparams(bindparam("ttl", type_=Float))

# Per-OS-process claimant suffix. Distinguishes workers/processes that share one
# bridge instance id so a claim is always scoped to exactly one event loop's
# refresh task. It MUST be derived per OS process and resolved lazily -- never
# frozen at module import: in pre-fork deployments (gunicorn/uvicorn --workers
# with a preloaded/imported-before-fork module) the parent imports this module
# once, so a suffix captured at import time is inherited *identically* by every
# forked child. Sibling workers sharing one instance id would then build the
# same ``claimed_by`` string, and the re-entrant claim upsert
# (``claimed_by == claimed_by``) would grant BOTH processes the claim, letting
# them refresh the single-use token concurrently -- exactly the cross-process
# race this module exists to prevent. We therefore combine the OS pid (unique
# per process on a host) with a random component captured lazily on first use in
# the current process, and memoize it keyed on the pid so repeated calls within
# one process stay stable (preserving genuine same-process re-entrant claims)
# while a fork -- which changes ``os.getpid()`` -- forces regeneration. The pid
# handles same-host uniqueness; the instance id plus the post-fork random
# component keep two hosts that happen to reuse an instance id and a pid apart.
_PROCESS_SUFFIX_LOCK = threading.Lock()
_process_suffix: str | None = None
_process_suffix_pid: int | None = None


def _current_process_suffix() -> str:
    """Return this OS process's stable claimant suffix, regenerated after fork.

    Resolved at call time (not at import) and memoized against the current pid,
    so a module preloaded in a parent before forking yields distinct suffixes in
    each child (the child's pid differs from the memoized one, forcing a fresh
    random component) while repeated calls in one process return an identical
    value.
    """
    global _process_suffix, _process_suffix_pid
    pid = os.getpid()
    with _PROCESS_SUFFIX_LOCK:
        if _process_suffix is None or _process_suffix_pid != pid:
            _process_suffix = f"{pid}-{uuid.uuid4().hex[:8]}"
            _process_suffix_pid = pid
        return _process_suffix


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
    only; the per-OS-process suffix (see ``_current_process_suffix``) is always
    preserved so two workers sharing one instance id -- including forked
    children of a preloaded process -- can never collapse into the same claimant
    (which would make the re-entrant claim upsert grant both of them the claim
    concurrently).
    """
    instance_id = get_settings().http_responses_session_bridge_instance_id
    suffix = f":{_current_process_suffix()}"
    # Reserve room for the per-refresh owner suffix appended at claim time
    # (see ``_compose_claimed_by``) so the composed ``claimed_by`` fits the
    # column without ever truncating the process suffix or the owner token.
    budget = _CLAIMANT_ID_MAX_LEN - len(suffix)
    return f"{instance_id[:budget]}{suffix}"


class RefreshClaimCoordinator:
    """DB-backed per-account refresh claim shared by all replicas."""

    def __init__(self, *, claimant_id: str | None = None) -> None:
        # An explicitly injected claimant id is frozen for the coordinator's
        # lifetime (callers that pass one own its stability). The process-default
        # / auto-derived id, by contrast, MUST NOT be frozen at construction:
        # in pre-fork deployments the process-default coordinator is often built
        # (via ``get_refresh_claim_coordinator()``) during preload/startup,
        # BEFORE the server forks its workers. A frozen id would then be
        # inherited *identically* by every forked child, so sibling workers
        # would compose the same ``claimed_by`` and both satisfy the re-entrant
        # claim upsert (``claimed_by == claimed_by``), refreshing the single-use
        # token concurrently -- the exact cross-process race this module
        # prevents. We therefore resolve the auto-derived id lazily on each use
        # via ``default_refresh_claimant_id()``, whose per-OS-process suffix is
        # memoized against ``os.getpid()`` (see ``_current_process_suffix``): a
        # forked child's differing pid forces a fresh, distinct id while repeated
        # calls within one process stay stable (preserving genuine same-process
        # re-entrant claims).
        self._explicit_claimant_id = claimant_id

    @property
    def claimant_id(self) -> str:
        if self._explicit_claimant_id is not None:
            return self._explicit_claimant_id
        return default_refresh_claimant_id()

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
        claimed_by = _compose_claimed_by(self.claimant_id, owner)
        async with sqlite_writer_section():
            for attempt in range(_SQLITE_BUSY_RETRY_ATTEMPTS):
                try:
                    async with get_background_session() as session:
                        stmt = build_refresh_claim_upsert(dialect_name=session.get_bind().dialect.name)
                        result = await session.execute(
                            stmt,
                            {
                                "account_id": account_id,
                                "claimed_by": claimed_by,
                                "ttl": float(ttl_seconds),
                            },
                        )
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
        claimed_by = _compose_claimed_by(self.claimant_id, owner)
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


def build_refresh_claim_upsert(*, dialect_name: str) -> TextClause:
    """Conditional claim upsert; RETURNING yields a row iff the claim was won.

    The statement binds ``account_id``, ``claimed_by`` and ``ttl`` and evaluates
    BOTH the stored ``claim_expires_at`` AND the takeover predicate on the
    DATABASE server clock (``clock_timestamp()``/``now()`` on PostgreSQL,
    in-statement ``strftime(..., 'now')`` on SQLite), so the "exactly one replica
    exchanges" guarantee no longer depends on inter-replica wall-clock skew
    staying below the TTL.
    """
    if dialect_name == "postgresql":
        return _POSTGRES_CLAIM_UPSERT_SQL
    if dialect_name == "sqlite":
        return _SQLITE_CLAIM_UPSERT_SQL
    raise RuntimeError(f"Refresh claims unsupported for dialect={dialect_name!r}")


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

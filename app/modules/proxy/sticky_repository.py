from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Insert

from app.core.utils.time import naive_utc_to_epoch, to_utc_naive, utcnow
from app.db.models import Account, AccountStatus, StickySession, StickySessionKind
from app.db.session import sqlite_writer_section
from app.modules.sticky_sessions.schemas import StickySessionSortBy, StickySessionSortDir

# Each (key, kind) pair in delete_entries contributes 2 bind parameters to
# the underlying DELETE...OR (key=:k AND kind=:t)... statement. SQLite's
# default SQLITE_LIMIT_VARIABLE_NUMBER is 999 on builds older than 3.32
# and 32766 on newer builds, so chunking conservatively at 250 pairs
# (500 bind parameters) keeps delete-filtered safe on any libsqlite that
# ships with current Python interpreters. Postgres allows up to 65535
# bind parameters, which this chunk size also respects.
_DELETE_ENTRIES_CHUNK_SIZE = 250

_ContinuitySource = Literal["session_header", "thread_header", "turn_state"]
_SESSION_HEADER_ABANDONMENT_SCOPE = "session_header"

# A same-owner TTL refresh upsert only rewrites ``updated_at``. On hot
# (key, kind) rows, concurrent requests serialize on that row lock, so the
# selection path may skip the rewrite while the row is younger than this
# window, revalidating the observed deadline at write time. The window is
# bounded to at most 1% of the mapping TTL (so expiry moves by at most 1% of
# the window it protects) and to a small absolute ceiling; a rebind to a
# different owner, a row carrying any abandonment marker, or a row stamped in
# the future is never skippable because those writes change state beyond
# freshness (or the observation itself is untrustworthy).
_REFRESH_SKIP_TTL_FRACTION = 0.01
_REFRESH_SKIP_MAX_SECONDS = 15.0

# Only the Live-call ownership namespace is reserved. Other LF-prefixed keys
# (e.g. the pre-existing "\ncodex-lb-affinity-v1" selection affinities) remain
# ordinary operator-manageable sessions.
RESERVED_STICKY_SESSION_KEY_PREFIX = "\ncodex_live_call:"


def is_reserved_sticky_session_key(key: str) -> bool:
    return key.startswith(RESERVED_STICKY_SESSION_KEY_PREFIX)


@dataclass(frozen=True, slots=True)
class StickySessionListEntryRecord:
    sticky_session: StickySession
    display_name: str


@dataclass(frozen=True, slots=True)
class StickyOwnerLookup:
    """Result of resolving a mapping's owner, accessed by attribute (never
    unpacked) so an un-configured test double for ``sticky_sessions`` degrades
    to its existing safe defaults (no owner, not abandoned) instead of a hard
    crash on tuple destructuring."""

    account_id: str | None
    continuity_abandoned: bool
    # Source-qualified abandonment makes account_id ownerless only for the
    # matching source, but selection must still remember which durable owner
    # was retired. Global stale-hard tombstones leave this unset because their
    # established recovery path may legitimately reselect a recovered owner.
    abandoned_account_id: str | None = None
    # Set only when the row was observed in this lookup with a fresh
    # ``updated_at`` (within the refresh-skip window derived from
    # ``max_age_seconds``) and no abandonment marker, so a same-owner TTL
    # refresh upsert would be a pure ``updated_at`` rewrite. The value is the
    # naive-UTC instant (``observed_updated_at`` + skip window) after which
    # the skip is no longer valid; consumers must isinstance-check
    # ``datetime`` (test doubles may auto-vivify attributes), must revalidate
    # the deadline against the clock immediately before omitting the write,
    # and must never skip a write that changes the owner account.
    refresh_skip_deadline: datetime | None = None


def _continuity_is_abandoned_for_source(
    abandoned_at: datetime | None,
    abandonment_scope: str | None,
    continuity_source: _ContinuitySource | None,
) -> bool:
    if abandonment_scope is not None:
        # A scope is itself the source-qualified marker. Goal-restart writers
        # deliberately leave the legacy timestamp NULL so pre-scope binaries
        # keep treating account_id as hard ownership during rollout/rollback.
        # Unknown and nonmatching typed callers likewise fail closed.
        return abandonment_scope == continuity_source
    # Historical stale-hard tombstones have a timestamp and NULL scope, and
    # therefore continue to abandon ownership globally for every source.
    return abandoned_at is not None


def _source_scoped_abandoned_account_id(
    account_id: str,
    abandonment_scope: str | None,
    continuity_source: _ContinuitySource | None,
) -> str | None:
    if abandonment_scope is not None and abandonment_scope == continuity_source:
        return account_id
    return None


def _owner_lookup_from_row(
    row: StickySession,
    *,
    continuity_source: _ContinuitySource | None,
    refresh_skip_deadline: datetime | None = None,
) -> StickyOwnerLookup:
    if _continuity_is_abandoned_for_source(
        row.continuity_abandoned_at,
        row.continuity_abandonment_scope,
        continuity_source,
    ):
        return StickyOwnerLookup(
            account_id=None,
            continuity_abandoned=True,
            abandoned_account_id=_source_scoped_abandoned_account_id(
                row.account_id,
                row.continuity_abandonment_scope,
                continuity_source,
            ),
        )
    return StickyOwnerLookup(
        account_id=row.account_id,
        continuity_abandoned=False,
        refresh_skip_deadline=refresh_skip_deadline,
    )


def _same_owner_refresh_skip_deadline(
    row: StickySession,
    *,
    observed_updated_at: datetime,
    now: datetime,
    max_age_seconds: int,
) -> datetime | None:
    """Deadline until which a same-owner upsert of this row stays skippable.

    Any abandonment marker disqualifies the skip: an upsert re-establishes
    ownership by clearing both marker columns, so that write is semantic even
    when the owner account is unchanged. A row whose ``updated_at`` sits in
    the future (database clock ahead of this process, or a restored row) is
    also never skippable: an upper-bound-only age comparison would let such a
    row satisfy the window for longer than the documented bound.
    """

    if row.continuity_abandoned_at is not None or row.continuity_abandonment_scope is not None:
        return None
    age_seconds = (now - observed_updated_at).total_seconds()
    if age_seconds < 0:
        return None
    skip_window_seconds = min(_REFRESH_SKIP_MAX_SECONDS, max_age_seconds * _REFRESH_SKIP_TTL_FRACTION)
    if age_seconds > skip_window_seconds:
        return None
    return observed_updated_at + timedelta(seconds=skip_window_seconds)


class StickySessionsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_account_id(
        self,
        key: str,
        *,
        kind: StickySessionKind,
        max_age_seconds: int | None = None,
        continuity_source: _ContinuitySource | None = None,
    ) -> str | None:
        lookup = await self.get_account_id_and_abandonment(
            key,
            kind=kind,
            max_age_seconds=max_age_seconds,
            continuity_source=continuity_source,
        )
        return lookup.account_id

    async def get_account_id_and_abandonment(
        self,
        key: str,
        *,
        kind: StickySessionKind,
        max_age_seconds: int | None = None,
        continuity_source: _ContinuitySource | None = None,
    ) -> StickyOwnerLookup:
        """Resolve a mapping's owner and applicable abandonment marker.

        Global stale-hard tombstones remain ownerless for every source. A
        source-scoped marker is ownerless only for its matching typed source;
        explicit turn-state and unknown callers retain the stored owner when a
        goal restart abandoned only session-header interpretation.
        """
        if not key:
            return StickyOwnerLookup(account_id=None, continuity_abandoned=False)
        row = await self.get_entry(key, kind=kind)
        if row is None:
            return StickyOwnerLookup(account_id=None, continuity_abandoned=False)
        if max_age_seconds is None:
            return _owner_lookup_from_row(row, continuity_source=continuity_source)
        now = utcnow()
        cutoff = now - timedelta(seconds=max_age_seconds)
        observed_updated_at = to_utc_naive(row.updated_at)
        if observed_updated_at >= cutoff:
            return _owner_lookup_from_row(
                row,
                continuity_source=continuity_source,
                refresh_skip_deadline=_same_owner_refresh_skip_deadline(
                    row,
                    observed_updated_at=observed_updated_at,
                    now=now,
                    max_age_seconds=max_age_seconds,
                ),
            )

        # Release the read snapshot before attempting a SQLite write upgrade.
        # The DELETE remains safe because every value observed above participates
        # in the predicate; a concurrent rebind therefore wins the comparison.
        await self._session.commit()
        statement = (
            delete(StickySession)
            .where(
                StickySession.key == key,
                StickySession.kind == kind,
                StickySession.account_id == row.account_id,
                StickySession.updated_at == observed_updated_at,
                StickySession.updated_at < cutoff,
            )
            .returning(StickySession.key)
        )
        current: tuple[str, datetime, datetime | None, str | None] | None = None
        async with sqlite_writer_section():
            deleted_key = (await self._session.execute(statement)).scalar_one_or_none()
            if deleted_key is None:
                current = (
                    (
                        await self._session.execute(
                            select(
                                StickySession.account_id,
                                StickySession.updated_at,
                                StickySession.continuity_abandoned_at,
                                StickySession.continuity_abandonment_scope,
                            ).where(
                                StickySession.key == key,
                                StickySession.kind == kind,
                            )
                        )
                    )
                    .tuples()
                    .one_or_none()
                )
            await self._session.commit()

        if deleted_key is not None or current is None:
            return StickyOwnerLookup(account_id=None, continuity_abandoned=False)
        (
            current_account_id,
            current_updated_at,
            current_continuity_abandoned_at,
            current_continuity_abandonment_scope,
        ) = current
        if to_utc_naive(current_updated_at) < cutoff:
            return StickyOwnerLookup(account_id=None, continuity_abandoned=False)
        if _continuity_is_abandoned_for_source(
            current_continuity_abandoned_at,
            current_continuity_abandonment_scope,
            continuity_source,
        ):
            return StickyOwnerLookup(
                account_id=None,
                continuity_abandoned=True,
                abandoned_account_id=_source_scoped_abandoned_account_id(
                    current_account_id,
                    current_continuity_abandonment_scope,
                    continuity_source,
                ),
            )
        return StickyOwnerLookup(account_id=current_account_id, continuity_abandoned=False)

    async def release_read_snapshot(self) -> None:
        """End the session's current read transaction.

        On the default SQLite/WAL configuration one transaction pins one read
        snapshot at its first SELECT, so a session shared across successive
        ownership lookups would leave every later lookup blind to owners
        committed concurrently after the first read. Committing ends that
        snapshot so the next SELECT begins a fresh transaction; on PostgreSQL
        READ COMMITTED each statement already reads fresh committed state, so
        this is a near-free no-op. COMMIT (not rollback) on purpose: rollback
        expires all tracked ORM state regardless of ``expire_on_commit``,
        while commit under the session factory's ``expire_on_commit=False``
        keeps rows loaded by earlier lookups readable.
        """
        await self._session.commit()

    async def get_entry(self, key: str, *, kind: StickySessionKind) -> StickySession | None:
        if not key:
            return None
        statement = select(StickySession).where(
            StickySession.key == key,
            StickySession.kind == kind,
        )
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def upsert(self, key: str, account_id: str, *, kind: StickySessionKind) -> StickySession:
        # RETURNING collapses the previous upsert + re-select + refresh
        # (4 round trips) into one statement; this runs inline before the
        # first upstream byte on sticky requests, so round trips are TTFT.
        # populate_existing forces the returned row to overwrite any stale
        # identity-map instance the session may already hold for this key.
        statement = self._build_upsert_statement(key, account_id, kind).returning(StickySession)
        async with sqlite_writer_section():
            result = await self._session.execute(statement, execution_options={"populate_existing": True})
            row = result.scalar_one_or_none()
            await self._session.commit()
        if row is None:
            raise RuntimeError(f"StickySession upsert failed for key={key!r} kind={kind.value!r}")
        return row

    async def insert_if_absent(self, key: str, account_id: str, kind: StickySessionKind) -> str:
        """Insert immutable ownership and return the persisted owner."""

        statement = self._build_insert_do_nothing_statement(key, account_id, kind).returning(StickySession.account_id)
        async with sqlite_writer_section():
            result = await self._session.execute(statement)
            owner_id = result.scalar_one_or_none()
            if owner_id is None:
                owner_id = await self._session.scalar(
                    select(StickySession.account_id).where(
                        StickySession.key == key,
                        StickySession.kind == kind,
                    )
                )
            await self._session.commit()
        if owner_id is None:
            raise RuntimeError("StickySession immutable insert did not resolve an owner")
        return owner_id

    async def upsert_with_seed_if_absent(
        self,
        key: str,
        account_id: str,
        *,
        kind: StickySessionKind,
        seed_key: str,
        seed_kind: StickySessionKind,
    ) -> StickySession:
        """Upsert one mapping and initialize its immutable seed atomically."""

        # Keep these writes in one transaction. A process seed without the
        # initiating thread row is false placement evidence, while a thread
        # row without its seed makes the first admitted thread invisible to
        # later siblings. Do not replace the seed's DO NOTHING with an upsert:
        # another thread may have won first-writer initialization already.
        seed_statement = self._build_insert_do_nothing_statement(seed_key, account_id, seed_kind)
        mapping_statement = self._build_upsert_statement(key, account_id, kind).returning(StickySession)
        async with sqlite_writer_section():
            try:
                await self._session.execute(seed_statement)
                result = await self._session.execute(
                    mapping_statement,
                    execution_options={"populate_existing": True},
                )
                row = result.scalar_one_or_none()
                if row is None:
                    raise RuntimeError(f"StickySession seeded upsert failed for key={key!r} kind={kind.value!r}")
                await self._session.commit()
            except BaseException:
                # This method owns both writes as one unit even when a caller
                # catches the error and keeps using the same session.
                await self._session.rollback()
                raise
        return row

    async def delete(self, key: str, *, kind: StickySessionKind) -> bool:
        if not key:
            return False
        statement = delete(StickySession).where(
            StickySession.key == key,
            StickySession.kind == kind,
        )
        async with sqlite_writer_section():
            result = await self._session.execute(statement.returning(StickySession.key))
            await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def abandon_legacy_session_header_owner_if_unavailable(
        self,
        key: str,
        *,
        kind: StickySessionKind,
        expected_account_id: str,
    ) -> bool:
        """Abandon only session-header interpretation of an unavailable raw owner."""

        if not key or not expected_account_id:
            return False
        unavailable_statuses = (
            AccountStatus.PAUSED,
            AccountStatus.RATE_LIMITED,
            AccountStatus.QUOTA_EXCEEDED,
        )
        # PostgreSQL evaluates the status subquery from the UPDATE statement's
        # snapshot. Without first locking the Account row, a concurrent status
        # recovery can commit while that statement waits for the StickySession
        # row and the stale snapshot can still authorize a tombstone. Locking
        # the status owner makes recovery and retirement serialize; the sticky
        # owner predicate below independently keeps concurrent rebinds safe.
        owner_status_lock = select(Account.status).where(Account.id == expected_account_id).with_for_update()
        # Retain account status inside the UPDATE as a second, database-level
        # invariant. The lock is the concurrency guarantee; this predicate
        # prevents future refactors from turning a prior status observation
        # into unconditional retirement.
        unavailable_owner = select(Account.id).where(
            Account.id == expected_account_id,
            Account.status.in_(unavailable_statuses),
        )
        statement = (
            update(StickySession)
            .where(
                StickySession.key == key,
                StickySession.kind == kind,
                StickySession.account_id == expected_account_id,
                StickySession.continuity_abandoned_at.is_(None),
                StickySession.continuity_abandonment_scope.is_(None),
                StickySession.account_id.in_(unavailable_owner),
            )
            # The scope column is the new reader's marker. Keep the legacy
            # timestamp NULL: older replicas know only that timestamp, so they
            # continue to treat account_id as hard ownership instead of
            # globally abandoning and rebinding a colliding explicit turn
            # state. New readers use typed scope, never key shape, to decide
            # which source may ignore the retained owner.
            .values(
                updated_at=func.now(),
                continuity_abandoned_at=None,
                continuity_abandonment_scope=_SESSION_HEADER_ABANDONMENT_SCOPE,
            )
            .returning(StickySession.key)
        )
        async with sqlite_writer_section():
            owner_status = await self._session.scalar(owner_status_lock)
            if owner_status not in unavailable_statuses:
                await self._session.commit()
                return False
            result = await self._session.execute(statement)
            await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def restore_if_current(
        self,
        key: str,
        *,
        kind: StickySessionKind,
        expected_account_id: str | None,
        restore_account_id: str | None,
    ) -> bool:
        """Restore a sticky owner only if the provisional owner is still current."""

        if not key:
            return False
        if expected_account_id is None:
            if restore_account_id is None:
                return True
            statement = self._build_insert_do_nothing_statement(key, restore_account_id, kind).returning(
                StickySession.key
            )
        elif restore_account_id is None:
            statement = (
                delete(StickySession)
                .where(
                    StickySession.key == key,
                    StickySession.kind == kind,
                    StickySession.account_id == expected_account_id,
                )
                .returning(StickySession.key)
            )
        else:
            statement = (
                update(StickySession)
                .where(
                    StickySession.key == key,
                    StickySession.kind == kind,
                    StickySession.account_id == expected_account_id,
                )
                .values(
                    account_id=restore_account_id,
                    updated_at=func.now(),
                    continuity_abandoned_at=None,
                    continuity_abandonment_scope=None,
                )
                .returning(StickySession.key)
            )

        async with sqlite_writer_section():
            result = await self._session.execute(statement)
            await self._session.commit()
        return result.scalar_one_or_none() is not None

    async def delete_entries(
        self,
        entries: Sequence[tuple[str, StickySessionKind]],
    ) -> list[tuple[str, StickySessionKind]]:
        targets = {(key, kind) for key, kind in entries if key}
        if not targets:
            return []

        deleted: list[tuple[str, StickySessionKind]] = []
        targets_list = list(targets)
        for offset in range(0, len(targets_list), _DELETE_ENTRIES_CHUNK_SIZE):
            chunk = targets_list[offset : offset + _DELETE_ENTRIES_CHUNK_SIZE]
            statement = delete(StickySession).where(
                or_(*(and_(StickySession.key == key, StickySession.kind == kind) for key, kind in chunk))
            )
            async with sqlite_writer_section():
                result = await self._session.execute(statement.returning(StickySession.key, StickySession.kind))
                await self._session.commit()
            deleted.extend((key, kind) for key, kind in result.all())
        return deleted

    async def list_entry_identifiers(
        self,
        *,
        kind: StickySessionKind | None = None,
        updated_before: datetime | None = None,
        account_query: str | None = None,
        key_query: str | None = None,
    ) -> list[tuple[str, StickySessionKind]]:
        statement = (
            self._apply_filters(
                select(StickySession.key, StickySession.kind),
                kind=kind,
                updated_before=updated_before,
                account_query=account_query,
                key_query=key_query,
            )
            .join(Account, Account.id == StickySession.account_id)
            .order_by(
                StickySession.updated_at.desc(),
                StickySession.created_at.desc(),
                StickySession.key.asc(),
            )
        )
        result = await self._session.execute(statement)
        return [(key, kind) for key, kind in result.all()]

    async def list_entries(
        self,
        *,
        kind: StickySessionKind | None = None,
        updated_before: datetime | None = None,
        account_query: str | None = None,
        key_query: str | None = None,
        sort_by: StickySessionSortBy = "updated_at",
        sort_dir: StickySessionSortDir = "desc",
        offset: int = 0,
        limit: int | None = None,
    ) -> Sequence[StickySessionListEntryRecord]:
        order_by = self._build_order_by(sort_by=sort_by, sort_dir=sort_dir)
        statement = (
            self._apply_filters(
                select(StickySession, Account.email),
                kind=kind,
                updated_before=updated_before,
                account_query=account_query,
                key_query=key_query,
            )
            .join(Account, Account.id == StickySession.account_id)
            .order_by(*order_by)
        )
        if offset > 0:
            statement = statement.offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        result = await self._session.execute(statement)
        return [
            StickySessionListEntryRecord(sticky_session=sticky_session, display_name=display_name)
            for sticky_session, display_name in result.all()
        ]

    async def count_entries(
        self,
        *,
        kind: StickySessionKind | None = None,
        updated_before: datetime | None = None,
        account_query: str | None = None,
        key_query: str | None = None,
    ) -> int:
        statement = self._apply_filters(
            select(func.count()).select_from(StickySession).join(Account, Account.id == StickySession.account_id),
            kind=kind,
            updated_before=updated_before,
            account_query=account_query,
            key_query=key_query,
        )
        result = await self._session.execute(statement)
        return int(result.scalar_one())

    async def purge_prompt_cache_before(self, cutoff: datetime) -> int:
        return await self.purge_before(cutoff, kind=StickySessionKind.PROMPT_CACHE)

    async def purge_before(self, cutoff: datetime, *, kind: StickySessionKind | None = None) -> int:
        stmt = delete(StickySession).where(StickySession.updated_at < to_utc_naive(cutoff))
        if kind is not None:
            stmt = stmt.where(StickySession.kind == kind)
        async with sqlite_writer_section():
            result = await self._session.execute(stmt.returning(StickySession.key))
            deleted = len(result.scalars().all())
            await self._session.commit()
        return deleted

    async def purge_before_for_key_prefix(
        self,
        cutoff: datetime,
        *,
        kind: StickySessionKind,
        key_prefix: str,
        limit: int = _DELETE_ENTRIES_CHUNK_SIZE,
    ) -> int:
        """Delete one bounded batch from a reserved key namespace."""

        if limit <= 0:
            return 0
        target_keys = (
            select(StickySession.key)
            .where(
                StickySession.kind == kind,
                StickySession.key.startswith(key_prefix, autoescape=True),
                StickySession.updated_at < to_utc_naive(cutoff),
            )
            .order_by(StickySession.updated_at.asc(), StickySession.key.asc())
            .limit(limit)
        )
        stmt = delete(StickySession).where(
            StickySession.kind == kind,
            StickySession.key.in_(target_keys),
        )
        async with sqlite_writer_section():
            result = await self._session.execute(stmt.returning(StickySession.key))
            deleted = len(result.scalars().all())
            await self._session.commit()
        return deleted

    async def purge_stale_hard_codex_session_mappings(self, cutoff: datetime, *, now: datetime) -> int:
        """Retire CODEX_SESSION mappings pinned to a durably unusable owner.

        A hard `codex_session` mapping is never rebound by ordinary selection
        (see load_balancer.py's hard_sticky branch) even once its owner is
        rate-limited/quota-exceeded/paused, because that pin can represent
        live, unverifiable session state that isn't safe to move mid-flight.
        That correctly protects a transient blip, but leaves the mapping
        stuck forever if the owner never recovers.

        ``Account.reset_at`` is frequently absent, while ``blocked_at`` is
        cleared when an account is paused, so neither field provides one
        durable outage clock for every unavailable status. Instead,
        ``AccountsRepository`` refreshes the mapping timestamp exactly when
        its owner transitions from an available status into one of the
        unavailable statuses below. ``StickySession.updated_at`` therefore
        records the later of the mapping's last use and the outage start.
        Only once BOTH the owner is still non-active AND that conservative
        timestamp is before ``cutoff`` do we give up on the mapping.

        When ``Account.reset_at`` is known and still in the future (e.g. a
        multi-day quota window), the owner's own stated recovery point takes
        priority over the flat cutoff: the mapping survives until after
        ``reset_at`` even if it has long since gone stale by the cutoff
        alone, since purging before an account's own known recovery time
        would contradict "well past its own recovery point". ``reset_at``
        only ever narrows eligibility (delays a purge); it never widens it
        when unset, which is why the fixed cutoff remains the fallback.

        Giving up is done in two phases, never by an outright delete on the
        first pass:

        1. Tombstone: set ``continuity_abandoned_at`` instead of deleting.
           A `conversation`-continuity request has no owner index besides
           this row (see affinity.py's ``require_unambiguous_account``), so
           an outright delete would be indistinguishable from "this key was
           never seen" — and with more than one account in the pool, that
           makes ``run_sticky_selection_path`` fail closed forever, even
           after the original owner recovers, because nothing on that path
           can ever re-create the very row it needs to stop failing closed.
           A tombstone instead lets selection recognize "this key's
           continuity was deliberately abandoned, picking a fresh owner is
           authorized", so a subsequent request can escape the stuck state.
        2. Delete: once a tombstone has sat for a further ``cutoff`` window
           with nobody claiming it (i.e. it's still a tombstone, so no
           request re-pinned it), it's dropped outright. A fresh request for
           that key then falls back to the same conservative fail-closed
           default as a key that was never seen, which is fine this long
           after the fact.
        """
        now_epoch = naive_utc_to_epoch(to_utc_naive(now))
        unavailable_account_ids = select(Account.id).where(
            Account.status.in_((AccountStatus.PAUSED, AccountStatus.RATE_LIMITED, AccountStatus.QUOTA_EXCEEDED)),
            or_(Account.reset_at.is_(None), Account.reset_at < now_epoch),
        )
        cutoff_naive = to_utc_naive(cutoff)
        tombstone_stmt = (
            update(StickySession)
            .where(
                StickySession.kind == StickySessionKind.CODEX_SESSION,
                or_(
                    StickySession.continuity_abandoned_at.is_(None),
                    StickySession.continuity_abandonment_scope.is_not(None),
                ),
                StickySession.updated_at < cutoff_naive,
                StickySession.account_id.in_(unavailable_account_ids),
            )
            # Stale-hard cleanup is global. It may promote a younger
            # session-header-only marker once the original row itself crosses
            # the normal stale-hard threshold.
            .values(
                continuity_abandoned_at=to_utc_naive(now),
                continuity_abandonment_scope=None,
            )
        )
        delete_stmt = delete(StickySession).where(
            StickySession.kind == StickySessionKind.CODEX_SESSION,
            StickySession.continuity_abandoned_at.is_not(None),
            StickySession.continuity_abandonment_scope.is_(None),
            StickySession.continuity_abandoned_at < cutoff_naive,
        )
        async with sqlite_writer_section():
            tombstoned_result = await self._session.execute(tombstone_stmt.returning(StickySession.key))
            tombstoned = len(tombstoned_result.scalars().all())
            deleted_result = await self._session.execute(delete_stmt.returning(StickySession.key))
            deleted = len(deleted_result.scalars().all())
            await self._session.commit()
        return tombstoned + deleted

    def _build_upsert_statement(self, key: str, account_id: str, kind: StickySessionKind) -> Insert:
        dialect = self._session.get_bind().dialect.name
        if dialect == "postgresql":
            insert_fn = pg_insert
        elif dialect == "sqlite":
            insert_fn = sqlite_insert
        else:
            raise RuntimeError(f"StickySession upsert unsupported for dialect={dialect!r}")
        statement = insert_fn(StickySession).values(key=key, account_id=account_id, kind=kind)
        return statement.on_conflict_do_update(
            index_elements=[StickySession.key, StickySession.kind],
            set_={
                "account_id": account_id,
                "updated_at": func.now(),
                # A fresh pin fully re-establishes ownership, so any earlier
                # purge tombstone (see purge_stale_hard_codex_session_mappings)
                # no longer applies — otherwise this row would keep reporting
                # itself as abandoned even though it now has a live owner.
                "continuity_abandoned_at": None,
                "continuity_abandonment_scope": None,
            },
        )

    def _build_insert_do_nothing_statement(self, key: str, account_id: str, kind: StickySessionKind) -> Insert:
        dialect = self._session.get_bind().dialect.name
        if dialect == "postgresql":
            insert_fn = pg_insert
        elif dialect == "sqlite":
            insert_fn = sqlite_insert
        else:
            raise RuntimeError(f"StickySession insert unsupported for dialect={dialect!r}")
        statement = insert_fn(StickySession).values(key=key, account_id=account_id, kind=kind)
        return statement.on_conflict_do_nothing(index_elements=[StickySession.key, StickySession.kind])

    @staticmethod
    def _apply_filters(
        statement,
        *,
        kind: StickySessionKind | None,
        updated_before: datetime | None,
        account_query: str | None,
        key_query: str | None,
    ):
        statement = statement.where(~StickySession.key.startswith(RESERVED_STICKY_SESSION_KEY_PREFIX, autoescape=True))
        if kind is not None:
            statement = statement.where(StickySession.kind == kind)
        if updated_before is not None:
            statement = statement.where(StickySession.updated_at < to_utc_naive(updated_before))
        if account_query:
            statement = statement.where(func.lower(Account.email).contains(account_query.lower()))
        if key_query:
            statement = statement.where(func.lower(StickySession.key).contains(key_query.lower()))
        return statement

    @staticmethod
    def _build_order_by(
        *,
        sort_by: StickySessionSortBy,
        sort_dir: StickySessionSortDir,
    ):
        sort_column_map = {
            "updated_at": StickySession.updated_at,
            "created_at": StickySession.created_at,
            "account": Account.email,
            "key": StickySession.key,
        }
        primary = sort_column_map[sort_by]
        primary_order = primary.asc() if sort_dir == "asc" else primary.desc()
        if sort_by == "updated_at":
            return (
                primary_order,
                StickySession.created_at.desc(),
                StickySession.key.asc(),
            )
        if sort_by == "created_at":
            return (
                primary_order,
                StickySession.updated_at.desc(),
                StickySession.key.asc(),
            )
        if sort_by == "account":
            return (
                primary_order,
                StickySession.updated_at.desc(),
                StickySession.key.asc(),
            )
        return (
            primary_order,
            StickySession.updated_at.desc(),
            StickySession.created_at.desc(),
        )

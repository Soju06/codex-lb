from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

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


def _owner_lookup_from_row(row: StickySession) -> StickyOwnerLookup:
    if row.continuity_abandoned_at is not None:
        return StickyOwnerLookup(account_id=None, continuity_abandoned=True)
    return StickyOwnerLookup(account_id=row.account_id, continuity_abandoned=False)


class StickySessionsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_account_id(
        self,
        key: str,
        *,
        kind: StickySessionKind,
        max_age_seconds: int | None = None,
    ) -> str | None:
        lookup = await self.get_account_id_and_abandonment(key, kind=kind, max_age_seconds=max_age_seconds)
        return lookup.account_id

    async def get_account_id_and_abandonment(
        self,
        key: str,
        *,
        kind: StickySessionKind,
        max_age_seconds: int | None = None,
    ) -> StickyOwnerLookup:
        """Resolve a mapping's owner, and whether it's a purge tombstone.

        A tombstoned row (``continuity_abandoned_at`` set — see
        ``purge_stale_hard_codex_session_mappings``) is deliberately reported
        as ownerless here, same as a missing row, so every existing caller of
        ``get_account_id`` keeps treating it as "no live pin" without change.
        The extra flag lets ``run_sticky_selection_path`` additionally
        distinguish "this key was purged" from "this key was never seen",
        which matters only for the `conversation`-continuity ambiguous-owner
        check that has no other index to fall back on.
        """
        if not key:
            return StickyOwnerLookup(account_id=None, continuity_abandoned=False)
        row = await self.get_entry(key, kind=kind)
        if row is None:
            return StickyOwnerLookup(account_id=None, continuity_abandoned=False)
        if max_age_seconds is None:
            return _owner_lookup_from_row(row)
        cutoff = utcnow() - timedelta(seconds=max_age_seconds)
        observed_updated_at = to_utc_naive(row.updated_at)
        if observed_updated_at >= cutoff:
            return _owner_lookup_from_row(row)

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
        current: tuple[str, datetime, datetime | None] | None = None
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
        current_account_id, current_updated_at, current_continuity_abandoned_at = current
        if to_utc_naive(current_updated_at) < cutoff:
            return StickyOwnerLookup(account_id=None, continuity_abandoned=False)
        if current_continuity_abandoned_at is not None:
            return StickyOwnerLookup(account_id=None, continuity_abandoned=True)
        return StickyOwnerLookup(account_id=current_account_id, continuity_abandoned=False)

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
                .values(account_id=restore_account_id, updated_at=func.now(), continuity_abandoned_at=None)
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
                StickySession.continuity_abandoned_at.is_(None),
                StickySession.updated_at < cutoff_naive,
                StickySession.account_id.in_(unavailable_account_ids),
            )
            .values(continuity_abandoned_at=to_utc_naive(now))
        )
        delete_stmt = delete(StickySession).where(
            StickySession.kind == StickySessionKind.CODEX_SESSION,
            StickySession.continuity_abandoned_at.is_not(None),
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

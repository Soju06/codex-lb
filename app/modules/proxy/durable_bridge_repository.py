from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256

from sqlalchemy import Row, and_, case, delete, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time import to_utc_naive, utcnow
from app.db.models import HttpBridgeSessionAlias, HttpBridgeSessionRecord, HttpBridgeSessionState
from app.db.session import sqlite_writer_section
from app.modules.proxy.continuity import (
    HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KEY_PREFIX,
    HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KIND,
    HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_REBINDABLE_KINDS,
    is_http_bridge_account_neutral_replay,
)

_ANONYMOUS_API_KEY_SCOPE = "__anonymous__"
REQUIRED_DURABLE_BRIDGE_TABLES = (
    "http_bridge_sessions",
    "http_bridge_session_aliases",
)
_PURGE_CLOSED_BATCH_SIZE = 500
_SESSION_ID_LOOKUP_CHUNK_SIZE = 500


class DurableBridgeAliasRegistration(StrEnum):
    REGISTERED = "registered"
    OWNER_FENCED = "owner_fenced"
    ALIAS_PROTECTED = "alias_protected"


@dataclass(frozen=True, slots=True)
class DurableBridgeAliasRegistrationReceipt:
    """Rollback data for a continuity alias published before dispatch."""

    status: DurableBridgeAliasRegistration
    session_id: str
    api_key_scope: str
    alias_kind: str
    alias_value: str
    instance_id: str
    owner_epoch: int
    previous_alias_session_id: str | None
    previous_alias_owner_epoch: int | None
    previous_alias_account_id: str | None
    previous_latest_turn_state: str | None


def durable_bridge_api_key_scope(api_key_id: str | None) -> str:
    if api_key_id is None:
        return _ANONYMOUS_API_KEY_SCOPE
    stripped = api_key_id.strip()
    return stripped or _ANONYMOUS_API_KEY_SCOPE


def durable_bridge_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class DurableBridgeSessionSnapshot:
    id: str
    session_key_kind: str
    session_key_value: str
    session_key_hash: str
    api_key_scope: str
    owner_instance_id: str | None
    owner_epoch: int
    lease_expires_at: datetime | None
    state: HttpBridgeSessionState
    account_id: str | None
    model: str | None
    service_tier: str | None
    latest_turn_state: str | None
    latest_response_id: str | None
    latest_input_item_count: int | None
    latest_input_full_fingerprint: str | None
    closed_at: datetime | None


class DurableBridgeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _commit_writer_section(self) -> None:
        async with sqlite_writer_section():
            await self._session.commit()

    async def get_session(
        self,
        *,
        session_key_kind: str,
        session_key_value: str,
        api_key_scope: str,
    ) -> DurableBridgeSessionSnapshot | None:
        statement = select(HttpBridgeSessionRecord).where(
            HttpBridgeSessionRecord.session_key_kind == session_key_kind,
            HttpBridgeSessionRecord.session_key_hash == durable_bridge_hash(session_key_value),
            HttpBridgeSessionRecord.api_key_scope == api_key_scope,
        )
        result = await self._session.execute(statement)
        row = result.scalar_one_or_none()
        return _to_snapshot(row)

    async def get_session_by_id(self, session_id: str) -> DurableBridgeSessionSnapshot | None:
        row = await self._session.get(HttpBridgeSessionRecord, session_id)
        return _to_snapshot(row)

    async def resolve_alias(
        self,
        *,
        alias_kind: str,
        alias_value: str,
        api_key_scope: str,
    ) -> DurableBridgeSessionSnapshot | None:
        statement = (
            select(HttpBridgeSessionRecord)
            .join(HttpBridgeSessionAlias, HttpBridgeSessionAlias.session_id == HttpBridgeSessionRecord.id)
            .where(
                HttpBridgeSessionAlias.alias_kind == alias_kind,
                HttpBridgeSessionAlias.alias_hash == durable_bridge_hash(alias_value),
                HttpBridgeSessionAlias.api_key_scope == api_key_scope,
            )
            .limit(1)
        )
        result = await self._session.execute(statement)
        row = result.scalar_one_or_none()
        return _to_snapshot(row)

    async def find_session_by_latest_turn_state(
        self,
        *,
        turn_state: str,
        api_key_scope: str,
    ) -> DurableBridgeSessionSnapshot | None:
        statement = (
            select(HttpBridgeSessionRecord)
            .where(
                HttpBridgeSessionRecord.latest_turn_state == turn_state,
                HttpBridgeSessionRecord.api_key_scope == api_key_scope,
                HttpBridgeSessionRecord.state.in_((HttpBridgeSessionState.ACTIVE, HttpBridgeSessionState.DRAINING)),
            )
            .order_by(
                case((HttpBridgeSessionRecord.state == HttpBridgeSessionState.ACTIVE, 0), else_=1),
                HttpBridgeSessionRecord.last_seen_at.desc(),
                HttpBridgeSessionRecord.updated_at.desc(),
            )
            .limit(1)
        )
        result = await self._session.execute(statement)
        row = result.scalar_one_or_none()
        return _to_snapshot(row)

    async def find_session_by_latest_response_id(
        self,
        *,
        response_id: str,
        api_key_scope: str,
    ) -> DurableBridgeSessionSnapshot | None:
        statement = (
            select(HttpBridgeSessionRecord)
            .where(
                HttpBridgeSessionRecord.latest_response_id == response_id,
                HttpBridgeSessionRecord.api_key_scope == api_key_scope,
                HttpBridgeSessionRecord.state.in_((HttpBridgeSessionState.ACTIVE, HttpBridgeSessionState.DRAINING)),
            )
            .order_by(
                case((HttpBridgeSessionRecord.state == HttpBridgeSessionState.ACTIVE, 0), else_=1),
                HttpBridgeSessionRecord.last_seen_at.desc(),
                HttpBridgeSessionRecord.updated_at.desc(),
            )
            .limit(1)
        )
        result = await self._session.execute(statement)
        row = result.scalar_one_or_none()
        return _to_snapshot(row)

    async def claim_session(
        self,
        *,
        session_key_kind: str,
        session_key_value: str,
        api_key_scope: str,
        instance_id: str,
        lease_ttl_seconds: float,
        account_id: str | None,
        model: str | None,
        service_tier: str | None,
        latest_turn_state: str | None,
        latest_response_id: str | None,
        allow_takeover: bool,
        force_owner_epoch_advance: bool = False,
    ) -> DurableBridgeSessionSnapshot:
        session_key_hash = durable_bridge_hash(session_key_value)
        for attempt in range(2):
            now = utcnow()
            lease_expires_at = now + timedelta(seconds=max(1.0, lease_ttl_seconds))
            row = await self._session.execute(
                select(HttpBridgeSessionRecord)
                .where(
                    HttpBridgeSessionRecord.session_key_kind == session_key_kind,
                    HttpBridgeSessionRecord.session_key_hash == session_key_hash,
                    HttpBridgeSessionRecord.api_key_scope == api_key_scope,
                )
                .with_for_update()
            )
            existing = row.scalar_one_or_none()
            if existing is None:
                record = HttpBridgeSessionRecord(
                    session_key_kind=session_key_kind,
                    session_key_value=session_key_value,
                    session_key_hash=session_key_hash,
                    api_key_scope=api_key_scope,
                    owner_instance_id=instance_id,
                    owner_epoch=1,
                    lease_expires_at=lease_expires_at,
                    state=HttpBridgeSessionState.ACTIVE,
                    account_id=account_id,
                    model=model,
                    service_tier=service_tier,
                    latest_turn_state=latest_turn_state,
                    latest_response_id=latest_response_id,
                    last_seen_at=now,
                    closed_at=None,
                )
                self._session.add(record)
                try:
                    await self._commit_writer_section()
                except IntegrityError:
                    await self._session.rollback()
                    if attempt == 0:
                        continue
                    raise
                await self._session.refresh(record)
                return _to_snapshot_required(record)

            state_allows_takeover = existing.state in {
                HttpBridgeSessionState.DRAINING,
                HttpBridgeSessionState.CLOSED,
            }
            account_changed = existing.account_id != account_id
            owner_changed = existing.owner_instance_id != instance_id
            if owner_changed:
                lease_expired = existing.lease_expires_at is None or to_utc_naive(existing.lease_expires_at) <= now
                if not allow_takeover and not lease_expired and not state_allows_takeover:
                    return _to_snapshot_required(existing)
                next_epoch = existing.owner_epoch + 1
            elif account_changed or force_owner_epoch_advance:
                next_epoch = existing.owner_epoch + 1
            else:
                next_epoch = existing.owner_epoch

            async with sqlite_writer_section():
                existing.owner_instance_id = instance_id
                existing.owner_epoch = next_epoch
                existing.lease_expires_at = lease_expires_at
                existing.state = HttpBridgeSessionState.ACTIVE
                if account_changed:
                    await self._clear_aliases_for_session(existing.id)
                existing.account_id = account_id
                existing.model = model
                existing.service_tier = service_tier
                if account_changed:
                    existing.latest_turn_state = latest_turn_state
                    existing.latest_response_id = latest_response_id
                    existing.latest_input_item_count = None
                    existing.latest_input_full_fingerprint = None
                elif owner_changed:
                    if latest_turn_state is not None:
                        existing.latest_turn_state = latest_turn_state
                    if latest_response_id is not None:
                        existing.latest_response_id = latest_response_id
                        existing.latest_input_item_count = None
                        existing.latest_input_full_fingerprint = None
                else:
                    if latest_turn_state is not None:
                        existing.latest_turn_state = latest_turn_state
                    if latest_response_id is not None:
                        existing.latest_response_id = latest_response_id
                        existing.latest_input_item_count = None
                        existing.latest_input_full_fingerprint = None
                existing.last_seen_at = now
                existing.closed_at = None
                await self._session.commit()
            await self._session.refresh(existing)
            return _to_snapshot_required(existing)
        raise RuntimeError("Failed to claim durable bridge session after retry")

    async def renew_session(
        self,
        *,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        lease_ttl_seconds: float,
        latest_turn_state: str | None = None,
        latest_response_id: str | None = None,
        latest_input_item_count: int | None = None,
        latest_input_full_fingerprint: str | None = None,
        state: HttpBridgeSessionState | None = None,
    ) -> DurableBridgeSessionSnapshot | None:
        """Renew the lease with a single fenced UPDATE.

        Fenced-out callers mutate nothing and receive the current owner snapshot.
        """

        now = utcnow()
        values: dict[str, object] = {
            "lease_expires_at": now + timedelta(seconds=max(1.0, lease_ttl_seconds)),
            "last_seen_at": now,
        }
        if latest_turn_state is not None:
            values["latest_turn_state"] = latest_turn_state
        if latest_response_id is not None:
            values["latest_response_id"] = latest_response_id
            if latest_input_item_count is None or latest_input_full_fingerprint is None:
                values["latest_input_item_count"] = None
                values["latest_input_full_fingerprint"] = None
        if latest_input_item_count is not None and latest_input_full_fingerprint is not None:
            values["latest_input_item_count"] = latest_input_item_count
            values["latest_input_full_fingerprint"] = latest_input_full_fingerprint
        if state is not None:
            values["state"] = state
        return await self._execute_fenced_session_update(
            session_id=session_id,
            instance_id=instance_id,
            owner_epoch=owner_epoch,
            values=values,
        )

    async def release_session(
        self,
        *,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        draining: bool,
    ) -> DurableBridgeSessionSnapshot | None:
        """Release the lease with a single fenced UPDATE.

        Fenced-out callers mutate nothing and receive the current owner snapshot.
        """

        now = utcnow()
        values: dict[str, object] = {
            "owner_instance_id": None,
            "lease_expires_at": now,
            "last_seen_at": now,
            "state": HttpBridgeSessionState.DRAINING if draining else HttpBridgeSessionState.CLOSED,
            "closed_at": None if draining else now,
        }
        return await self._execute_fenced_session_update(
            session_id=session_id,
            instance_id=instance_id,
            owner_epoch=owner_epoch,
            values=values,
        )

    async def _execute_fenced_session_update(
        self,
        *,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        values: dict[str, object],
    ) -> DurableBridgeSessionSnapshot | None:
        async with sqlite_writer_section():
            result = await self._session.execute(
                update(HttpBridgeSessionRecord)
                .where(
                    HttpBridgeSessionRecord.id == session_id,
                    HttpBridgeSessionRecord.owner_instance_id == instance_id,
                    HttpBridgeSessionRecord.owner_epoch == owner_epoch,
                )
                .values(**values)
                .returning(*_SNAPSHOT_COLUMNS)
            )
            updated_row = result.one_or_none()
            await self._session.commit()
        if updated_row is not None:
            return _returned_row_to_snapshot(updated_row)
        current = await self._session.get(HttpBridgeSessionRecord, session_id, populate_existing=True)
        return _to_snapshot(current)

    async def get_sessions_by_ids(
        self,
        session_ids: Sequence[str],
        *,
        chunk_size: int = _SESSION_ID_LOOKUP_CHUNK_SIZE,
    ) -> list[DurableBridgeSessionSnapshot]:
        unique_ids = list(dict.fromkeys(session_ids))
        if not unique_ids:
            return []
        snapshots: list[DurableBridgeSessionSnapshot] = []
        for start in range(0, len(unique_ids), chunk_size):
            chunk = unique_ids[start : start + chunk_size]
            result = await self._session.execute(
                select(HttpBridgeSessionRecord).where(HttpBridgeSessionRecord.id.in_(chunk))
            )
            snapshots.extend(_to_snapshot_required(row) for row in result.scalars().all())
        return snapshots

    async def mark_owner_draining(self, *, instance_id: str) -> int:
        result = await self._session.execute(
            select(HttpBridgeSessionRecord).where(
                HttpBridgeSessionRecord.owner_instance_id == instance_id,
                HttpBridgeSessionRecord.state == HttpBridgeSessionState.ACTIVE,
            )
        )
        rows = list(result.scalars().all())
        now = utcnow()
        for row in rows:
            row.state = HttpBridgeSessionState.DRAINING
            row.last_seen_at = now
        await self._commit_writer_section()
        return len(rows)

    async def purge_owned_sessions_on_startup(
        self,
        *,
        instance_id: str,
        ownerless_cutoff: datetime | None = None,
        batch_size: int = _PURGE_CLOSED_BATCH_SIZE,
    ) -> int:
        """Remove durable bridge rows left by the previous process instance.

        Ownerless ACTIVE/DRAINING rows are preserved by default: a graceful
        drain release intentionally clears ownership while keeping continuity
        aliases reusable until the full bridge idle-retention window.  Callers
        that already computed that retention cutoff may pass ``ownerless_cutoff``
        to piggyback that abandoned-row cleanup onto startup.
        """

        deleted_count = 0
        while True:
            now = utcnow()
            purge_predicates = [HttpBridgeSessionRecord.owner_instance_id == instance_id]
            if ownerless_cutoff is not None:
                purge_predicates.append(
                    and_(
                        HttpBridgeSessionRecord.owner_instance_id.is_(None),
                        HttpBridgeSessionRecord.state.in_(
                            (HttpBridgeSessionState.ACTIVE, HttpBridgeSessionState.DRAINING),
                        ),
                        or_(
                            HttpBridgeSessionRecord.lease_expires_at.is_(None),
                            HttpBridgeSessionRecord.lease_expires_at < now,
                        ),
                        HttpBridgeSessionRecord.last_seen_at < ownerless_cutoff,
                    )
                )
            startup_purge_filter = or_(*purge_predicates)
            result = await self._session.execute(
                select(
                    HttpBridgeSessionRecord.id,
                    HttpBridgeSessionRecord.session_key_kind,
                    HttpBridgeSessionRecord.session_key_value,
                    HttpBridgeSessionRecord.owner_instance_id,
                    HttpBridgeSessionRecord.last_seen_at,
                )
                .where(startup_purge_filter)
                .order_by(HttpBridgeSessionRecord.last_seen_at.asc())
                .limit(batch_size)
            )
            candidates = list(result.all())
            session_ids = [candidate.id for candidate in candidates]
            if not session_ids:
                return deleted_count
            retained_recovery_ids = {
                candidate.id
                for candidate in candidates
                if candidate.owner_instance_id == instance_id
                and (ownerless_cutoff is None or to_utc_naive(candidate.last_seen_at) >= to_utc_naive(ownerless_cutoff))
                and is_http_bridge_account_neutral_replay(
                    kind=candidate.session_key_kind,
                    key=candidate.session_key_value,
                )
            }
            async with sqlite_writer_section():
                if retained_recovery_ids:
                    await self._session.execute(
                        update(HttpBridgeSessionRecord)
                        .where(
                            HttpBridgeSessionRecord.id.in_(retained_recovery_ids),
                            HttpBridgeSessionRecord.owner_instance_id == instance_id,
                        )
                        .values(
                            owner_instance_id=None,
                            lease_expires_at=now,
                            state=HttpBridgeSessionState.DRAINING,
                            closed_at=None,
                        )
                    )
                deletable_ids = [session_id for session_id in session_ids if session_id not in retained_recovery_ids]
                if deletable_ids:
                    deleted = await self._session.execute(
                        delete(HttpBridgeSessionRecord)
                        .where(HttpBridgeSessionRecord.id.in_(deletable_ids))
                        .where(startup_purge_filter)
                        .returning(HttpBridgeSessionRecord.id)
                    )
                    deleted_ids = list(deleted.scalars().all())
                else:
                    deleted_ids = []
                if deleted_ids:
                    await self._session.execute(
                        delete(HttpBridgeSessionAlias).where(HttpBridgeSessionAlias.session_id.in_(deleted_ids))
                    )
                await self._session.commit()
            deleted_count += len(deleted_ids)

    async def purge_closed_before(self, cutoff: datetime, *, batch_size: int = _PURGE_CLOSED_BATCH_SIZE) -> int:
        deleted_count = 0
        while True:
            result = await self._session.execute(
                select(HttpBridgeSessionRecord.id)
                .where(
                    HttpBridgeSessionRecord.state == HttpBridgeSessionState.CLOSED,
                    HttpBridgeSessionRecord.last_seen_at < cutoff,
                )
                .order_by(HttpBridgeSessionRecord.last_seen_at.asc())
                .limit(batch_size)
            )
            session_ids = list(result.scalars().all())
            if not session_ids:
                return deleted_count
            async with sqlite_writer_section():
                await self._session.execute(
                    delete(HttpBridgeSessionAlias).where(
                        HttpBridgeSessionAlias.session_id.in_(
                            select(HttpBridgeSessionRecord.id).where(
                                HttpBridgeSessionRecord.id.in_(session_ids),
                                HttpBridgeSessionRecord.state == HttpBridgeSessionState.CLOSED,
                                HttpBridgeSessionRecord.last_seen_at < cutoff,
                            )
                        )
                    )
                )
                deleted = await self._session.execute(
                    delete(HttpBridgeSessionRecord)
                    .where(HttpBridgeSessionRecord.id.in_(session_ids))
                    .where(HttpBridgeSessionRecord.state == HttpBridgeSessionState.CLOSED)
                    .where(HttpBridgeSessionRecord.last_seen_at < cutoff)
                    .returning(HttpBridgeSessionRecord.id)
                )
                await self._session.commit()
            deleted_count += len(deleted.scalars().all())

    async def purge_abandoned_before(self, cutoff: datetime, *, batch_size: int = _PURGE_CLOSED_BATCH_SIZE) -> int:
        """Purge ACTIVE/DRAINING rows whose lease expired and whose activity predates the cutoff."""

        deleted_count = 0
        while True:
            now = utcnow()
            abandoned_filter = (
                HttpBridgeSessionRecord.state.in_((HttpBridgeSessionState.ACTIVE, HttpBridgeSessionState.DRAINING)),
                or_(
                    HttpBridgeSessionRecord.lease_expires_at.is_(None),
                    HttpBridgeSessionRecord.lease_expires_at < now,
                ),
                HttpBridgeSessionRecord.last_seen_at < cutoff,
            )
            result = await self._session.execute(
                select(HttpBridgeSessionRecord.id)
                .where(*abandoned_filter)
                .order_by(HttpBridgeSessionRecord.last_seen_at.asc())
                .limit(batch_size)
            )
            session_ids = list(result.scalars().all())
            if not session_ids:
                return deleted_count
            async with sqlite_writer_section():
                await self._session.execute(
                    delete(HttpBridgeSessionAlias).where(
                        HttpBridgeSessionAlias.session_id.in_(
                            select(HttpBridgeSessionRecord.id).where(
                                HttpBridgeSessionRecord.id.in_(session_ids),
                                *abandoned_filter,
                            )
                        )
                    )
                )
                deleted = await self._session.execute(
                    delete(HttpBridgeSessionRecord)
                    .where(HttpBridgeSessionRecord.id.in_(session_ids))
                    .where(*abandoned_filter)
                    .returning(HttpBridgeSessionRecord.id)
                )
                await self._session.commit()
            deleted_count += len(deleted.scalars().all())

    async def upsert_alias(
        self,
        *,
        session_id: str,
        alias_kind: str,
        alias_value: str,
        api_key_scope: str,
    ) -> None:
        async with sqlite_writer_section():
            await self._execute_alias_upsert(
                session_id=session_id,
                alias_kind=alias_kind,
                alias_value=alias_value,
                api_key_scope=api_key_scope,
            )
            await self._session.commit()

    async def register_owned_alias(
        self,
        *,
        session_id: str,
        api_key_scope: str,
        instance_id: str,
        owner_epoch: int,
        alias_kind: str,
        alias_value: str,
        lease_ttl_seconds: float,
        latest_turn_state: str | None = None,
        latest_response_id: str | None = None,
        latest_input_item_count: int | None = None,
        latest_input_full_fingerprint: str | None = None,
    ) -> DurableBridgeAliasRegistration:
        """Register continuity only while the caller still owns the durable row."""

        async with sqlite_writer_section():
            now = utcnow()
            session_values: dict[str, object] = {
                "lease_expires_at": now + timedelta(seconds=max(1.0, lease_ttl_seconds)),
                "last_seen_at": now,
            }
            if latest_turn_state is not None:
                session_values["latest_turn_state"] = latest_turn_state
            if latest_response_id is not None:
                session_values["latest_response_id"] = latest_response_id
                session_values["latest_input_item_count"] = latest_input_item_count
                session_values["latest_input_full_fingerprint"] = latest_input_full_fingerprint
            elif latest_input_item_count is not None and latest_input_full_fingerprint is not None:
                session_values["latest_input_item_count"] = latest_input_item_count
                session_values["latest_input_full_fingerprint"] = latest_input_full_fingerprint

            fenced_update = await self._session.execute(
                update(HttpBridgeSessionRecord)
                .where(
                    HttpBridgeSessionRecord.id == session_id,
                    HttpBridgeSessionRecord.api_key_scope == api_key_scope,
                    HttpBridgeSessionRecord.owner_instance_id == instance_id,
                    HttpBridgeSessionRecord.owner_epoch == owner_epoch,
                )
                .values(**session_values)
                .returning(
                    HttpBridgeSessionRecord.id,
                    HttpBridgeSessionRecord.session_key_kind,
                    HttpBridgeSessionRecord.session_key_value,
                )
            )
            target = fenced_update.one_or_none()
            if target is None:
                return DurableBridgeAliasRegistration.OWNER_FENCED

            registered = await self._execute_alias_upsert(
                session_id=session_id,
                alias_kind=alias_kind,
                alias_value=alias_value,
                api_key_scope=api_key_scope,
                target_account_neutral_replay=is_http_bridge_account_neutral_replay(
                    kind=target.session_key_kind,
                    key=target.session_key_value,
                ),
            )
            if not registered:
                await self._session.rollback()
                return DurableBridgeAliasRegistration.ALIAS_PROTECTED
            await self._session.commit()
        return DurableBridgeAliasRegistration.REGISTERED

    async def register_reversible_turn_state_alias(
        self,
        *,
        session_id: str,
        api_key_scope: str,
        instance_id: str,
        owner_epoch: int,
        turn_state: str,
        lease_ttl_seconds: float,
    ) -> DurableBridgeAliasRegistrationReceipt:
        """Publish a pre-dispatch turn alias with enough state for exact rollback."""

        alias_kind = "turn_state"
        async with sqlite_writer_section():
            now = utcnow()
            # The first UPDATE both fences ownership and acquires the target-row
            # lock (and SQLite's writer lock) before prior alias state is read.
            fenced_lock = await self._session.execute(
                update(HttpBridgeSessionRecord)
                .where(
                    HttpBridgeSessionRecord.id == session_id,
                    HttpBridgeSessionRecord.api_key_scope == api_key_scope,
                    HttpBridgeSessionRecord.owner_instance_id == instance_id,
                    HttpBridgeSessionRecord.owner_epoch == owner_epoch,
                )
                .values(
                    lease_expires_at=now + timedelta(seconds=max(1.0, lease_ttl_seconds)),
                    last_seen_at=now,
                )
                .returning(
                    HttpBridgeSessionRecord.id,
                    HttpBridgeSessionRecord.session_key_kind,
                    HttpBridgeSessionRecord.session_key_value,
                    HttpBridgeSessionRecord.latest_turn_state,
                )
            )
            target = fenced_lock.one_or_none()
            if target is None:
                await self._session.rollback()
                return DurableBridgeAliasRegistrationReceipt(
                    status=DurableBridgeAliasRegistration.OWNER_FENCED,
                    session_id=session_id,
                    api_key_scope=api_key_scope,
                    alias_kind=alias_kind,
                    alias_value=turn_state,
                    instance_id=instance_id,
                    owner_epoch=owner_epoch,
                    previous_alias_session_id=None,
                    previous_alias_owner_epoch=None,
                    previous_alias_account_id=None,
                    previous_latest_turn_state=None,
                )

            previous_latest_turn_state = target.latest_turn_state
            previous_alias_session_id = await self._session.scalar(
                select(HttpBridgeSessionAlias.session_id)
                .where(
                    HttpBridgeSessionAlias.alias_kind == alias_kind,
                    HttpBridgeSessionAlias.alias_hash == durable_bridge_hash(turn_state),
                    HttpBridgeSessionAlias.alias_value == turn_state,
                    HttpBridgeSessionAlias.api_key_scope == api_key_scope,
                )
                .with_for_update()
            )
            previous_alias_owner_epoch = None
            previous_alias_account_id = None
            if previous_alias_session_id is not None:
                previous_alias_owner = (
                    await self._session.execute(
                        select(
                            HttpBridgeSessionRecord.owner_epoch,
                            HttpBridgeSessionRecord.account_id,
                        ).where(HttpBridgeSessionRecord.id == previous_alias_session_id)
                    )
                ).one_or_none()
                if previous_alias_owner is not None:
                    previous_alias_owner_epoch = previous_alias_owner.owner_epoch
                    previous_alias_account_id = previous_alias_owner.account_id
            await self._session.execute(
                update(HttpBridgeSessionRecord)
                .where(HttpBridgeSessionRecord.id == session_id)
                .values(latest_turn_state=turn_state)
            )
            registered = await self._execute_alias_upsert(
                session_id=session_id,
                alias_kind=alias_kind,
                alias_value=turn_state,
                api_key_scope=api_key_scope,
                target_account_neutral_replay=is_http_bridge_account_neutral_replay(
                    kind=target.session_key_kind,
                    key=target.session_key_value,
                ),
            )
            if not registered:
                await self._session.rollback()
                return DurableBridgeAliasRegistrationReceipt(
                    status=DurableBridgeAliasRegistration.ALIAS_PROTECTED,
                    session_id=session_id,
                    api_key_scope=api_key_scope,
                    alias_kind=alias_kind,
                    alias_value=turn_state,
                    instance_id=instance_id,
                    owner_epoch=owner_epoch,
                    previous_alias_session_id=previous_alias_session_id,
                    previous_alias_owner_epoch=previous_alias_owner_epoch,
                    previous_alias_account_id=previous_alias_account_id,
                    previous_latest_turn_state=previous_latest_turn_state,
                )
            await self._session.commit()

        return DurableBridgeAliasRegistrationReceipt(
            status=DurableBridgeAliasRegistration.REGISTERED,
            session_id=session_id,
            api_key_scope=api_key_scope,
            alias_kind=alias_kind,
            alias_value=turn_state,
            instance_id=instance_id,
            owner_epoch=owner_epoch,
            previous_alias_session_id=previous_alias_session_id,
            previous_alias_owner_epoch=previous_alias_owner_epoch,
            previous_alias_account_id=previous_alias_account_id,
            previous_latest_turn_state=previous_latest_turn_state,
        )

    async def rollback_reversible_turn_state_alias(
        self,
        *,
        receipt: DurableBridgeAliasRegistrationReceipt,
    ) -> bool:
        """Undo a registered pre-dispatch alias while the same owner is fenced in."""

        if receipt.status != DurableBridgeAliasRegistration.REGISTERED:
            return False

        async with sqlite_writer_section():
            previous_session_valid = False
            dialect = self._session.get_bind().dialect.name
            if dialect == "postgresql":
                session_ids = {receipt.session_id}
                if receipt.previous_alias_session_id is not None:
                    session_ids.add(receipt.previous_alias_session_id)
                locked_records = (
                    (
                        await self._session.execute(
                            select(HttpBridgeSessionRecord)
                            .where(HttpBridgeSessionRecord.id.in_(session_ids))
                            .order_by(HttpBridgeSessionRecord.id)
                            .with_for_update()
                        )
                    )
                    .scalars()
                    .all()
                )
                records_by_id = {record.id: record for record in locked_records}
                target_record = records_by_id.get(receipt.session_id)
                if (
                    target_record is None
                    or target_record.api_key_scope != receipt.api_key_scope
                    or target_record.owner_instance_id != receipt.instance_id
                    or target_record.owner_epoch != receipt.owner_epoch
                ):
                    await self._session.rollback()
                    return False
                previous_record = (
                    records_by_id.get(receipt.previous_alias_session_id)
                    if receipt.previous_alias_session_id is not None
                    else None
                )
                previous_session_valid = previous_record is not None and (
                    previous_record.owner_epoch == receipt.previous_alias_owner_epoch
                    and previous_record.account_id == receipt.previous_alias_account_id
                )

            fenced_restore = await self._session.execute(
                update(HttpBridgeSessionRecord)
                .where(
                    HttpBridgeSessionRecord.id == receipt.session_id,
                    HttpBridgeSessionRecord.api_key_scope == receipt.api_key_scope,
                    HttpBridgeSessionRecord.owner_instance_id == receipt.instance_id,
                    HttpBridgeSessionRecord.owner_epoch == receipt.owner_epoch,
                )
                .values(
                    latest_turn_state=case(
                        (
                            HttpBridgeSessionRecord.latest_turn_state == receipt.alias_value,
                            receipt.previous_latest_turn_state,
                        ),
                        else_=HttpBridgeSessionRecord.latest_turn_state,
                    )
                )
                .returning(HttpBridgeSessionRecord.id)
            )
            if fenced_restore.scalar_one_or_none() is None:
                await self._session.rollback()
                return False

            if dialect != "postgresql" and receipt.previous_alias_session_id is not None:
                previous_record = (
                    await self._session.execute(
                        select(
                            HttpBridgeSessionRecord.owner_epoch,
                            HttpBridgeSessionRecord.account_id,
                        ).where(HttpBridgeSessionRecord.id == receipt.previous_alias_session_id)
                    )
                ).one_or_none()
                previous_session_valid = previous_record is not None and (
                    previous_record.owner_epoch == receipt.previous_alias_owner_epoch
                    and previous_record.account_id == receipt.previous_alias_account_id
                )

            alias_predicate = (
                HttpBridgeSessionAlias.session_id == receipt.session_id,
                HttpBridgeSessionAlias.alias_kind == receipt.alias_kind,
                HttpBridgeSessionAlias.alias_hash == durable_bridge_hash(receipt.alias_value),
                HttpBridgeSessionAlias.alias_value == receipt.alias_value,
                HttpBridgeSessionAlias.api_key_scope == receipt.api_key_scope,
            )
            current_alias_session_id = await self._session.scalar(
                select(HttpBridgeSessionAlias.session_id).where(*alias_predicate).with_for_update()
            )
            if current_alias_session_id == receipt.session_id:
                previous_session_id = receipt.previous_alias_session_id
                if previous_session_id is None:
                    await self._session.execute(delete(HttpBridgeSessionAlias).where(*alias_predicate))
                elif previous_session_id != receipt.session_id:
                    if not previous_session_valid:
                        await self._session.execute(delete(HttpBridgeSessionAlias).where(*alias_predicate))
                    else:
                        await self._session.execute(
                            update(HttpBridgeSessionAlias)
                            .where(*alias_predicate)
                            .values(session_id=previous_session_id, updated_at=utcnow())
                        )
            await self._session.commit()
        return True

    async def _execute_alias_upsert(
        self,
        *,
        session_id: str,
        alias_kind: str,
        alias_value: str,
        api_key_scope: str,
        target_account_neutral_replay: bool | None = None,
    ) -> bool:
        dialect = self._session.get_bind().dialect.name
        now = utcnow()
        values = {
            "session_id": session_id,
            "alias_kind": alias_kind,
            "alias_value": alias_value,
            "alias_hash": durable_bridge_hash(alias_value),
            "api_key_scope": api_key_scope,
        }
        existing_target_is_account_neutral_replay = HttpBridgeSessionAlias.session_id.in_(
            select(HttpBridgeSessionRecord.id).where(
                HttpBridgeSessionRecord.session_key_kind == HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KIND,
                HttpBridgeSessionRecord.session_key_value.like(f"{HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KEY_PREFIX}%"),
                HttpBridgeSessionRecord.session_key_value != HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KEY_PREFIX,
            )
        )
        existing_target_is_rebindable = HttpBridgeSessionAlias.session_id.in_(
            select(HttpBridgeSessionRecord.id).where(
                HttpBridgeSessionRecord.session_key_kind.in_(HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_REBINDABLE_KINDS),
            )
        )
        existing_target_is_replaceable_recovery = HttpBridgeSessionAlias.session_id.in_(
            select(HttpBridgeSessionRecord.id).where(
                HttpBridgeSessionRecord.session_key_kind == HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KIND,
                HttpBridgeSessionRecord.session_key_value.like(f"{HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KEY_PREFIX}%"),
                HttpBridgeSessionRecord.session_key_value != HTTP_BRIDGE_ACCOUNT_NEUTRAL_REPLAY_KEY_PREFIX,
                or_(
                    HttpBridgeSessionRecord.owner_instance_id.is_(None),
                    HttpBridgeSessionRecord.lease_expires_at.is_(None),
                    HttpBridgeSessionRecord.lease_expires_at <= now,
                ),
            )
        )
        conflict_where = None
        if target_account_neutral_replay is True:
            conflict_where = or_(
                HttpBridgeSessionAlias.session_id == session_id,
                existing_target_is_replaceable_recovery,
                existing_target_is_rebindable,
            )
        elif target_account_neutral_replay is False:
            conflict_where = or_(
                HttpBridgeSessionAlias.session_id == session_id,
                ~existing_target_is_account_neutral_replay,
            )
        if dialect == "postgresql":
            statement = (
                pg_insert(HttpBridgeSessionAlias)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=[
                        HttpBridgeSessionAlias.alias_kind,
                        HttpBridgeSessionAlias.alias_hash,
                        HttpBridgeSessionAlias.api_key_scope,
                    ],
                    set_={
                        "session_id": session_id,
                        "alias_value": alias_value,
                        "updated_at": now,
                    },
                    where=conflict_where,
                )
                .returning(HttpBridgeSessionAlias.session_id)
            )
        elif dialect == "sqlite":
            statement = (
                sqlite_insert(HttpBridgeSessionAlias)
                .values(**values)
                .on_conflict_do_update(
                    index_elements=[
                        HttpBridgeSessionAlias.alias_kind,
                        HttpBridgeSessionAlias.alias_hash,
                        HttpBridgeSessionAlias.api_key_scope,
                    ],
                    set_={
                        "session_id": session_id,
                        "alias_value": alias_value,
                        "updated_at": now,
                    },
                    where=conflict_where,
                )
                .returning(HttpBridgeSessionAlias.session_id)
            )
        else:
            raise RuntimeError(f"DurableBridgeRepository alias upsert unsupported for dialect={dialect!r}")
        result = await self._session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def _clear_aliases_for_session(self, session_id: str) -> None:
        await self._session.execute(
            delete(HttpBridgeSessionAlias).where(HttpBridgeSessionAlias.session_id == session_id)
        )


async def missing_durable_bridge_tables(session: AsyncSession) -> tuple[str, ...]:
    dialect = session.get_bind().dialect.name
    expected = set(REQUIRED_DURABLE_BRIDGE_TABLES)
    if dialect == "sqlite":
        result = await session.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name IN ('http_bridge_sessions', 'http_bridge_session_aliases')"
            )
        )
    else:
        result = await session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' "
                "AND table_name IN ('http_bridge_sessions', 'http_bridge_session_aliases')"
            )
        )
    present = {str(row[0]) for row in result.fetchall()}
    return tuple(sorted(expected - present))


_SNAPSHOT_COLUMNS = (
    HttpBridgeSessionRecord.id,
    HttpBridgeSessionRecord.session_key_kind,
    HttpBridgeSessionRecord.session_key_value,
    HttpBridgeSessionRecord.session_key_hash,
    HttpBridgeSessionRecord.api_key_scope,
    HttpBridgeSessionRecord.owner_instance_id,
    HttpBridgeSessionRecord.owner_epoch,
    HttpBridgeSessionRecord.lease_expires_at,
    HttpBridgeSessionRecord.state,
    HttpBridgeSessionRecord.account_id,
    HttpBridgeSessionRecord.model,
    HttpBridgeSessionRecord.service_tier,
    HttpBridgeSessionRecord.latest_turn_state,
    HttpBridgeSessionRecord.latest_response_id,
    HttpBridgeSessionRecord.latest_input_item_count,
    HttpBridgeSessionRecord.latest_input_full_fingerprint,
    HttpBridgeSessionRecord.closed_at,
)


def _returned_row_to_snapshot(row: Row[tuple[object, ...]]) -> DurableBridgeSessionSnapshot:
    mapping = row._mapping
    return DurableBridgeSessionSnapshot(
        id=mapping[HttpBridgeSessionRecord.id],
        session_key_kind=mapping[HttpBridgeSessionRecord.session_key_kind],
        session_key_value=mapping[HttpBridgeSessionRecord.session_key_value],
        session_key_hash=mapping[HttpBridgeSessionRecord.session_key_hash],
        api_key_scope=mapping[HttpBridgeSessionRecord.api_key_scope],
        owner_instance_id=mapping[HttpBridgeSessionRecord.owner_instance_id],
        owner_epoch=mapping[HttpBridgeSessionRecord.owner_epoch],
        lease_expires_at=mapping[HttpBridgeSessionRecord.lease_expires_at],
        state=mapping[HttpBridgeSessionRecord.state],
        account_id=mapping[HttpBridgeSessionRecord.account_id],
        model=mapping[HttpBridgeSessionRecord.model],
        service_tier=mapping[HttpBridgeSessionRecord.service_tier],
        latest_turn_state=mapping[HttpBridgeSessionRecord.latest_turn_state],
        latest_response_id=mapping[HttpBridgeSessionRecord.latest_response_id],
        latest_input_item_count=mapping[HttpBridgeSessionRecord.latest_input_item_count],
        latest_input_full_fingerprint=mapping[HttpBridgeSessionRecord.latest_input_full_fingerprint],
        closed_at=mapping[HttpBridgeSessionRecord.closed_at],
    )


def _to_snapshot(row: HttpBridgeSessionRecord | None) -> DurableBridgeSessionSnapshot | None:
    if row is None:
        return None
    return DurableBridgeSessionSnapshot(
        id=row.id,
        session_key_kind=row.session_key_kind,
        session_key_value=row.session_key_value,
        session_key_hash=row.session_key_hash,
        api_key_scope=row.api_key_scope,
        owner_instance_id=row.owner_instance_id,
        owner_epoch=row.owner_epoch,
        lease_expires_at=row.lease_expires_at,
        state=row.state,
        account_id=row.account_id,
        model=row.model,
        service_tier=row.service_tier,
        latest_turn_state=row.latest_turn_state,
        latest_response_id=row.latest_response_id,
        latest_input_item_count=row.latest_input_item_count,
        latest_input_full_fingerprint=row.latest_input_full_fingerprint,
        closed_at=row.closed_at,
    )


def _to_snapshot_required(row: HttpBridgeSessionRecord) -> DurableBridgeSessionSnapshot:
    snapshot = _to_snapshot(row)
    if snapshot is None:
        raise RuntimeError("Expected durable bridge session snapshot")
    return snapshot

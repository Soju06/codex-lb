from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time import utcnow
from app.db.models import HttpBridgeSessionAlias, HttpBridgeSessionRecord, HttpBridgeSessionState

_ANONYMOUS_API_KEY_SCOPE = "__anonymous__"
REQUIRED_DURABLE_BRIDGE_TABLES = (
    "http_bridge_sessions",
    "http_bridge_session_aliases",
)


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
    closed_at: datetime | None


class DurableBridgeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
                    await self._session.commit()
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
            owner_changed = existing.owner_instance_id != instance_id
            if not owner_changed:
                next_epoch = existing.owner_epoch
            else:
                lease_expired = existing.lease_expires_at is None or existing.lease_expires_at <= now
                if not allow_takeover and not lease_expired and not state_allows_takeover:
                    return _to_snapshot_required(existing)
                next_epoch = existing.owner_epoch + 1

            existing.owner_instance_id = instance_id
            existing.owner_epoch = next_epoch
            existing.lease_expires_at = lease_expires_at
            existing.state = HttpBridgeSessionState.ACTIVE
            existing.account_id = account_id
            existing.model = model
            existing.service_tier = service_tier
            if owner_changed:
                existing.latest_turn_state = latest_turn_state
                existing.latest_response_id = latest_response_id
            else:
                if latest_turn_state is not None:
                    existing.latest_turn_state = latest_turn_state
                if latest_response_id is not None:
                    existing.latest_response_id = latest_response_id
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
        state: HttpBridgeSessionState | None = None,
    ) -> DurableBridgeSessionSnapshot | None:
        row = await self._session.get(HttpBridgeSessionRecord, session_id)
        if row is None:
            return None
        if row.owner_instance_id != instance_id or row.owner_epoch != owner_epoch:
            return _to_snapshot(row)
        now = utcnow()
        row.lease_expires_at = now + timedelta(seconds=max(1.0, lease_ttl_seconds))
        row.last_seen_at = now
        if latest_turn_state is not None:
            row.latest_turn_state = latest_turn_state
        if latest_response_id is not None:
            row.latest_response_id = latest_response_id
        if state is not None:
            row.state = state
        await self._session.commit()
        await self._session.refresh(row)
        return _to_snapshot(row)

    async def release_session(
        self,
        *,
        session_id: str,
        instance_id: str,
        owner_epoch: int,
        draining: bool,
    ) -> DurableBridgeSessionSnapshot | None:
        row = await self._session.get(HttpBridgeSessionRecord, session_id)
        if row is None:
            return None
        if row.owner_instance_id != instance_id or row.owner_epoch != owner_epoch:
            return _to_snapshot(row)
        now = utcnow()
        row.owner_instance_id = None
        row.lease_expires_at = now
        row.last_seen_at = now
        row.state = HttpBridgeSessionState.DRAINING if draining else HttpBridgeSessionState.CLOSED
        row.closed_at = None if draining else now
        await self._session.commit()
        await self._session.refresh(row)
        return _to_snapshot(row)

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
        await self._session.commit()
        return len(rows)

    async def upsert_alias(
        self,
        *,
        session_id: str,
        alias_kind: str,
        alias_value: str,
        api_key_scope: str,
    ) -> None:
        dialect = self._session.get_bind().dialect.name
        values = {
            "session_id": session_id,
            "alias_kind": alias_kind,
            "alias_value": alias_value,
            "alias_hash": durable_bridge_hash(alias_value),
            "api_key_scope": api_key_scope,
        }
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
                        "updated_at": utcnow(),
                    },
                )
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
                        "updated_at": utcnow(),
                    },
                )
            )
        else:
            raise RuntimeError(f"DurableBridgeRepository alias upsert unsupported for dialect={dialect!r}")
        await self._session.execute(statement)
        await self._session.commit()


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
        closed_at=row.closed_at,
    )


def _to_snapshot_required(row: HttpBridgeSessionRecord) -> DurableBridgeSessionSnapshot:
    snapshot = _to_snapshot(row)
    if snapshot is None:
        raise RuntimeError("Expected durable bridge session snapshot")
    return snapshot

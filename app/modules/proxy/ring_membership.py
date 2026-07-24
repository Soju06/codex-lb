from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time import utcnow
from app.db.models import BridgeRingMember
from app.db.session import close_session

if TYPE_CHECKING:
    from collections.abc import Callable


RING_HEARTBEAT_INTERVAL_SECONDS = 10
RING_STALE_THRESHOLD_SECONDS = 30
RING_STALE_GRACE_SECONDS = RING_HEARTBEAT_INTERVAL_SECONDS + 5
RING_MEMBER_RETENTION_SECONDS = 24 * 60 * 60


class RingMembershipService:
    """Manages pod registration in the bridge ring.

    This service stores and retrieves active pod memberships from the DB,
    ensuring all pods see the same ring view (solving the split-brain problem).
    """

    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self._session_factory = session_factory

    async def register(self, instance_id: str, *, endpoint_base_url: str | None = None) -> None:
        """Upsert pod into ring. Safe to call multiple times."""
        async with self._session() as session:
            # Dialect-specific upsert
            dialect = session.get_bind().dialect.name
            metadata_json = _bridge_ring_metadata_json(endpoint_base_url)
            if dialect == "postgresql":
                stmt = (
                    pg_insert(BridgeRingMember)
                    .values(
                        id=str(uuid.uuid4()),
                        instance_id=instance_id,
                        registered_at=utcnow(),
                        last_heartbeat_at=utcnow(),
                        metadata_json=metadata_json,
                    )
                    .on_conflict_do_update(
                        index_elements=["instance_id"],
                        set_={
                            "last_heartbeat_at": utcnow(),
                            "registered_at": utcnow(),
                            "metadata_json": metadata_json,
                        },
                    )
                )
            elif dialect == "sqlite":
                stmt = (
                    sqlite_insert(BridgeRingMember)
                    .values(
                        id=str(uuid.uuid4()),
                        instance_id=instance_id,
                        registered_at=utcnow(),
                        last_heartbeat_at=utcnow(),
                        metadata_json=metadata_json,
                    )
                    .on_conflict_do_update(
                        index_elements=["instance_id"],
                        set_={
                            "last_heartbeat_at": utcnow(),
                            "registered_at": utcnow(),
                            "metadata_json": metadata_json,
                        },
                    )
                )
            else:
                raise RuntimeError(f"RingMembershipService unsupported for dialect={dialect!r}")
            await session.execute(stmt)
            await session.commit()

    async def heartbeat(
        self,
        instance_id: str,
        *,
        endpoint_base_url: str | None = None,
        account_stream_inflight: dict[str, int] | None = None,
    ) -> None:
        """Upsert heartbeat — recovers from mark_stale or unregister by sibling workers."""
        async with self._session() as session:
            dialect = session.get_bind().dialect.name
            now = utcnow()
            metadata_json = _bridge_ring_metadata_json(endpoint_base_url, account_stream_inflight)
            if dialect == "postgresql":
                stmt = (
                    pg_insert(BridgeRingMember)
                    .values(
                        id=str(uuid.uuid4()),
                        instance_id=instance_id,
                        registered_at=now,
                        last_heartbeat_at=now,
                        metadata_json=metadata_json,
                    )
                    .on_conflict_do_update(
                        index_elements=["instance_id"],
                        set_={"last_heartbeat_at": now, "metadata_json": metadata_json},
                    )
                )
            elif dialect == "sqlite":
                stmt = (
                    sqlite_insert(BridgeRingMember)
                    .values(
                        id=str(uuid.uuid4()),
                        instance_id=instance_id,
                        registered_at=now,
                        last_heartbeat_at=now,
                        metadata_json=metadata_json,
                    )
                    .on_conflict_do_update(
                        index_elements=["instance_id"],
                        set_={"last_heartbeat_at": now, "metadata_json": metadata_json},
                    )
                )
            else:
                stmt = (
                    update(BridgeRingMember)
                    .where(BridgeRingMember.instance_id == instance_id)
                    .values(last_heartbeat_at=now, metadata_json=metadata_json)
                )
            await session.execute(stmt)
            await session.commit()

    async def unregister(self, instance_id: str) -> None:
        """Remove pod from ring."""
        async with self._session() as session:
            stmt = delete(BridgeRingMember).where(BridgeRingMember.instance_id == instance_id)
            await session.execute(stmt)
            await session.commit()

    async def mark_stale(
        self,
        instance_id: str,
        *,
        stale_threshold_seconds: int = RING_STALE_THRESHOLD_SECONDS,
        grace_seconds: int = RING_STALE_GRACE_SECONDS,
    ) -> None:
        """Age the heartbeat close to expiry without deleting the shared row.

        A short grace window lets sibling workers refresh the shared row on
        their next heartbeat, while a fully terminating pod still ages out far
        faster than the normal stale threshold.
        """
        from datetime import timedelta

        active_for_seconds = max(grace_seconds, 0)
        age_seconds = max(stale_threshold_seconds - active_for_seconds, 0)
        stale_time = utcnow() - timedelta(seconds=age_seconds)
        async with self._session() as session:
            stmt = (
                update(BridgeRingMember)
                .where(BridgeRingMember.instance_id == instance_id)
                .values(last_heartbeat_at=stale_time)
            )
            await session.execute(stmt)
            await session.commit()

    async def purge_stale_before(self, cutoff: datetime) -> int:
        """Delete ring rows whose heartbeat predates the cutoff (dead replicas)."""

        async with self._session() as session:
            result = cast(
                CursorResult[Any],
                await session.execute(delete(BridgeRingMember).where(BridgeRingMember.last_heartbeat_at < cutoff)),
            )
            await session.commit()
        return int(result.rowcount or 0)

    async def list_active(
        self,
        stale_threshold_seconds: int = RING_STALE_THRESHOLD_SECONDS,
        *,
        require_endpoint: bool = False,
    ) -> list[str]:
        """Return sorted list of pods whose heartbeat is within threshold."""
        from datetime import timedelta

        cutoff = utcnow() - timedelta(seconds=stale_threshold_seconds)
        statement = select(BridgeRingMember.instance_id).where(BridgeRingMember.last_heartbeat_at >= cutoff)
        if require_endpoint:
            statement = statement.where(BridgeRingMember.metadata_json.is_not(None))
        async with self._session() as session:
            result = await session.execute(statement.order_by(BridgeRingMember.instance_id))
            return list(result.scalars().all())

    async def resolve_endpoint(
        self,
        instance_id: str,
        *,
        stale_threshold_seconds: int = RING_STALE_THRESHOLD_SECONDS,
    ) -> str | None:
        from datetime import timedelta

        cutoff = utcnow() - timedelta(seconds=stale_threshold_seconds)
        async with self._session() as session:
            result = await session.execute(
                select(BridgeRingMember.metadata_json)
                .where(
                    BridgeRingMember.instance_id == instance_id,
                    BridgeRingMember.last_heartbeat_at >= cutoff,
                )
                .limit(1)
            )
            metadata_json = result.scalar_one_or_none()
        return _bridge_ring_endpoint_from_metadata(metadata_json)

    async def list_active_stream_inflight(
        self,
        self_instance_id: str,
        *,
        stale_threshold_seconds: int = RING_STALE_THRESHOLD_SECONDS,
    ) -> dict[str, dict[str, int] | None]:
        """Fresh peers' published per-account stream-lease counts.

        Maps every other active member's instance id to its published counts,
        or ``None`` when that member's metadata carries no counts, so
        consumers can distinguish incomplete data from idle peers.
        """
        from datetime import timedelta

        cutoff = utcnow() - timedelta(seconds=stale_threshold_seconds)
        statement = select(BridgeRingMember.instance_id, BridgeRingMember.metadata_json).where(
            BridgeRingMember.last_heartbeat_at >= cutoff,
            BridgeRingMember.instance_id != self_instance_id,
        )
        async with self._session() as session:
            result = await session.execute(statement)
            rows = result.all()
        return {
            instance_id: _bridge_ring_stream_inflight_from_metadata(metadata_json)
            for instance_id, metadata_json in rows
        }

    async def ring_fingerprint(self, stale_threshold_seconds: int = RING_STALE_THRESHOLD_SECONDS) -> str:
        """sha256 of sorted active member list. Same for all pods with same membership."""
        members = await self.list_active(stale_threshold_seconds)
        data = ",".join(sorted(members))
        return sha256(data.encode()).hexdigest()

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        session = self._session_factory()
        try:
            yield session
        finally:
            await close_session(session)


def _bridge_ring_metadata_json(
    endpoint_base_url: str | None,
    account_stream_inflight: dict[str, int] | None = None,
) -> str | None:
    # ``list_active(require_endpoint=True)`` treats a non-null metadata row as
    # endpoint-bearing, so metadata is only written alongside an advertised
    # endpoint; stream-inflight counts ride along on the same upsert.
    if endpoint_base_url is None:
        return None
    payload: dict[str, object] = {"endpoint_base_url": endpoint_base_url}
    if account_stream_inflight is not None:
        payload["account_stream_inflight"] = {
            account_id: int(count) for account_id, count in account_stream_inflight.items() if count > 0
        }
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _bridge_ring_stream_inflight_from_metadata(metadata_json: str | None) -> dict[str, int] | None:
    """Published per-account stream counts; None when absent (mixed-version ring)."""
    if metadata_json is None:
        return None
    try:
        payload = json.loads(metadata_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    counts = payload.get("account_stream_inflight")
    if not isinstance(counts, dict):
        return None
    parsed: dict[str, int] = {}
    for account_id, count in counts.items():
        if isinstance(account_id, str) and isinstance(count, int) and count > 0:
            parsed[account_id] = count
    return parsed


def _bridge_ring_endpoint_from_metadata(metadata_json: str | None) -> str | None:
    if metadata_json is None:
        return None
    try:
        payload = json.loads(metadata_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    endpoint = payload.get("endpoint_base_url")
    if not isinstance(endpoint, str):
        return None
    stripped = endpoint.strip().rstrip("/")
    return stripped or None

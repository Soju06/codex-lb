from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from hashlib import sha256
from typing import TYPE_CHECKING

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time import utcnow
from app.db.models import BridgeRingMember

if TYPE_CHECKING:
    from collections.abc import Callable


class RingMembershipService:
    """Manages pod registration in the bridge ring.

    This service stores and retrieves active pod memberships from the DB,
    ensuring all pods see the same ring view (solving the split-brain problem).
    """

    def __init__(self, session_factory: Callable[[], AsyncSession]) -> None:
        self._session_factory = session_factory

    async def register(self, instance_id: str) -> None:
        """Upsert pod into ring. Safe to call multiple times."""
        async with self._session() as session:
            # Dialect-specific upsert
            dialect = session.get_bind().dialect.name
            if dialect == "postgresql":
                stmt = (
                    pg_insert(BridgeRingMember)
                    .values(
                        id=str(uuid.uuid4()),
                        instance_id=instance_id,
                        registered_at=utcnow(),
                        last_heartbeat_at=utcnow(),
                    )
                    .on_conflict_do_update(
                        index_elements=["instance_id"],
                        set_={"last_heartbeat_at": utcnow(), "registered_at": utcnow()},
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
                    )
                    .on_conflict_do_update(
                        index_elements=["instance_id"],
                        set_={"last_heartbeat_at": utcnow(), "registered_at": utcnow()},
                    )
                )
            else:
                raise RuntimeError(f"RingMembershipService unsupported for dialect={dialect!r}")
            await session.execute(stmt)
            await session.commit()

    async def heartbeat(self, instance_id: str) -> None:
        """Update last_heartbeat_at for a registered pod."""
        async with self._session() as session:
            stmt = (
                update(BridgeRingMember)
                .where(BridgeRingMember.instance_id == instance_id)
                .values(last_heartbeat_at=utcnow())
            )
            await session.execute(stmt)
            await session.commit()

    async def unregister(self, instance_id: str) -> None:
        """Remove pod from ring."""
        async with self._session() as session:
            stmt = delete(BridgeRingMember).where(BridgeRingMember.instance_id == instance_id)
            await session.execute(stmt)
            await session.commit()

    async def list_active(self, stale_threshold_seconds: int = 120) -> list[str]:
        """Return sorted list of pods whose heartbeat is within threshold."""
        from datetime import timedelta

        cutoff = utcnow() - timedelta(seconds=stale_threshold_seconds)
        async with self._session() as session:
            result = await session.execute(
                select(BridgeRingMember.instance_id)
                .where(BridgeRingMember.last_heartbeat_at >= cutoff)
                .order_by(BridgeRingMember.instance_id)
            )
            return list(result.scalars().all())

    async def ring_fingerprint(self, stale_threshold_seconds: int = 120) -> str:
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
            await session.close()

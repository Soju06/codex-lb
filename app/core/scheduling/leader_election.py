from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from app.core.config.settings import get_settings
from app.db.models import SchedulerLeader
from app.db.session import get_session

logger = logging.getLogger(__name__)


class LeaderElection:
    def __init__(self, leader_id: str | None = None) -> None:
        self._leader_id = leader_id or str(uuid.uuid4())
        self._is_leader = False

    async def try_acquire(self) -> bool:
        settings = get_settings()
        if not settings.leader_election_enabled:
            self._is_leader = True
            return True

        if "sqlite" in settings.database_url.lower():
            self._is_leader = True
            return True

        ttl = settings.leader_election_ttl_seconds
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=ttl)

        try:
            async for session in get_session():
                await session.execute(
                    text(
                        """
                        INSERT INTO scheduler_leader (id, leader_id, acquired_at, expires_at)
                        VALUES (1, :leader_id, :now, :expires_at)
                        ON CONFLICT (id) DO UPDATE SET
                            leader_id = :leader_id,
                            acquired_at = :now,
                            expires_at = :expires_at
                        WHERE scheduler_leader.expires_at < :now OR scheduler_leader.leader_id = :leader_id
                        """
                    ),
                    {
                        "leader_id": self._leader_id,
                        "now": now,
                        "expires_at": expires_at,
                    },
                )
                await session.commit()
                row = await session.scalar(select(SchedulerLeader.leader_id).where(SchedulerLeader.id == 1))
                self._is_leader = row == self._leader_id
                return self._is_leader
        except Exception:
            logger.warning("Leader election failed, defaulting to non-leader", exc_info=True)

        self._is_leader = False
        return False

    async def renew(self) -> bool:
        if not self._is_leader:
            return False

        settings = get_settings()
        if not settings.leader_election_enabled:
            return True
        if "sqlite" in settings.database_url.lower():
            return True

        ttl = settings.leader_election_ttl_seconds
        expires_at = datetime.now(UTC) + timedelta(seconds=ttl)
        try:
            async for session in get_session():
                await session.execute(
                    text(
                        "UPDATE scheduler_leader SET expires_at = :expires_at WHERE id = 1 AND leader_id = :leader_id"
                    ),
                    {
                        "expires_at": expires_at,
                        "leader_id": self._leader_id,
                    },
                )
                await session.commit()
                return True
        except Exception:
            logger.warning("Failed to renew leadership", exc_info=True)
            return False

        return False


_leader_election: LeaderElection | None = None


def get_leader_election() -> LeaderElection:
    global _leader_election
    if _leader_election is None:
        _leader_election = LeaderElection()
    return _leader_election

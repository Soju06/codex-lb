from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar, cast

from sqlalchemy import CursorResult, Float, Result, bindparam, delete, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Insert

from app.core.config.settings import get_settings
from app.db.models import SchedulerLeader
from app.db.session import get_background_session

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

_MAX_CONSECUTIVE_RENEW_ERRORS = 2

# PostgreSQL evaluates both the new expiry and the takeover predicate on the
# database clock so inter-replica wall-clock skew cannot steal a live lease.
_POSTGRES_ACQUIRE_SQL = text(
    """
    INSERT INTO scheduler_leader (id, leader_id, acquired_at, expires_at)
    VALUES (1, :leader_id, now(), now() + make_interval(secs => :ttl))
    ON CONFLICT (id) DO UPDATE SET
        leader_id = excluded.leader_id,
        acquired_at = excluded.acquired_at,
        expires_at = excluded.expires_at
    WHERE scheduler_leader.expires_at < now() OR scheduler_leader.leader_id = :leader_id
    """
).bindparams(bindparam("ttl", type_=Float))

_POSTGRES_RENEW_SQL = text(
    """
    UPDATE scheduler_leader
    SET expires_at = now() + make_interval(secs => :ttl)
    WHERE id = 1 AND leader_id = :leader_id
    """
).bindparams(bindparam("ttl", type_=Float))


def _sqlite_acquire_statement(leader_id: str, now: datetime, expires_at: datetime) -> Insert:
    # A shared SQLite file implies a single host, so binding the host clock on
    # both sides of the comparison keeps a single clock domain. The upsert is
    # atomic under SQLite's single-writer lock, so it arbitrates for real.
    statement = sqlite_insert(SchedulerLeader).values(
        id=1,
        leader_id=leader_id,
        acquired_at=now,
        expires_at=expires_at,
    )
    return statement.on_conflict_do_update(
        index_elements=["id"],
        set_={
            "leader_id": statement.excluded.leader_id,
            "acquired_at": statement.excluded.acquired_at,
            "expires_at": statement.excluded.expires_at,
        },
        where=(SchedulerLeader.expires_at < now) | (SchedulerLeader.leader_id == leader_id),
    )


def _dialect_name(session: AsyncSession) -> str:
    return session.get_bind().dialect.name


def _rowcount(result: Result[Any]) -> int:
    # ``AsyncSession.execute`` is typed as returning ``Result``, but DML
    # statements always produce a ``CursorResult`` at runtime, which is the
    # only Result subtype that carries ``rowcount``.
    return cast(CursorResult[Any], result).rowcount


class LeaderElection:
    def __init__(self, leader_id: str | None = None) -> None:
        self._leader_id = leader_id or str(uuid.uuid4())
        self._is_leader = False

    @property
    def leader_id(self) -> str:
        return self._leader_id

    @property
    def is_leader(self) -> bool:
        return self._is_leader

    async def try_acquire(self) -> bool:
        settings = get_settings()
        if not settings.leader_election_enabled:
            self._is_leader = True
            return True

        ttl = settings.leader_election_ttl_seconds
        try:
            async with get_background_session() as session:
                if _dialect_name(session) == "sqlite":
                    now = datetime.now(UTC)
                    result = await session.execute(
                        _sqlite_acquire_statement(self._leader_id, now, now + timedelta(seconds=ttl))
                    )
                else:
                    result = await session.execute(
                        _POSTGRES_ACQUIRE_SQL,
                        {"leader_id": self._leader_id, "ttl": ttl},
                    )
                acquired = _rowcount(result) == 1
                await session.commit()
        except Exception:
            logger.warning("Leader election failed, defaulting to non-leader", exc_info=True)
            self._is_leader = False
            return False

        self._is_leader = acquired
        return acquired

    async def renew(self) -> bool:
        """Extend the held lease; demote when the lease is no longer ours.

        Raises on database errors so callers can distinguish a lost lease
        (returns ``False``) from a transient renewal failure.
        """
        if not self._is_leader:
            return False

        settings = get_settings()
        if not settings.leader_election_enabled:
            return True

        ttl = settings.leader_election_ttl_seconds
        async with get_background_session() as session:
            if _dialect_name(session) == "sqlite":
                result = await session.execute(
                    update(SchedulerLeader)
                    .where(SchedulerLeader.id == 1, SchedulerLeader.leader_id == self._leader_id)
                    .values(expires_at=datetime.now(UTC) + timedelta(seconds=ttl))
                )
            else:
                result = await session.execute(
                    _POSTGRES_RENEW_SQL,
                    {"leader_id": self._leader_id, "ttl": ttl},
                )
            renewed = _rowcount(result) == 1
            await session.commit()

        if not renewed:
            self._is_leader = False
        return renewed

    async def release(self) -> None:
        """Delete the lease row we hold so followers can take over immediately.

        Failure to release must never block shutdown; the lease then simply
        expires after the TTL.
        """
        self._is_leader = False
        settings = get_settings()
        if not settings.leader_election_enabled:
            return
        try:
            async with get_background_session() as session:
                await session.execute(
                    delete(SchedulerLeader).where(
                        SchedulerLeader.id == 1,
                        SchedulerLeader.leader_id == self._leader_id,
                    )
                )
                await session.commit()
        except Exception:
            logger.warning("Failed to release scheduler leader lease", exc_info=True)

    async def run_if_leader(self, fn: Callable[[], Awaitable[_T]]) -> _T | None:
        """Run ``fn`` only while holding the leader lease.

        Heartbeats the lease every ``max(1, ttl // 3)`` seconds while the body
        runs and cancels the body when the lease is lost (or after
        ``_MAX_CONSECUTIVE_RENEW_ERRORS`` consecutive renewal errors), bounding
        leader overlap to roughly one renew interval. Returns the body's
        result, or ``None`` when this replica is not leader or the body was
        cancelled due to lease loss.
        """
        if not await self.try_acquire():
            return None

        settings = get_settings()
        if not settings.leader_election_enabled:
            return await fn()

        renew_interval = max(1, settings.leader_election_ttl_seconds // 3)
        # ``ensure_future`` accepts any awaitable (``create_task`` requires a
        # coroutine), wrapping it in a task so it can be cancelled on lease loss.
        body_task: asyncio.Task[_T] = asyncio.ensure_future(fn())
        lease_lost = False

        async def _heartbeat() -> None:
            nonlocal lease_lost
            consecutive_errors = 0
            while True:
                await asyncio.sleep(renew_interval)
                try:
                    renewed = await self.renew()
                    consecutive_errors = 0
                except Exception:
                    consecutive_errors += 1
                    logger.warning(
                        "Leader lease renewal errored consecutive_errors=%s",
                        consecutive_errors,
                        exc_info=True,
                    )
                    if consecutive_errors < _MAX_CONSECUTIVE_RENEW_ERRORS:
                        continue
                    self._is_leader = False
                    renewed = False
                if not renewed:
                    lease_lost = True
                    body_task.cancel()
                    return

        heartbeat_task = asyncio.create_task(_heartbeat())
        try:
            return await body_task
        except asyncio.CancelledError:
            if lease_lost:
                logger.warning(
                    "Leader-gated task cancelled after lease loss leader_id=%s",
                    self._leader_id,
                )
                return None
            raise
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
            if not body_task.done():
                body_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await body_task


_leader_election: LeaderElection | None = None


def get_leader_election() -> LeaderElection:
    global _leader_election
    if _leader_election is None:
        _leader_election = LeaderElection()
    return _leader_election

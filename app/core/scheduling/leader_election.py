from __future__ import annotations

import asyncio
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

# How long a lease-loss (or shutdown) cancellation waits for the gated body to
# actually finish before detaching it. Bodies may legitimately shield in-flight
# singleton work (token/usage refresh singleflights) and drain it after
# cancellation; awaiting that unboundedly would pin ``run_if_leader`` for the
# duration of an upstream call after the lease is already gone.
_CANCEL_GRACE_SECONDS = 5.0

# How long ``release`` waits for previously detached gated bodies to finish
# before deleting the lease row. If a detached body is still draining after
# this grace the early release is skipped entirely: handing the lease to a
# follower while the old body may still act as leader would recreate the
# duplicate-singleton overlap, so the lease is left to expire after its TTL.
_RELEASE_DRAIN_GRACE_SECONDS = 5.0

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
        # Gated bodies that were detached after the cancellation grace and may
        # still be draining shielded singleton work as the (former) leader.
        self._detached_bodies: set[asyncio.Task[Any]] = set()

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

        Bodies detached after the cancellation grace may still be draining
        shielded singleton work as the former leader, so the early release
        first waits up to ``_RELEASE_DRAIN_GRACE_SECONDS`` for them. If any
        body is still draining after the grace, the row is left in place —
        the lease then expires after its TTL — because handing it to a
        follower while old gated work still runs would recreate the
        duplicate-singleton overlap the lease exists to prevent.

        Failure to release must never block shutdown; the lease then simply
        expires after the TTL.
        """
        self._is_leader = False
        settings = get_settings()
        if not settings.leader_election_enabled:
            return
        if not await self._drain_detached_bodies():
            logger.warning(
                "Skipping early leader lease release: detached leader-gated work is still "
                "draining after %.1fs; the lease will expire after its TTL",
                _RELEASE_DRAIN_GRACE_SECONDS,
            )
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
        runs. Each renewal attempt is time-boxed to ``ttl / 6`` so a hung
        database call cannot silently extend leadership: two consecutive
        failed attempts (each costing at most interval + timeout = ttl / 2),
        or any failed attempt after the locally tracked lease deadline has
        passed, demote the holder no later than the lease TTL. On lease loss
        the body is cancelled and awaited for at most
        ``_CANCEL_GRACE_SECONDS``; a body still draining shielded work after
        the grace is detached (its outcome is logged from a done callback),
        so ``run_if_leader`` itself returns within the grace of the loss.
        Returns the body's result, or ``None`` when this replica is not
        leader or the body was cancelled due to lease loss.
        """
        if not await self.try_acquire():
            return None

        settings = get_settings()
        if not settings.leader_election_enabled:
            return await fn()

        ttl = settings.leader_election_ttl_seconds
        renew_interval = max(1, ttl // 3)
        renew_timeout = max(1.0, ttl / 6)
        loop = asyncio.get_running_loop()
        # Local monotonic estimate of when the lease we hold expires; extended
        # on every successful renewal. This is deliberately conservative: the
        # database clock may grant slightly more, never less.
        lease_deadline = loop.time() + ttl
        # ``ensure_future`` accepts any awaitable (``create_task`` requires a
        # coroutine), wrapping it in a task so it can be cancelled on lease loss.
        body_task: asyncio.Task[_T] = asyncio.ensure_future(fn())
        lease_lost = False

        async def _heartbeat() -> None:
            nonlocal lease_deadline, lease_lost
            consecutive_errors = 0
            while True:
                await asyncio.sleep(renew_interval)
                try:
                    renewed = await asyncio.wait_for(self.renew(), timeout=renew_timeout)
                    consecutive_errors = 0
                    if renewed:
                        lease_deadline = loop.time() + ttl
                except Exception:
                    consecutive_errors += 1
                    logger.warning(
                        "Leader lease renewal errored or timed out consecutive_errors=%s",
                        consecutive_errors,
                        exc_info=True,
                    )
                    if consecutive_errors < _MAX_CONSECUTIVE_RENEW_ERRORS and loop.time() < lease_deadline:
                        continue
                    self._is_leader = False
                    renewed = False
                if not renewed:
                    lease_lost = True
                    return

        heartbeat_task = asyncio.create_task(_heartbeat())
        # Whether the lease-loss branch already cancelled (and possibly
        # detached) the body. The finally block must not cancel a second time:
        # a body draining shielded work sits at a plain ``await inner`` in its
        # CancelledError handler, and a second ``Task.cancel()`` would cancel
        # that shielded inner task through the await.
        body_cancel_handled = False
        try:
            done, _ = await asyncio.wait({body_task, heartbeat_task}, return_when=asyncio.FIRST_COMPLETED)
            if heartbeat_task in done and not lease_lost and not body_task.done():
                # The heartbeat can only exit without flagging lease loss by
                # crashing; without renewals leadership cannot be trusted.
                logger.error(
                    "Leader heartbeat failed unexpectedly; demoting leader_id=%s",
                    self._leader_id,
                    exc_info=heartbeat_task.exception(),
                )
                self._is_leader = False
                lease_lost = True
            if lease_lost:
                logger.warning(
                    "Leader-gated task cancelled after lease loss leader_id=%s",
                    self._leader_id,
                )
                body_cancel_handled = True
                await self._cancel_within_grace(body_task)
                return None
            return await body_task
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Leader heartbeat failed during cleanup")
            if not body_cancel_handled and not body_task.done():
                await self._cancel_within_grace(body_task)

    async def _cancel_within_grace(self, task: asyncio.Task[Any]) -> None:
        """Cancel ``task`` and await it for at most ``_CANCEL_GRACE_SECONDS``.

        Gated bodies may shield in-flight singleton refreshes (e.g. the token
        and usage refresh singleflights) and drain them after a cancellation
        request, so this uses ``asyncio.wait`` (which does not re-cancel on
        timeout) and detaches the task after the grace instead of blocking on
        the shielded upstream call. A detached task keeps draining in the
        background bounded by the underlying operation's own timeout; it is
        tracked so ``release`` will not hand the lease over while it may still
        run, and its outcome is logged from a done callback so failures are
        still observed.
        """
        task.cancel()
        _, pending = await asyncio.wait({task}, timeout=_CANCEL_GRACE_SECONDS)
        if pending:
            self._detached_bodies.add(task)
            task.add_done_callback(self._on_detached_body_done)
            logger.warning(
                "Leader-gated task still draining shielded work %.1fs after cancellation; detaching",
                _CANCEL_GRACE_SECONDS,
            )
            return
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning("Leader-gated task failed while being cancelled", exc_info=exc)

    def _on_detached_body_done(self, task: asyncio.Task[Any]) -> None:
        self._detached_bodies.discard(task)
        _log_detached_body_result(task)

    async def _drain_detached_bodies(self) -> bool:
        """Wait for detached gated bodies; true when none remain running."""
        pending = {task for task in self._detached_bodies if not task.done()}
        if not pending:
            return True
        logger.info(
            "Waiting up to %.1fs for %d detached leader-gated task(s) before releasing the lease",
            _RELEASE_DRAIN_GRACE_SECONDS,
            len(pending),
        )
        _, still_pending = await asyncio.wait(pending, timeout=_RELEASE_DRAIN_GRACE_SECONDS)
        return not still_pending


def _log_detached_body_result(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        logger.info("Detached leader-gated task finished cancelling after lease loss")
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("Detached leader-gated task failed after lease loss", exc_info=exc)
    else:
        logger.info("Detached leader-gated task completed after lease loss")


_leader_election: LeaderElection | None = None


def get_leader_election() -> LeaderElection:
    global _leader_election
    if _leader_election is None:
        _leader_election = LeaderElection()
    return _leader_election

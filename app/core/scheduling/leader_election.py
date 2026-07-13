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

# PostgreSQL evaluates the lease clock server-side so inter-replica wall-clock
# skew cannot steal a live lease. The stored expiry uses ``clock_timestamp()``
# (the actual statement-execution time) rather than ``now()`` /
# ``transaction_timestamp()`` (fixed at transaction start): a renewal or
# same-leader re-acquire that blocks on the ``scheduler_leader`` row lock must
# extend from the CURRENT time, not from a timestamp captured before it waited.
# Because the row lock serializes writers, ``clock_timestamp()`` is evaluated in
# commit order, so a slow writer that committed after a newer one can never
# write an earlier ``expires_at`` — the lease can only move forward. The
# conflict-update path recomputes ``clock_timestamp()`` in the ``DO UPDATE SET``
# clause rather than copying ``excluded.*``: the ``excluded`` row is the
# ``VALUES`` tuple, evaluated before the statement blocked on the row lock, so
# reusing it would stamp a pre-wait expiry and reintroduce the stale-clock race.
# The
# takeover predicate keeps the transaction snapshot clock (``now()``): the
# takeover decision is a single point-in-time read and staying on the snapshot
# is the conservative choice (a waiter never over-eagerly steals a lease that
# was refreshed while it was blocked on the lock).
_POSTGRES_ACQUIRE_SQL = text(
    """
    INSERT INTO scheduler_leader (id, leader_id, acquired_at, expires_at)
    VALUES (1, :leader_id, clock_timestamp(), clock_timestamp() + make_interval(secs => :ttl))
    ON CONFLICT (id) DO UPDATE SET
        leader_id = excluded.leader_id,
        acquired_at = clock_timestamp(),
        expires_at = clock_timestamp() + make_interval(secs => :ttl)
    WHERE scheduler_leader.expires_at < now() OR scheduler_leader.leader_id = :leader_id
    """
).bindparams(bindparam("ttl", type_=Float))

_POSTGRES_RENEW_SQL = text(
    """
    UPDATE scheduler_leader
    SET expires_at = clock_timestamp() + make_interval(secs => :ttl)
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
        # Monotonic (event-loop clock) estimate of when the lease we hold
        # expires, set on every successful acquire/renew and cleared on
        # authoritative loss. ``None`` means no lease is currently held. It is
        # deliberately conservative: the database clock may grant slightly
        # more, never less. A transient acquisition failure observed before
        # this deadline passes must not demote an already-held lease.
        self._lease_deadline: float | None = None
        # Gated bodies that were detached after the cancellation grace and may
        # still be draining shielded singleton work as the (former) leader.
        self._detached_bodies: set[asyncio.Task[Any]] = set()
        # Renewal tasks abandoned after their time-box elapsed. The heartbeat
        # must not await a stalled renewal's (possibly hung) cancellation
        # unwinding, so it drops the task here with a done callback that
        # consumes the result. Keeping a strong reference prevents the loop
        # from garbage-collecting a still-pending task mid-flight.
        self._abandoned_renewals: set[asyncio.Task[bool]] = set()

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
        loop = asyncio.get_running_loop()
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
            # A transient acquisition failure must not demote a lease this
            # instance already holds and whose locally tracked deadline has
            # not passed. The leader election is a shared singleton across
            # every singleton scheduler, so one scheduler tick can call
            # ``try_acquire`` while another scheduler's gated body is still
            # the valid leader; clearing ``_is_leader`` here would make that
            # body's next ``renew`` return ``False`` without touching the
            # database and cancel otherwise-valid leader work. Demotion is
            # reserved for an authoritative non-owner result (below) or a
            # failure observed after the held lease has already expired.
            if self._is_leader and self._lease_deadline is not None and loop.time() < self._lease_deadline:
                logger.warning(
                    "Leader lease acquisition failed but the held lease is still valid; "
                    "preserving leadership leader_id=%s",
                    self._leader_id,
                    exc_info=True,
                )
                return True
            logger.warning("Leader election failed, defaulting to non-leader", exc_info=True)
            self._is_leader = False
            self._lease_deadline = None
            return False

        self._is_leader = acquired
        self._lease_deadline = loop.time() + ttl if acquired else None
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
        loop = asyncio.get_running_loop()
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

        if renewed:
            # Extend the locally tracked deadline so a concurrent acquire that
            # hits a transient error keeps preserving leadership for the full
            # renewed lease, not just the original acquisition window.
            self._lease_deadline = loop.time() + ttl
        else:
            self._is_leader = False
            self._lease_deadline = None
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
        self._lease_deadline = None
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
        runs, except that each sleep is bounded by the time remaining until the
        locally tracked lease deadline: a lease seeded from a preserved acquire
        may have less than a renew interval left, and the heartbeat must wake to
        renew (or demote when already past the deadline) before the database row
        expires rather than sleeping a full interval past it. Each renewal
        attempt is time-boxed to ``ttl / 6`` so a hung
        database call cannot silently extend leadership: two consecutive
        failed attempts (each costing at most interval + timeout = ttl / 2),
        or any failed attempt after the locally tracked lease deadline has
        passed, demote the holder no later than the lease TTL. The time-box is
        enforced with ``asyncio.wait`` (not ``asyncio.wait_for``) so a renewal
        whose cancellation cleanup itself stalls — e.g. a blocked rollback in
        session teardown — cannot pin the heartbeat past the timeout: once the
        timeout elapses the attempt is abandoned and counted as an error
        immediately, without awaiting the renewal coroutine's unwinding. On
        lease loss
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
        #
        # Seed it from the instance's DB-confirmed deadline rather than a fresh
        # ``loop.time() + ttl``. ``try_acquire`` may have returned ``True``
        # WITHOUT extending the database ``expires_at`` — the "preserve active
        # leadership on a transient acquire error" path keeps an already-held
        # lease alive but performs no DB write, so it leaves ``_lease_deadline``
        # at the last value a real acquire/renew confirmed. Resetting to a full
        # TTL here would let the local deadline drift PAST the true DB lease
        # expiry, so the heartbeat could keep believing it leads after a
        # follower has legitimately taken over the row. ``_lease_deadline`` is
        # only ``None`` in the disabled escape hatch (handled above), so the
        # fallback is defensive only.
        lease_deadline = self._lease_deadline if self._lease_deadline is not None else loop.time() + ttl
        # ``ensure_future`` accepts any awaitable (``create_task`` requires a
        # coroutine), wrapping it in a task so it can be cancelled on lease loss.
        body_task: asyncio.Task[_T] = asyncio.ensure_future(fn())
        lease_lost = False

        async def _heartbeat() -> None:
            nonlocal lease_deadline, lease_lost
            consecutive_errors = 0
            inflight: asyncio.Task[bool] | None = None
            try:
                while True:
                    # Never sleep past the locally tracked lease deadline. A
                    # preserved acquire (a transient acquire error that kept an
                    # already-held lease) seeds ``lease_deadline`` from the last
                    # DB-confirmed expiry, which may be less than a full
                    # ``renew_interval`` out; sleeping the whole interval would
                    # keep the gated body running after the database row has
                    # expired, letting a follower acquire and run the same
                    # singleton work concurrently. Bound each sleep (especially
                    # the first) by the time remaining, and demote immediately
                    # when the deadline has already passed rather than sleeping.
                    remaining = lease_deadline - loop.time()
                    if remaining <= 0:
                        self._is_leader = False
                        self._lease_deadline = None
                        lease_lost = True
                        return
                    await asyncio.sleep(min(float(renew_interval), remaining))
                    inflight = asyncio.ensure_future(self.renew())
                    # ``asyncio.wait`` returns on the timeout without cancelling
                    # or awaiting ``inflight``, so a renewal whose cancellation
                    # cleanup itself hangs cannot pin the heartbeat past the
                    # time-box the way ``asyncio.wait_for`` (which awaits the
                    # cancelled coroutine's unwinding) would.
                    done, _ = await asyncio.wait({inflight}, timeout=renew_timeout)
                    renew_task = inflight
                    inflight = None
                    try:
                        if renew_task not in done:
                            # Renewal stalled past its time-box. Abandon it
                            # without awaiting the unwind and treat the lease as
                            # at risk on the timeout deadline, not whenever the
                            # renewal finally returns.
                            self._abandon_renewal(renew_task)
                            raise TimeoutError("leader lease renewal timed out")
                        renewed = renew_task.result()
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
                        self._lease_deadline = None
                        renewed = False
                    else:
                        consecutive_errors = 0
                        if renewed:
                            lease_deadline = loop.time() + ttl
                    if not renewed:
                        lease_lost = True
                        return
            finally:
                # If the heartbeat is itself cancelled (e.g. shutdown) while a
                # renewal is in flight, abandon it so it does not leak.
                if inflight is not None and not inflight.done():
                    self._abandon_renewal(inflight)

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
                self._lease_deadline = None
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

    def _abandon_renewal(self, task: asyncio.Task[bool]) -> None:
        """Request cancellation of a stalled renewal without awaiting its unwind.

        The heartbeat already observed the time-box elapse, so leadership can
        be treated as at risk immediately. Cancellation cleanup of a hung
        database call (a blocked rollback or driver call during session
        teardown) may itself block, so the task is dropped here — tracked with
        a strong reference and a done callback that consumes its result — and
        left to unwind in the background rather than blocking the loop.
        """
        self._abandoned_renewals.add(task)
        task.add_done_callback(self._on_renewal_done)
        task.cancel()

    def _on_renewal_done(self, task: asyncio.Task[bool]) -> None:
        self._abandoned_renewals.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.debug("Abandoned leader lease renewal finished with error", exc_info=exc)

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

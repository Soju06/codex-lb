from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from sqlalchemy import Float, Result, bindparam, delete, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

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
#
# ``RETURNING`` reports the lease STILL REMAINING (``expires_at`` minus the
# database's OWN clock, ``clock_timestamp()``, in the same statement) so the
# caller can anchor its local monotonic deadline to the database's authoritative
# remaining lease rather than to a Python instant. Both the stored expiry and
# the returned ``db_now`` come from the one server clock, so the remaining is
# correct regardless of how long the statement waited on the row lock. A
# conflict no-op (another replica owns an unexpired lease) matches no row and
# RETURNS nothing, so the caller reads it as "not acquired". See ``try_acquire``
# for why anchoring a POST-statement monotonic instant to this remaining fixes
# both the backdate (a pre-statement instant seeds an already-expired deadline
# after a lock wait) and the outrun (a post-commit instant overshoots the DB
# expiry) failure modes at once.
_POSTGRES_ACQUIRE_SQL = text(
    """
    INSERT INTO scheduler_leader (id, leader_id, acquired_at, expires_at)
    VALUES (1, :leader_id, clock_timestamp(), clock_timestamp() + make_interval(secs => :ttl))
    ON CONFLICT (id) DO UPDATE SET
        leader_id = excluded.leader_id,
        acquired_at = clock_timestamp(),
        expires_at = clock_timestamp() + make_interval(secs => :ttl)
    WHERE scheduler_leader.expires_at < now() OR scheduler_leader.leader_id = :leader_id
    RETURNING extract(epoch FROM (expires_at - clock_timestamp()))
    """
).bindparams(bindparam("ttl", type_=Float))

# The renewal is conditional on the lease still being unexpired at
# statement-execution time (``expires_at > clock_timestamp()``), matching the
# same clock domain as the ``SET`` expiry. Keying only on ``id`` + ``leader_id``
# is not enough: if a heartbeat is delayed past the lease deadline (event-loop
# stall, row-lock wait) the row can already have expired while no follower has
# claimed it yet, and an unconditional UPDATE would resurrect that dead lease —
# extending leader-gated work past the TTL and delaying/overlapping failover. A
# renewal that finds the lease already expired matches 0 rows, which the caller
# treats as lease loss and demotes on (see :meth:`renew`), letting the follower
# take the row cleanly. ``clock_timestamp()`` (not ``now()``) is used so a
# renewal that blocked on the row lock is judged against the CURRENT time, not
# the transaction-start snapshot captured before it waited. ``RETURNING`` yields
# the renewed lease's remaining (``expires_at`` minus the same server clock) so
# :meth:`renew` can re-anchor its local deadline to the database's authoritative
# remaining lease; a rowcount-0 no-match RETURNS nothing (read as lease loss).
_POSTGRES_RENEW_SQL = text(
    """
    UPDATE scheduler_leader
    SET expires_at = clock_timestamp() + make_interval(secs => :ttl)
    WHERE id = 1 AND leader_id = :leader_id AND expires_at > clock_timestamp()
    RETURNING extract(epoch FROM (expires_at - clock_timestamp()))
    """
).bindparams(bindparam("ttl", type_=Float))


# SQLite evaluates the lease clock inside the statement using its OWN
# execution-time clock, mirroring the PostgreSQL ``clock_timestamp()`` fix
# above. A shared SQLite file implies a single host, so there is one clock
# domain — but a renewal or same-leader re-acquire can still sit behind SQLite's
# single-writer lock (``busy_timeout``, default ~30s) for far longer than the
# lease TTL (the minimum is 5s). Binding a Python ``now`` captured BEFORE
# ``session.execute`` would then evaluate the unexpired-lease guard AND the new
# ``expires_at`` against a STALE pre-wait instant once the write lock is finally
# acquired: a heartbeat that should have lost the lease could still match and
# extend the row, running leader-gated work past the real TTL and overlapping
# failover. Computing the clock in-SQL removes that gap — SQLite evaluates
# ``'now'`` once per ``sqlite3_step()`` (every ``strftime(..., 'now')`` in one
# statement shares that single instant, so the guard predicate and the ``SET``
# expiry stay in one clock domain) and only after the write lock is actually
# held, exactly like ``clock_timestamp()`` on PostgreSQL. ``strftime('%Y-%m-%d
# %H:%M:%f', 'now')`` renders a 3-digit-millisecond fractional field;
# ``|| '000'`` pads it to the 6-digit-microsecond WIDTH SQLAlchemy's
# ``DateTime`` persists, so the stored string stays lexicographically
# comparable with existing rows and with the acquire path. SQLite's ``'now'``
# is UTC, matching the naive-UTC wall clock SQLAlchemy writes for the tz-aware
# ``expires_at`` column, so no extra anchoring is needed. The takeover
# predicate necessarily shares this same execution-time clock (SQLite has no
# separate transaction-snapshot clock within a statement).
_SQLITE_NOW = "(strftime('%Y-%m-%d %H:%M:%f', 'now') || '000')"
_SQLITE_NOW_PLUS_TTL = "(strftime('%Y-%m-%d %H:%M:%f', 'now', '+' || :ttl || ' seconds') || '000')"
# Lease still remaining, in seconds, computed entirely on SQLite's own clock:
# ``expires_at`` (the string this module stores) minus ``'now'``, both evaluated
# in the SAME statement step so they share one instant. ``RETURNING`` this lets
# the caller anchor its local monotonic deadline to the database's authoritative
# remaining lease instead of a Python instant — see ``try_acquire`` — so the
# deadline neither backdates (a pre-statement Python instant seeds an
# already-expired deadline once the write lock is finally acquired after a long
# ``busy_timeout`` wait) nor outruns (a post-commit instant overshoots the row).
# ``julianday`` parses the 6-digit-microsecond-width string the acquire/renewal
# paths persist; the difference in fractional days is scaled to seconds.
_SQLITE_REMAINING = "((julianday(expires_at) - julianday('now')) * 86400.0)"

_SQLITE_ACQUIRE_SQL = text(
    f"""
    INSERT INTO scheduler_leader (id, leader_id, acquired_at, expires_at)
    VALUES (1, :leader_id, {_SQLITE_NOW}, {_SQLITE_NOW_PLUS_TTL})
    ON CONFLICT (id) DO UPDATE SET
        leader_id = excluded.leader_id,
        acquired_at = {_SQLITE_NOW},
        expires_at = {_SQLITE_NOW_PLUS_TTL}
    WHERE scheduler_leader.expires_at < {_SQLITE_NOW} OR scheduler_leader.leader_id = :leader_id
    RETURNING {_SQLITE_REMAINING}
    """
).bindparams(bindparam("ttl", type_=Float))

# The SQLite renewal mirrors ``_POSTGRES_RENEW_SQL``: it extends ``expires_at``
# from the execution-time clock and is conditional on the lease still being
# unexpired at that same clock, so a heartbeat delayed past the deadline (event-
# loop stall, single-writer-lock wait) matches 0 rows instead of resurrecting a
# dead row that no follower has claimed yet. Shared by :meth:`renew` and the
# release/drain path (:meth:`_renew_lease_row`).
_SQLITE_RENEW_SQL = text(
    f"""
    UPDATE scheduler_leader
    SET expires_at = {_SQLITE_NOW_PLUS_TTL}
    WHERE id = 1 AND leader_id = :leader_id AND expires_at > {_SQLITE_NOW}
    RETURNING {_SQLITE_REMAINING}
    """
).bindparams(bindparam("ttl", type_=Float))


def _dialect_name(session: AsyncSession) -> str:
    return session.get_bind().dialect.name


def _is_locked_error(exc: BaseException) -> bool:
    """Return ``True`` for a transient SQLite ``database is locked``/``busy``.

    A shared SQLite file has a single writer; even with the connection-level
    ``busy_timeout`` a best-effort lease write (renewal or release) can still
    lose the race for the write lock against the app's other DB work — or, in
    the test suite, against schema teardown's ``DROP TABLE`` — and surface as
    ``sqlite3.OperationalError: database is locked``. On the best-effort
    shutdown paths this is not a lease-release failure: the lease simply expires
    after its TTL (release) or is retried on the next cadence (renewal), so it
    is swallowed and logged at DEBUG rather than spamming warnings. Only
    ``OperationalError`` carrying the locked/busy text qualifies; every other
    error keeps its original WARNING so genuine faults stay visible.
    """
    if not isinstance(exc, OperationalError):
        return False
    message = str(exc.orig if exc.orig is not None else exc).lower()
    return "database is locked" in message or "database is busy" in message


def _returned_remaining(result: Result[Any]) -> float | None:
    """Return the DB-computed remaining lease seconds, or ``None`` on no-match.

    The acquire/renew statements ``RETURNING`` the affected row's remaining
    lease (``expires_at`` minus the database's OWN clock, computed in the same
    statement so both share one instant). A single row means the write took
    effect and its value is the authoritative remaining lease the caller
    anchors its local monotonic deadline to; no row means the statement matched
    nothing — another replica owns the lease, or a guarded renewal found it
    already expired — which the caller treats as "not acquired / lease lost".
    Relying on the RETURNING row (not ``rowcount``) is also portable: SQLite's
    driver reports ``rowcount`` as ``-1`` for ``RETURNING`` statements.
    """
    row = result.first()
    if row is None:
        return None
    return float(row[0])


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
        # The single process-level lease-renewal keeper for the graceful-
        # shutdown window. Started once at shutdown-begin (before ANY scheduler
        # is stopped) and stopped by ``release``. Because schedulers are stopped
        # one at a time and only the LAST one triggers ``release``, an earlier
        # scheduler's ``stop()`` can detach a shielded gated body — which cancels
        # that scheduler's own heartbeat — while later schedulers are still
        # stopping and ``release`` has not begun its drain-renewal yet. This
        # keeper owns lease renewal continuously across that whole gap so the DB
        # lease can never expire while any detached/draining leader-gated body
        # could still be acting as leader.
        self._release_keeper: asyncio.Task[None] | None = None
        self._release_keeper_stop: asyncio.Event | None = None

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
                acquire_sql = _SQLITE_ACQUIRE_SQL if _dialect_name(session) == "sqlite" else _POSTGRES_ACQUIRE_SQL
                result = await session.execute(
                    acquire_sql,
                    {"leader_id": self._leader_id, "ttl": ttl},
                )
                # The statement RETURNS the lease still remaining, measured on
                # the database's OWN clock (``expires_at`` minus ``db_now`` in
                # the same statement), or nothing when another replica owns the
                # lease. Capture the monotonic instant right AFTER the statement
                # returns and add the DB-remaining to it: the local deadline then
                # tracks the database's authoritative expiry precisely. Anchoring
                # a PRE-statement instant would backdate the deadline by however
                # long the statement waited on the row / write lock (a lock wait
                # can exceed a short TTL, seeding an already-expired deadline);
                # anchoring after ``commit()`` would push it past the true DB
                # expiry by the commit round-trip. Reading the DB-remaining after
                # a post-statement instant avoids both.
                remaining = _returned_remaining(result)
                acquired_at = loop.time()
                if remaining is None:
                    # No RETURNING row is an AUTHORITATIVE non-owner result:
                    # another replica owns an unexpired lease. Demote BEFORE
                    # committing — mirroring :meth:`renew`'s rowcount-0 path — so
                    # that a ``commit()`` failure on a flaky connection cannot
                    # re-raise into the generic ``except`` below, whose
                    # preservation branch (``_is_leader`` + unexpired deadline)
                    # would keep leadership and let ``run_if_leader`` run ANOTHER
                    # singleton body even though the database row is owned by a
                    # different leader. The non-owner verdict is captured before
                    # the commit and is authoritative regardless of whether the
                    # (functionally no-op) commit then succeeds. Only a genuine
                    # connection error observed BEFORE this authoritative row
                    # result may fall through to the preservation branch.
                    self._is_leader = False
                    self._lease_deadline = None
                    try:
                        await session.commit()
                    except Exception:
                        logger.warning(
                            "Leader lease acquisition observed a non-owner result (no row) but "
                            "the commit failed; treating the lease as not acquired leader_id=%s",
                            self._leader_id,
                            exc_info=True,
                        )
                    return False
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
            # reserved for an authoritative non-owner result (the no-row branch
            # ABOVE, which returns before reaching here even if its commit fails)
            # or a failure observed after the held lease has already expired.
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

        # A no-row (authoritative non-owner) result already returned False above,
        # so ``remaining`` is the DB-RETURNED remaining lease of a row this
        # statement wrote. Anchor the local deadline to it and promote.
        self._is_leader = True
        self._lease_deadline = acquired_at + remaining
        return True

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
            # Both dialects evaluate the unexpired-lease guard and the new expiry
            # on the database's own execution-time clock, so a renewal delayed
            # past the deadline (event-loop stall, row/write-lock wait) matches 0
            # rows instead of resurrecting a row whose ``expires_at`` has already
            # passed but no follower has yet claimed. A no-match RETURNS no row
            # and falls through to the lease-loss demotion below.
            renew_sql = _SQLITE_RENEW_SQL if _dialect_name(session) == "sqlite" else _POSTGRES_RENEW_SQL
            result = await session.execute(
                renew_sql,
                {"leader_id": self._leader_id, "ttl": ttl},
            )
            # The statement RETURNS the renewed lease's remaining (on the
            # database's OWN clock) or nothing. Read it BEFORE the commit: a
            # no-match means another replica now owns the lease (or the row had
            # expired), an authoritative verdict independent of whether the
            # (functionally no-op) commit then succeeds. Capture the monotonic
            # instant right AFTER the statement returns to anchor the renewed
            # deadline against the DB-remaining below.
            remaining = _returned_remaining(result)
            renew_anchor = loop.time()
            if remaining is None:
                # Demote on the authoritative lease loss BEFORE committing, so a
                # ``commit()`` failure on a flaky connection cannot re-raise out
                # of ``renew()`` and be misread by the heartbeat as a transient
                # renewal error. A transient error would keep the gated body
                # running as a believed-leader until another error or the local
                # deadline; a definitive rowcount-0 loss must demote and cancel
                # the body immediately.
                self._is_leader = False
                self._lease_deadline = None
                try:
                    await session.commit()
                except Exception:
                    logger.warning(
                        "Leader lease renewal observed lease loss (rowcount 0) but the commit "
                        "failed; treating the lease as lost leader_id=%s",
                        self._leader_id,
                        exc_info=True,
                    )
                return False
            await session.commit()

        # Extend the locally tracked deadline so a concurrent acquire that hits a
        # transient error keeps preserving leadership for the full renewed lease,
        # not just the original acquisition window. Anchor the POST-statement
        # monotonic instant to the DB-returned remaining so the deadline tracks
        # the database's authoritative expiry — never backdated by a lock wait,
        # never outrunning the row.
        self._lease_deadline = renew_anchor + remaining
        return True

    async def release(self) -> None:
        """Delete the lease row we hold so followers can take over immediately.

        Bodies detached after the cancellation grace may still be draining
        shielded singleton work as the former leader, so the early release
        first waits up to ``_RELEASE_DRAIN_GRACE_SECONDS`` for them WHILE it
        keeps renewing the lease on the heartbeat cadence. A body detached on
        the graceful-shutdown path is still the rightful leader, and with a
        short TTL (the minimum is 5s) the DB lease would otherwise expire
        during the drain wait — a follower could then acquire it and run the
        same singleton work concurrently with the still-draining body. Renewing
        while waiting keeps the lease alive for as long as the detached body may
        still act as leader. If any body is still draining after the grace, the
        row is left in place — the lease then expires after its TTL, roughly one
        more TTL past the last renewal, after which the body is treated as
        abandoned — because handing it to a follower while old gated work still
        runs would recreate the duplicate-singleton overlap the lease exists to
        prevent.

        Renewal ownership is handed off from the shutdown keeper (see
        :meth:`start_release_keeper`, started at shutdown-begin so the lease is
        renewed continuously through the whole scheduler stop sequence). The
        keeper is stopped FIRST so exactly one owner renews at a time: the keeper
        renewed within the last interval, so the lease is still valid across the
        synchronous handoff, and :meth:`_drain_detached_bodies` then renews on
        the same cadence while it waits. Stopping the keeper before the drain
        (rather than after) keeps a single renewal owner rather than two writers
        racing on the row.

        Failure to release must never block shutdown; the lease then simply
        expires after the TTL.
        """
        self._is_leader = False
        self._lease_deadline = None
        settings = get_settings()
        if not settings.leader_election_enabled:
            await self._stop_release_keeper()
            return
        await self._stop_release_keeper()
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
        except Exception as exc:
            # A transient ``database is locked`` while deleting the row is not a
            # release failure: the row is simply left in place and the lease
            # expires after its TTL, exactly the fallback the caller already
            # tolerates. Swallow it at DEBUG so shutdown never spams warnings on
            # SQLite write contention; surface anything else as before.
            if _is_locked_error(exc):
                logger.debug(
                    "Leader lease release contended on a locked database; leaving the row "
                    "to expire after its TTL leader_id=%s",
                    self._leader_id,
                    exc_info=True,
                )
                return
            logger.warning("Failed to release scheduler leader lease", exc_info=True)

    def start_release_keeper(self) -> None:
        """Start the single lease-renewal keeper for the graceful-shutdown window.

        Call this once at shutdown-begin, BEFORE stopping any scheduler. From
        that moment until :meth:`release` stops it, one owner renews the
        ``scheduler_leader`` row on the ``max(1, ttl // 3)`` cadence, closing the
        cross-scheduler stop-sequence gap: schedulers are stopped one at a time
        and only the last one triggers ``release``, so an earlier scheduler's
        ``stop()`` can detach a shielded leader-gated body — which cancels that
        scheduler's own heartbeat — while later schedulers are still stopping and
        ``release`` has not begun its own drain-renewal. Without this keeper the
        DB lease could expire in that window (with the minimum 5s TTL, a later
        scheduler that takes >= the TTL to drain is enough) while the detached
        body still runs as leader, letting a follower acquire the lease and run
        the same singleton work concurrently.

        Idempotent; a no-op when leader election is disabled. The keeper renews
        keyed on our ``leader_id`` (a harmless rowcount-0 no-op if this process
        never held the lease or a follower already took it over), so it is safe
        to start unconditionally at shutdown-begin regardless of current
        leadership.
        """
        if not get_settings().leader_election_enabled:
            return
        if self._release_keeper is not None and not self._release_keeper.done():
            return
        stop = asyncio.Event()
        self._release_keeper_stop = stop
        self._release_keeper = asyncio.create_task(self._run_release_keeper(stop))

    async def _run_release_keeper(self, stop: asyncio.Event) -> None:
        """Renew the held lease row on the ttl/3 cadence until asked to stop.

        This is the SINGLE renewal owner for the shutdown window that precedes
        ``release``. It renews BEFORE each wait so the lease is extended as soon
        as the keeper starts, and it consults neither ``_is_leader`` nor the
        locally tracked deadline: ``release`` clears both up-front, yet the row
        is still ours while detached bodies drain. Renewal errors are swallowed
        by :meth:`_renew_lease_row`; shutdown must always be able to proceed.
        """
        renew_interval = max(1, get_settings().leader_election_ttl_seconds // 3)
        try:
            while not stop.is_set():
                await self._renew_lease_row()
                try:
                    await asyncio.wait_for(stop.wait(), timeout=renew_interval)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass

    async def _stop_release_keeper(self) -> None:
        """Stop the shutdown lease-renewal keeper if it is running.

        Signals the keeper to exit and awaits it so renewal ownership passes
        cleanly to ``release``'s own bounded drain-renewal with no overlapping
        renewer. Cancels as a fallback so a keeper wedged inside a database call
        cannot pin shutdown; the outer release deadline (``app/main.py``'s
        ``_release_leader_lease_within``) abandons the whole release if even that
        stalls, so shutdown always proceeds.
        """
        keeper = self._release_keeper
        stop = self._release_keeper_stop
        self._release_keeper = None
        self._release_keeper_stop = None
        if keeper is None:
            return
        if stop is not None:
            stop.set()
        keeper.cancel()
        try:
            await keeper
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("Scheduler leader lease keeper failed during shutdown", exc_info=True)

    async def run_if_leader(self, fn: Callable[[], Awaitable[_T]]) -> _T | None:
        """Run ``fn`` only while holding the leader lease.

        Heartbeats the lease every ``max(1, ttl // 3)`` seconds while the body
        runs, except that each sleep is capped so the renewal it schedules
        BEGINS with enough budget to FINISH before the locally tracked lease
        deadline: the sleep is bounded by ``remaining - renew_timeout`` (clamped
        to ``[0, renew_interval]``) so that ``sleep + renew_timeout`` lands at or
        before the deadline. A lease seeded from a preserved acquire may have
        less than a renew interval — even less than a whole renewal time-box —
        left; sleeping the full interval (or the whole remaining) would start
        ``renew()`` at/after the deadline, so its time-box would run while the
        database row has already expired and the gated body would keep acting as
        leader for up to ``renew_timeout`` past the DB expiry while a follower is
        free to acquire the row. Each renewal attempt is time-boxed to
        ``ttl / 6``, and the wait is additionally bounded so it never extends
        past the local deadline: a renewal still in flight at the deadline is
        abandoned and demoted rather than left running, so the gated body is
        cancelled the instant the deadline is reached with no successful
        renewal. Two consecutive failed attempts, or any failed attempt at/after
        the locally tracked lease deadline, demote the holder no later than the
        lease TTL — EXCEPT when a concurrent heartbeat on this shared
        ``LeaderElection`` has advanced ``self._lease_deadline`` past this
        heartbeat's local deadline: because the election is a shared singleton
        across every scheduler, clearing the shared leadership on one
        heartbeat's local transient errors would cancel a sibling's still-valid
        leader work and make its next ``renew`` return ``False`` even though the
        DB lease is freshly held. In that case this heartbeat adopts the
        sibling's fresher deadline and keeps renewing; the shared flag is cleared
        only on an authoritative rowcount-0 loss or a genuinely expired shared
        lease. The time-box is enforced with ``asyncio.wait`` (not
        ``asyncio.wait_for``) so a renewal whose cancellation cleanup itself
        stalls — e.g. a blocked rollback in session teardown — cannot pin the
        heartbeat past the timeout: once the timeout elapses the attempt is
        abandoned and counted as an error immediately, without awaiting the
        renewal coroutine's unwinding. On lease loss
        the body is cancelled and awaited for at most
        ``_CANCEL_GRACE_SECONDS``; a body still draining shielded work after
        the grace is detached (its outcome is logged from a done callback),
        so ``run_if_leader`` itself returns within the grace of the loss.

        When ``run_if_leader`` is instead cancelled externally (graceful
        shutdown) while the lease is still held, the heartbeat keeps renewing
        the lease until the gated body has drained (bounded by the cancel
        grace); the heartbeat is stopped only after the body exits, so a body
        that honours cancellation slower than the remaining lease TTL cannot let
        the DB lease expire while it still runs as leader. If the body still has
        not exited when the cancel grace elapses it is detached (and the
        heartbeat stops), but the lease is not abandoned: ``release`` keeps
        renewing it on the same cadence while it waits for the detached body to
        drain, so the DB lease still cannot expire while a detached body may act
        as leader.

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
                    # ``run_if_leader`` is a SHARED singleton across every
                    # scheduler, so a concurrent heartbeat on the SAME
                    # ``LeaderElection`` instance may have renewed the row and
                    # advanced ``self._lease_deadline`` past this heartbeat's
                    # local view. Adopt that later shared deadline before judging
                    # expiry so a stale local deadline cannot demote (and cancel
                    # a sibling's still-valid leader work) while the DB lease is
                    # freshly held by the same process. ``self._lease_deadline``
                    # only ever advances via a real DB-confirmed acquire/renewal,
                    # so adopting it can never outrun the true lease.
                    if self._lease_deadline is not None and self._lease_deadline > lease_deadline:
                        lease_deadline = self._lease_deadline
                    remaining = lease_deadline - loop.time()
                    if remaining <= 0:
                        self._is_leader = False
                        self._lease_deadline = None
                        lease_lost = True
                        return
                    # Begin the renewal with enough budget to FINISH before the
                    # deadline: cap the sleep so ``sleep + renew_timeout`` lands
                    # at or before ``lease_deadline``. A preserved acquire can
                    # leave less than one renew interval — even less than a whole
                    # renewal time-box — on the lease; sleeping the full interval
                    # (or the whole remaining) would start ``renew()`` at/after
                    # the deadline, so its time-box would run while the database
                    # row has already expired and the gated body would keep
                    # acting as leader for up to ``renew_timeout`` past the DB
                    # expiry (a follower is free to acquire the row in that gap).
                    # When already within a time-box of the deadline the sleep is
                    # 0 and the renewal fires immediately.
                    sleep_budget = min(float(renew_interval), max(0.0, remaining - renew_timeout))
                    await asyncio.sleep(sleep_budget)
                    inflight = asyncio.ensure_future(self.renew())
                    # Time-box the attempt to ``renew_timeout``, but never wait
                    # PAST the local deadline: a renewal still in flight at the
                    # deadline is abandoned and demoted rather than left to keep
                    # the body running as leader past the lease expiry. Bounding
                    # the wait this way guarantees the heartbeat wakes AT the
                    # deadline (not a time-box later) so the gated body is
                    # cancelled the instant the deadline is reached with no
                    # successful renewal. ``asyncio.wait`` returns on the timeout
                    # without cancelling or awaiting ``inflight``, so a renewal
                    # whose cancellation cleanup itself hangs cannot pin the
                    # heartbeat past the time-box the way ``asyncio.wait_for``
                    # (which awaits the cancelled coroutine's unwinding) would.
                    renew_wait = min(renew_timeout, max(0.0, lease_deadline - loop.time()))
                    done, _ = await asyncio.wait({inflight}, timeout=renew_wait)
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
                        # This heartbeat lost local confidence (two consecutive
                        # transient renewal errors, or a failure at/after its
                        # local deadline). Before clearing the SHARED leadership
                        # state, consult the authoritative shared lease: because
                        # ``run_if_leader`` is a shared singleton across every
                        # scheduler, a concurrent heartbeat on the SAME instance
                        # may have renewed the row and advanced
                        # ``self._lease_deadline`` past this heartbeat's stale
                        # local deadline. Clearing ``self._is_leader`` /
                        # ``self._lease_deadline`` here would cancel that
                        # sibling's otherwise-valid leader work and make its next
                        # ``renew()`` return ``False`` without touching the
                        # database, even though the DB lease is freshly held.
                        # Demotion of the shared flag is authoritative only for a
                        # rowcount-0 loss (already cleared by ``renew`` and
                        # handled in the ``else`` branch) or a genuinely expired
                        # shared lease. When a sibling has advanced the shared
                        # deadline beyond this heartbeat's local view, adopt it
                        # and keep renewing rather than tearing down shared
                        # leadership; this heartbeat's own transient errors are
                        # not evidence the DB lease is gone.
                        if (
                            self._is_leader
                            and self._lease_deadline is not None
                            and self._lease_deadline > lease_deadline
                        ):
                            lease_deadline = self._lease_deadline
                            consecutive_errors = 0
                            continue
                        self._is_leader = False
                        self._lease_deadline = None
                        renewed = False
                    else:
                        consecutive_errors = 0
                        if renewed and self._lease_deadline is not None:
                            # ``renew`` anchored ``_lease_deadline`` to the lease
                            # the database RETURNed as remaining (its ``expires_at``
                            # minus the DB's own clock) from a monotonic instant
                            # captured AFTER its statement, so the working deadline
                            # tracks the DB's authoritative expiry — neither
                            # backdated by a lock wait nor outrunning the row.
                            # Adopt it rather than recomputing a pre-dispatch
                            # ``loop.time() + ttl`` here.
                            lease_deadline = self._lease_deadline
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
            # Shutdown-cancel path: ``run_if_leader`` was cancelled externally
            # (e.g. a scheduler's ``stop()``) while the lease is still HELD —
            # unlike the lease-loss branch above, which has already cancelled the
            # body BECAUSE the lease was lost. Here the gated body may still be
            # running as the rightful leader, e.g. draining a shielded token/
            # usage refresh. Cancel and drain it FIRST, while the heartbeat keeps
            # renewing the lease, and only stop the heartbeat once the body has
            # exited (bounded by the cancel grace). Stopping renewals first — the
            # old ordering — could let a body that honours cancellation slower
            # than the remaining lease TTL (e.g. TTL=5s with the 5s cancel grace)
            # outlive the DB lease, so a follower could acquire it and run the
            # same singleton work concurrently. On the lease-loss / heartbeat-
            # crash paths ``body_cancel_handled`` is already set, so the body is
            # not re-cancelled here and the (already-returned) heartbeat is simply
            # cancelled — the lease is genuinely gone there and MUST NOT be
            # renewed.
            if not body_cancel_handled and not body_task.done():
                await self._cancel_within_grace(body_task)
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Leader heartbeat failed during cleanup")

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
        """Wait for detached gated bodies, renewing the lease while they run.

        Returns ``True`` when no detached body remains running (the row is then
        safe to delete). A body detached on the graceful-shutdown path is still
        the rightful leader while it drains shielded singleton work, so the DB
        lease MUST NOT expire under it: this renews the lease on the heartbeat
        cadence (``max(1, ttl // 3)``) for as long as it waits. The wait is
        bounded by ``_RELEASE_DRAIN_GRACE_SECONDS`` so shutdown always proceeds;
        if a body is still draining when the grace elapses this returns
        ``False`` and the caller leaves the row for the lease to expire by TTL
        (the last renewal bought roughly one more TTL, after which the body is
        treated as abandoned). A renewal that finds rowcount 0 — the lease was
        already taken over on the lease-loss detach path — is a harmless no-op.
        """
        pending = {task for task in self._detached_bodies if not task.done()}
        if not pending:
            return True
        settings = get_settings()
        renew_interval = max(1, settings.leader_election_ttl_seconds // 3)
        loop = asyncio.get_running_loop()
        logger.info(
            "Waiting up to %.1fs for %d detached leader-gated task(s), renewing the lease "
            "meanwhile, before releasing it",
            _RELEASE_DRAIN_GRACE_SECONDS,
            len(pending),
        )
        deadline = loop.time() + _RELEASE_DRAIN_GRACE_SECONDS
        while pending:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False
            # Renew BEFORE each wait slice so the database lease cannot expire
            # while a detached body may still be acting as leader.
            await self._renew_lease_row()
            _, pending = await asyncio.wait(pending, timeout=min(float(renew_interval), remaining))
        return True

    async def _renew_lease_row(self) -> bool:
        """Extend the lease row's expiry keyed on our ``leader_id``.

        Unlike :meth:`renew` this consults neither the in-memory ``_is_leader``
        flag nor the locally tracked deadline: it is used by ``release`` while a
        body detached on the graceful-shutdown path may still be acting as
        leader, at which point ``_is_leader`` has already been cleared but the
        database row is still ours. Like :meth:`renew` the UPDATE is guarded on
        the lease still being unexpired, so it can never resurrect a row whose
        ``expires_at`` has already passed (a follower is then free to take it).
        Errors are swallowed (shutdown must proceed); the caller treats a
        failure — or an expired/taken-over no-match — as "lease not renewed".
        """
        settings = get_settings()
        ttl = settings.leader_election_ttl_seconds
        try:
            async with get_background_session() as session:
                # Guard the renewal on the lease still being unexpired at the
                # database's execution-time clock so a shutdown/drain renewal can
                # never resurrect a row whose ``expires_at`` has already passed.
                renew_sql = _SQLITE_RENEW_SQL if _dialect_name(session) == "sqlite" else _POSTGRES_RENEW_SQL
                result = await session.execute(
                    renew_sql,
                    {"leader_id": self._leader_id, "ttl": ttl},
                )
                # A RETURNING row means the guarded UPDATE extended the row; no
                # row means it was expired/taken over (a harmless no-op here).
                # The drain path holds no locally tracked deadline to re-anchor.
                renewed = _returned_remaining(result) is not None
                await session.commit()
                return renewed
        except Exception as exc:
            # This renewal is best-effort: the caller (the release keeper and the
            # drain loop) treats a ``False`` as "not renewed this cadence" and
            # renews again on the next tick, so a transient ``database is locked``
            # must not raise out of the shutdown path nor spam warnings. Log it at
            # DEBUG and let the next cadence retry; surface other errors as before.
            if _is_locked_error(exc):
                logger.debug(
                    "Leader lease renewal contended on a locked database; will retry next cadence leader_id=%s",
                    self._leader_id,
                    exc_info=True,
                )
                return False
            logger.warning(
                "Failed to renew leader lease while draining detached bodies leader_id=%s",
                self._leader_id,
                exc_info=True,
            )
            return False


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

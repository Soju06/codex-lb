from __future__ import annotations

import asyncio
import importlib
import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import OperationalError
from sqlalchemy.sql.dml import Delete
from sqlalchemy.sql.elements import TextClause

leader_election_module = importlib.import_module("app.core.scheduling.leader_election")

pytestmark = pytest.mark.unit


class _FakeResult:
    """Mimics a RETURNING result: one row carrying the remaining lease, or none.

    The acquire/renew statements ``RETURNING`` the lease still remaining (a
    single float) when they affect the row, and nothing when they match no row.
    ``rowcount`` drives which case a fake produces so existing tests keep their
    ``[1]`` / ``[0]`` scripting: ``1`` -> one row ``(remaining,)`` (the SQL's
    RETURNING output), ``0`` -> ``first()`` is ``None``.
    """

    def __init__(self, rowcount: int, remaining: float) -> None:
        self.rowcount = rowcount
        self._remaining = remaining

    def first(self) -> tuple[float] | None:
        if self.rowcount >= 1:
            return (self._remaining,)
        return None


class _FakeSession:
    def __init__(self, dialect_name: str, rowcounts: list[int]) -> None:
        self._dialect_name = dialect_name
        self._rowcounts = rowcounts
        # DB-returned remaining lease seconds for an affected row; ``_install``
        # sets it to the configured TTL so the anchored local deadline lands
        # ~TTL out, mirroring a fresh acquire/renewal on a real database.
        self._remaining = 30.0
        self.statements: list[Any] = []
        self.params: list[Any] = []
        self.commits = 0

    def get_bind(self) -> SimpleNamespace:
        return SimpleNamespace(dialect=SimpleNamespace(name=self._dialect_name))

    async def execute(self, statement: Any, params: Any = None) -> _FakeResult:
        self.statements.append(statement)
        self.params.append(params)
        return _FakeResult(self._rowcounts.pop(0), self._remaining)

    async def commit(self) -> None:
        self.commits += 1


def _install(
    monkeypatch: pytest.MonkeyPatch,
    session: Any,
    *,
    enabled: bool = True,
    ttl: int = 30,
) -> None:
    settings = SimpleNamespace(leader_election_enabled=enabled, leader_election_ttl_seconds=ttl)
    monkeypatch.setattr(leader_election_module, "get_settings", lambda: settings)
    # Model the DB RETURNING a full-TTL remaining lease on every affected row.
    if isinstance(session, _FakeSession):
        session._remaining = float(ttl)

    @asynccontextmanager
    async def _session_cm():
        yield session

    monkeypatch.setattr(leader_election_module, "get_background_session", _session_cm)


@pytest.mark.asyncio
async def test_try_acquire_returns_true_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession("postgresql", [])
    _install(monkeypatch, session, enabled=False)

    election = leader_election_module.LeaderElection(leader_id="node-a")

    assert await election.try_acquire() is True
    assert session.statements == []


@pytest.mark.asyncio
async def test_try_acquire_uses_database_clock_sql_on_postgres_dialect(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession("postgresql", [1])
    _install(monkeypatch, session)

    election = leader_election_module.LeaderElection(leader_id="node-a")

    assert await election.try_acquire() is True
    assert election.is_leader is True
    assert session.commits == 1
    [statement] = session.statements
    assert isinstance(statement, TextClause)
    sql = str(statement)
    assert "make_interval" in sql
    # The stored expiry uses the actual statement-execution clock so an acquire
    # (including a same-leader re-acquire) that waited on the row lock extends
    # from the current time and can never write expires_at backward.
    assert "clock_timestamp() + make_interval(secs => :ttl)" in sql
    assert "now() + make_interval" not in sql
    # Regression: the ON CONFLICT DO UPDATE SET clause must recompute the expiry
    # from clock_timestamp() in the current statement rather than copy
    # excluded.expires_at. ``excluded`` is the VALUES tuple, evaluated before the
    # statement blocked on the scheduler_leader row lock, so a re-acquire that
    # waited near or past the TTL would otherwise commit a pre-wait (stale)
    # expiry while try_acquire records a fresh local deadline after commit.
    conflict_clause = sql.split("DO UPDATE SET", 1)[1]
    assert "excluded.expires_at" not in conflict_clause
    assert "excluded.acquired_at" not in conflict_clause
    assert "expires_at = clock_timestamp() + make_interval(secs => :ttl)" in conflict_clause
    assert "acquired_at = clock_timestamp()" in conflict_clause
    # The upsert extends from the current clock on both the insert and the
    # conflict-update path, so clock_timestamp()+make_interval appears twice.
    assert sql.count("clock_timestamp() + make_interval(secs => :ttl)") == 2
    # The takeover predicate stays on the transaction snapshot clock.
    assert "expires_at < now()" in sql
    [params] = session.params
    assert params["leader_id"] == "node-a"
    assert params["ttl"] == 30


@pytest.mark.asyncio
async def test_renew_uses_current_statement_clock_on_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: PostgreSQL now()/transaction_timestamp() is fixed at
    # transaction start, so a renewal that blocks on the scheduler_leader row
    # lock would compute expires_at from a pre-lock timestamp; two overlapping
    # renewals could then commit out of order and move expires_at backward,
    # shortening the effective lease below the locally tracked deadline. The
    # renewal UPDATE must compute expires_at from clock_timestamp() (actual
    # statement-execution time) so a renewal that waited on the lock still
    # extends from the current time and the lease only moves forward.
    session = _FakeSession("postgresql", [1, 1])
    _install(monkeypatch, session)

    election = leader_election_module.LeaderElection(leader_id="node-a")
    assert await election.try_acquire() is True
    assert await election.renew() is True

    renew_statement = session.statements[1]
    assert isinstance(renew_statement, TextClause)
    renew_sql = str(renew_statement)
    assert "clock_timestamp() + make_interval(secs => :ttl)" in renew_sql
    assert "now()" not in renew_sql
    # Regression (P1): the renewal must be conditional on the lease still being
    # unexpired so a heartbeat delayed past expires_at (event-loop stall,
    # row-lock wait) cannot resurrect a dead row that no follower has claimed
    # yet. The guard is evaluated on the same statement-execution clock
    # (clock_timestamp()) as the SET expiry.
    assert "leader_id = :leader_id AND expires_at > clock_timestamp()" in renew_sql


@pytest.mark.asyncio
async def test_renew_guards_on_unexpired_lease_on_sqlite(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression (P1/P2): the SQLite renewal must carry the same unexpired-lease
    # guard, evaluated on SQLite's OWN execution-time clock on both sides, so a
    # renewal delayed past the deadline cannot extend a row whose expires_at has
    # already passed. The clock lives in the SQL (strftime('...','now')), not in
    # a Python value bound before execute.
    session = _FakeSession("sqlite", [1, 1])
    _install(monkeypatch, session)

    election = leader_election_module.LeaderElection(leader_id="node-a")
    assert await election.try_acquire() is True
    assert await election.renew() is True

    renew_statement = session.statements[1]
    assert isinstance(renew_statement, TextClause)
    renew_sql = str(renew_statement)
    # The guard and the new expiry both use SQLite's statement-execution clock.
    assert "expires_at > (strftime('%Y-%m-%d %H:%M:%f', 'now')" in renew_sql
    assert "SET expires_at = (strftime('%Y-%m-%d %H:%M:%f', 'now', '+' || :ttl || ' seconds')" in renew_sql
    assert session.params[1] == {"leader_id": "node-a", "ttl": 30}


@pytest.mark.asyncio
async def test_renew_after_expiry_demotes_and_does_not_extend(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression (P1): a renewal whose guarded UPDATE matches 0 rows because the
    # lease already expired (heartbeat delayed past expires_at while no follower
    # has claimed the row yet) MUST demote and MUST NOT extend the lease — the
    # guarded WHERE leaves the expired row untouched so a follower can take it.
    session = _FakeSession("sqlite", [1, 0])
    _install(monkeypatch, session, ttl=30)

    election = leader_election_module.LeaderElection(leader_id="node-a")
    assert await election.try_acquire() is True
    assert election._lease_deadline is not None

    assert await election.renew() is False
    assert election.is_leader is False
    assert election._lease_deadline is None
    # The renewal UPDATE carried the unexpired predicate evaluated on SQLite's
    # execution-time clock, so against a real database it matches 0 rows for an
    # expired lease rather than reviving it.
    assert "expires_at > (strftime('%Y-%m-%d %H:%M:%f', 'now')" in str(session.statements[1])


@pytest.mark.asyncio
async def test_sqlite_renew_delayed_past_expiry_matches_zero_rows_on_real_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression (P2): on SQLite a renewal can sit behind the single-writer lock
    # (busy_timeout ~30s) for longer than the lease TTL (min 5s). The guard and
    # the new expiry MUST be evaluated on SQLite's OWN execution-time clock, so a
    # renewal whose EXECUTION lands after expires_at has passed matches 0 rows
    # and demotes — it must NOT extend the row from a stale pre-wait instant.
    #
    # This exercises a REAL SQLite database and injects the delay INSIDE
    # session.execute (modelling the write-lock wait between issuing the
    # statement and SQLite actually running it). A pre-execute Python `now`
    # captured before that wait would still match the not-yet-expired lease and
    # extend it; the in-SQL clock evaluated at execution time does not.
    import sqlalchemy as sa

    from app.db.models import Base

    engine = sa.create_engine("sqlite://")  # single-connection in-memory DB
    Base.metadata.create_all(engine)
    conn = engine.connect()
    try:
        # Seed a lease owned by node-a that expires 0.4s from now, stored in the
        # same 6-digit-microsecond-width format the acquire path writes.
        conn.execute(
            sa.text(
                "INSERT INTO scheduler_leader (id, leader_id, acquired_at, expires_at) VALUES ("
                "1, 'node-a', strftime('%Y-%m-%d %H:%M:%f','now')||'000', "
                "strftime('%Y-%m-%d %H:%M:%f','now','+0.4 seconds')||'000')"
            )
        )
        conn.commit()
        expires_before = conn.exec_driver_sql("SELECT expires_at FROM scheduler_leader").scalar()

        class _DelayedSqliteSession:
            def __init__(self, delay: float) -> None:
                self._delay = delay

            def get_bind(self) -> SimpleNamespace:
                return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

            async def execute(self, statement: Any, params: Any = None) -> Any:
                await asyncio.sleep(self._delay)  # model the write-lock wait
                return conn.execute(statement, params or {})

            async def commit(self) -> None:
                conn.commit()

        _install(monkeypatch, _DelayedSqliteSession(delay=0.6), ttl=30)

        election = leader_election_module.LeaderElection(leader_id="node-a")
        election._is_leader = True
        election._lease_deadline = asyncio.get_running_loop().time() + 30

        # The renewal's execution lands ~0.6s in, past the 0.4s expiry.
        assert await election.renew() is False
        assert election.is_leader is False
        assert election._lease_deadline is None
        # The guarded UPDATE left the expired row untouched (not extended).
        expires_after = conn.exec_driver_sql("SELECT expires_at FROM scheduler_leader").scalar()
        assert expires_after == expires_before
    finally:
        conn.close()
        engine.dispose()


class _DelayedRealSqliteSession:
    """Real-SQLite session whose ``execute`` sleeps before running the statement.

    The sleep models the write-lock (``busy_timeout``) wait between issuing a
    statement and SQLite actually executing it, so the database stamps
    ``expires_at`` / evaluates ``RETURNING`` only after the delay — exactly when
    a pre-statement Python instant would already be stale.
    """

    def __init__(self, conn: Any, delay: float) -> None:
        self._conn = conn
        self._delay = delay

    def get_bind(self) -> SimpleNamespace:
        return SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))

    async def execute(self, statement: Any, params: Any = None) -> Any:
        await asyncio.sleep(self._delay)  # model the write-lock wait
        return self._conn.execute(statement, params or {})

    async def commit(self) -> None:
        self._conn.commit()


def _db_remaining_seconds(conn: Any) -> float:
    """Lease still remaining per the database's OWN clock (seconds)."""
    return float(
        conn.exec_driver_sql(
            "SELECT (julianday(expires_at) - julianday('now')) * 86400.0 FROM scheduler_leader"
        ).scalar()
    )


@pytest.mark.asyncio
async def test_acquire_deadline_reflects_full_remaining_after_lock_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression (P2, root cause): on a lock-delayed acquire the statement's
    # execution — and thus the DB's expires_at stamp — lands well after the
    # instant the acquire was issued. Anchoring the local deadline to a
    # PRE-statement Python instant would BACKDATE it: with a lock wait longer
    # than the TTL the acquire returns with an already-expired local deadline
    # and run_if_leader would immediately skip/cancel leader work even though the
    # DB lease is genuinely fresh for a full TTL. The deadline MUST instead
    # reflect ~the full remaining TTL the database RETURNED, anchored to an
    # instant captured AFTER the statement executed.
    import sqlalchemy as sa

    from app.db.models import Base

    ttl = 1  # a short TTL; the modelled lock wait below exceeds it
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    conn = engine.connect()
    try:
        _install(monkeypatch, _DelayedRealSqliteSession(conn, delay=1.5), ttl=ttl)

        election = leader_election_module.LeaderElection(leader_id="node-a")
        loop = asyncio.get_running_loop()
        assert await election.try_acquire() is True
        assert election.is_leader is True
        assert election._lease_deadline is not None

        local_remaining = election._lease_deadline - loop.time()
        # Pre-fix (pre-statement anchor) this is ~ttl - lock_wait = -0.5s, i.e.
        # already expired on arrival. Post-fix it reflects the full ~1s TTL the
        # DB granted, so it is comfortably positive and near the TTL.
        assert local_remaining > 0.5
        assert local_remaining <= ttl + 0.3
    finally:
        conn.close()
        engine.dispose()


@pytest.mark.asyncio
async def test_acquire_deadline_does_not_outrun_db_expiry_on_real_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression (P2, invariant): the local monotonic deadline MUST NOT outrun
    # the database's authoritative expiry. Because it is anchored to the
    # DB-RETURNED remaining lease (expires_at minus the DB's own clock), the
    # local remaining tracks the DB remaining precisely — neither backdated nor
    # overshooting — even across a modelled lock wait.
    import sqlalchemy as sa

    from app.db.models import Base

    ttl = 5
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    conn = engine.connect()
    try:
        _install(monkeypatch, _DelayedRealSqliteSession(conn, delay=0.3), ttl=ttl)

        election = leader_election_module.LeaderElection(leader_id="node-a")
        loop = asyncio.get_running_loop()
        assert await election.try_acquire() is True
        assert election._lease_deadline is not None

        # Measure both clocks back-to-back so the comparison is not skewed.
        local_remaining = election._lease_deadline - loop.time()
        db_remaining = _db_remaining_seconds(conn)
        # The local deadline does not outrun the DB expiry (bar sub-second
        # transport slack) and is not backdated: it tracks the DB remaining.
        assert local_remaining <= db_remaining + 0.25
        assert local_remaining >= db_remaining - 0.25
    finally:
        conn.close()
        engine.dispose()


@pytest.mark.asyncio
async def test_renew_deadline_reflects_full_remaining_after_lock_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression (P2, root cause): the same anchor fix applies to renew. A
    # renewal whose execution is delayed behind the write lock extends
    # expires_at from the DB's execution-time clock; the local deadline MUST be
    # re-anchored to the DB-RETURNED remaining from an instant AFTER the
    # statement, not a pre-statement instant that would land the renewed
    # deadline in the past.
    import sqlalchemy as sa

    from app.db.models import Base

    ttl = 1
    engine = sa.create_engine("sqlite://")
    Base.metadata.create_all(engine)
    conn = engine.connect()
    try:
        # Seed a lease owned by node-a that is still valid when the renewal is
        # issued but whose renewal execution is delayed past the original expiry.
        conn.execute(
            sa.text(
                "INSERT INTO scheduler_leader (id, leader_id, acquired_at, expires_at) VALUES ("
                "1, 'node-a', strftime('%Y-%m-%d %H:%M:%f','now')||'000', "
                "strftime('%Y-%m-%d %H:%M:%f','now','+3 seconds')||'000')"
            )
        )
        conn.commit()
        _install(monkeypatch, _DelayedRealSqliteSession(conn, delay=1.2), ttl=ttl)

        election = leader_election_module.LeaderElection(leader_id="node-a")
        election._is_leader = True
        loop = asyncio.get_running_loop()
        # A stale pre-statement deadline the buggy path would have kept.
        election._lease_deadline = loop.time() - 5

        assert await election.renew() is True
        assert election.is_leader is True
        assert election._lease_deadline is not None

        local_remaining = election._lease_deadline - loop.time()
        db_remaining = _db_remaining_seconds(conn)
        # The renewed deadline reflects the fresh ~1s TTL, not a backdated value,
        # and still does not outrun the DB expiry.
        assert local_remaining > 0.5
        assert local_remaining <= db_remaining + 0.25
    finally:
        conn.close()
        engine.dispose()


@pytest.mark.asyncio
async def test_drain_renewal_guards_on_unexpired_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression (P1): the release/drain renewal path (_renew_lease_row) must
    # likewise be guarded so it cannot resurrect an expired lease while a
    # detached shutdown body drains.
    session = _FakeSession("sqlite", [1])
    _install(monkeypatch, session)

    election = leader_election_module.LeaderElection(leader_id="node-a")
    assert await election._renew_lease_row() is True

    statement = session.statements[0]
    assert isinstance(statement, TextClause)
    assert "expires_at > (strftime('%Y-%m-%d %H:%M:%f', 'now')" in str(statement)

    # The PostgreSQL drain renewal reuses the guarded _POSTGRES_RENEW_SQL text.
    pg_session = _FakeSession("postgresql", [1])
    _install(monkeypatch, pg_session)
    pg_election = leader_election_module.LeaderElection(leader_id="node-a")
    assert await pg_election._renew_lease_row() is True
    assert "expires_at > clock_timestamp()" in str(pg_session.statements[0])


@pytest.mark.asyncio
async def test_try_acquire_uses_execution_time_clock_upsert_on_sqlite_dialect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression (P2): the SQLite acquire upsert must stamp acquired_at/expires_at
    # and evaluate the takeover predicate from SQLite's OWN execution-time clock
    # (strftime('...','now')), not a Python value bound before execute — so an
    # upsert that waited on the single-writer lock extends from the current time.
    session = _FakeSession("sqlite", [1])
    _install(monkeypatch, session)

    election = leader_election_module.LeaderElection(leader_id="node-a")

    assert await election.try_acquire() is True
    [statement] = session.statements
    assert isinstance(statement, TextClause)
    sql = str(statement)
    assert "INSERT INTO scheduler_leader" in sql
    assert "ON CONFLICT (id) DO UPDATE" in sql
    # The stored expiry uses the execution-time clock on both the insert and the
    # conflict-update path (recomputed, not copied from excluded).
    assert "excluded.expires_at" not in sql
    assert sql.count("strftime('%Y-%m-%d %H:%M:%f', 'now', '+' || :ttl || ' seconds')") == 2
    # The takeover predicate necessarily shares that same execution-time clock.
    assert "scheduler_leader.expires_at < (strftime('%Y-%m-%d %H:%M:%f', 'now')" in sql
    assert session.params[0] == {"leader_id": "node-a", "ttl": 30}


@pytest.mark.asyncio
async def test_dialect_selection_ignores_database_url_text(monkeypatch: pytest.MonkeyPatch) -> None:
    # A postgres engine whose URL merely contains the substring "sqlite"
    # (e.g. in credentials) must still take the postgres arbitration path:
    # selection derives from the engine dialect, never from URL text.
    session = _FakeSession("postgresql", [1])
    settings = SimpleNamespace(
        leader_election_enabled=True,
        leader_election_ttl_seconds=30,
        database_url="postgresql+asyncpg://sqlite-user:pw@db/codex",
    )
    monkeypatch.setattr(leader_election_module, "get_settings", lambda: settings)

    @asynccontextmanager
    async def _session_cm():
        yield session

    monkeypatch.setattr(leader_election_module, "get_background_session", _session_cm)

    election = leader_election_module.LeaderElection(leader_id="node-a")

    assert await election.try_acquire() is True
    [statement] = session.statements
    assert isinstance(statement, TextClause)
    assert "make_interval" in str(statement)


@pytest.mark.asyncio
async def test_try_acquire_loses_on_rowcount_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession("postgresql", [0])
    _install(monkeypatch, session)

    election = leader_election_module.LeaderElection(leader_id="node-b")

    assert await election.try_acquire() is False
    assert election.is_leader is False


@pytest.mark.asyncio
async def test_try_acquire_defaults_to_non_leader_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BrokenSession(_FakeSession):
        async def execute(self, statement: Any, params: Any = None) -> _FakeResult:
            raise RuntimeError("db down")

    session = _BrokenSession("postgresql", [])
    _install(monkeypatch, session)

    election = leader_election_module.LeaderElection(leader_id="node-a")
    election._is_leader = True

    assert await election.try_acquire() is False
    assert election.is_leader is False


@pytest.mark.asyncio
async def test_try_acquire_preserves_held_lease_on_transient_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: the leader election is a shared singleton across every
    # singleton scheduler, so a second scheduler tick can call try_acquire
    # while a concurrently-running gated body still validly holds the lease.
    # A transient DB error on that acquire must NOT demote the held, unexpired
    # lease — otherwise the running body's next renew() returns False without
    # touching the database and cancels otherwise-valid leader work.
    class _AcquireThenErrorSession(_FakeSession):
        def __init__(self) -> None:
            super().__init__("postgresql", [1])
            self.calls = 0

        async def execute(self, statement: Any, params: Any = None) -> _FakeResult:
            self.calls += 1
            if self.calls == 1:
                return await super().execute(statement, params)
            raise RuntimeError("db down")

    session = _AcquireThenErrorSession()
    _install(monkeypatch, session, ttl=30)

    election = leader_election_module.LeaderElection(leader_id="node-a")
    assert await election.try_acquire() is True
    assert election.is_leader is True

    # A second concurrent acquire hits a transient error while the held lease
    # is still valid: leadership is preserved so the running body's renewals
    # keep hitting the database instead of demoting on a stale local flag.
    assert await election.try_acquire() is True
    assert election.is_leader is True


@pytest.mark.asyncio
async def test_try_acquire_demotes_on_transient_error_after_lease_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    # The preservation above is bounded by the locally tracked lease deadline:
    # once that deadline has passed a transient acquire error must demote,
    # because the lease can no longer be assumed to be held.
    class _AcquireThenErrorSession(_FakeSession):
        def __init__(self) -> None:
            super().__init__("postgresql", [1])
            self.calls = 0

        async def execute(self, statement: Any, params: Any = None) -> _FakeResult:
            self.calls += 1
            if self.calls == 1:
                return await super().execute(statement, params)
            raise RuntimeError("db down")

    session = _AcquireThenErrorSession()
    _install(monkeypatch, session, ttl=30)

    election = leader_election_module.LeaderElection(leader_id="node-a")
    assert await election.try_acquire() is True
    # Force the held lease's local deadline into the past.
    election._lease_deadline = asyncio.get_running_loop().time() - 1

    assert await election.try_acquire() is False
    assert election.is_leader is False
    assert election._lease_deadline is None


@pytest.mark.asyncio
async def test_try_acquire_demotes_on_no_row_even_when_commit_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression (P2): try_acquire is the acquire-path analog of the renew()
    # rowcount-0 fix. When this instance already holds a valid lease and an
    # acquire's upsert RETURNS no row (an AUTHORITATIVE non-owner result: another
    # replica owns the unexpired lease), the demotion must be applied BEFORE the
    # commit so a commit() failure on a flaky connection cannot re-raise into the
    # generic except, whose preservation branch (_is_leader + unexpired deadline)
    # would otherwise return True and let run_if_leader run a SECOND singleton
    # body against a row owned by a different leader. The no-row verdict is
    # authoritative regardless of whether the (no-op) commit then succeeds.
    class _NoRowThenCommitFailsSession(_FakeSession):
        async def commit(self) -> None:
            self.commits += 1
            # First commit is the initial acquire (must succeed); the second
            # acquire observes a non-owner no-row result and its commit fails.
            if self.commits >= 2:
                raise RuntimeError("commit failed after takeover")

    session = _NoRowThenCommitFailsSession("postgresql", [1, 0])
    _install(monkeypatch, session, ttl=30)

    election = leader_election_module.LeaderElection(leader_id="node-a")
    assert await election.try_acquire() is True
    assert election.is_leader is True
    # Held deadline is still unexpired, so the buggy path would preserve True.
    assert election._lease_deadline is not None
    assert election._lease_deadline > asyncio.get_running_loop().time()

    # The second acquire returns no row (another replica owns the lease) and its
    # commit raises: try_acquire must return False (not preserve True, not raise)
    # and demote so run_if_leader does not run another singleton body.
    assert await election.try_acquire() is False
    assert election.is_leader is False
    assert election._lease_deadline is None


@pytest.mark.asyncio
async def test_renew_demotes_on_rowcount_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession("postgresql", [1, 0])
    _install(monkeypatch, session)

    election = leader_election_module.LeaderElection(leader_id="node-a")
    assert await election.try_acquire() is True

    assert await election.renew() is False
    assert election.is_leader is False


@pytest.mark.asyncio
async def test_renew_demotes_on_rowcount_zero_even_when_commit_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression (P2): when the renewal UPDATE affects rowcount 0 (another
    # replica took over the lease) but the following commit() raises on a flaky
    # connection, renew() must still demote — the rowcount-0 verdict is captured
    # before the commit and is authoritative. The pre-fix code committed after
    # the rowcount check, so a commit failure re-raised out of renew() before the
    # demotion block; the heartbeat then treated a definitive lease loss as a
    # transient renewal error and kept the gated body running as a believed
    # leader until another error or the local deadline.
    class _CommitFailsOnRenewSession(_FakeSession):
        async def commit(self) -> None:
            self.commits += 1
            # First commit is the acquire (must succeed); the renewal commit fails.
            if self.commits >= 2:
                raise RuntimeError("commit failed mid-takeover")

    session = _CommitFailsOnRenewSession("postgresql", [1, 0])
    _install(monkeypatch, session)

    election = leader_election_module.LeaderElection(leader_id="node-a")
    assert await election.try_acquire() is True

    # renew() must return False (not raise) and demote, despite the commit failure.
    assert await election.renew() is False
    assert election.is_leader is False
    assert election._lease_deadline is None


@pytest.mark.asyncio
async def test_acquire_deadline_anchored_after_statement_not_after_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    # Holistic hardening: the locally tracked lease deadline is anchored to a
    # monotonic instant captured right AFTER the acquire statement returns (plus
    # the DB-RETURNED remaining lease), NOT after commit(). Anchoring after a
    # slow commit round-trip would push the deadline past the true DB expiry and
    # let the heartbeat believe it still leads after the row had expired. A slow
    # commit exposes the difference: the recorded deadline must stay within ~ttl
    # of the pre-call instant, not ttl + commit_latency. (The complementary
    # backdate failure — anchoring a PRE-statement instant so a lock-delayed
    # acquire seeds an already-expired deadline — is covered on a real SQLite DB
    # by test_acquire_deadline_reflects_full_remaining_after_lock_delay.)
    ttl = 30

    class _SlowCommitSession(_FakeSession):
        async def commit(self) -> None:
            self.commits += 1
            await asyncio.sleep(0.5)

    session = _SlowCommitSession("postgresql", [1])
    _install(monkeypatch, session, ttl=ttl)

    election = leader_election_module.LeaderElection(leader_id="node-a")
    loop = asyncio.get_running_loop()
    before = loop.time()
    assert await election.try_acquire() is True

    assert election._lease_deadline is not None
    # Pre-fix (deadline read after the 0.5s commit) would be ~before + ttl + 0.5.
    assert election._lease_deadline - before < ttl + 0.2
    assert election._lease_deadline - before > ttl - 0.2


@pytest.mark.asyncio
async def test_renew_extends_on_rowcount_one(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession("sqlite", [1, 1])
    _install(monkeypatch, session)

    election = leader_election_module.LeaderElection(leader_id="node-a")
    assert await election.try_acquire() is True

    assert await election.renew() is True
    assert election.is_leader is True
    assert isinstance(session.statements[1], TextClause)


@pytest.mark.asyncio
async def test_renew_returns_false_when_not_leader_without_db_access(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession("postgresql", [])
    _install(monkeypatch, session)

    election = leader_election_module.LeaderElection(leader_id="node-a")

    assert await election.renew() is False
    assert session.statements == []


@pytest.mark.asyncio
async def test_release_deletes_own_row_and_demotes(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession("sqlite", [1, 1])
    _install(monkeypatch, session)

    election = leader_election_module.LeaderElection(leader_id="node-a")
    assert await election.try_acquire() is True

    await election.release()

    assert election.is_leader is False
    assert isinstance(session.statements[1], Delete)
    compiled = str(session.statements[1].compile(dialect=postgresql.dialect()))
    assert "DELETE FROM scheduler_leader" in compiled


@pytest.mark.asyncio
async def test_release_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class _BrokenSession(_FakeSession):
        async def execute(self, statement: Any, params: Any = None) -> _FakeResult:
            raise RuntimeError("db down")

    session = _BrokenSession("sqlite", [])
    _install(monkeypatch, session)

    election = leader_election_module.LeaderElection(leader_id="node-a")
    election._is_leader = True

    await election.release()

    assert election.is_leader is False


def _make_locked_error() -> OperationalError:
    """Build a SQLAlchemy OperationalError wrapping a SQLite ``database is locked``."""
    return OperationalError("UPDATE scheduler_leader ...", {}, Exception("database is locked"))


@pytest.mark.asyncio
async def test_renew_lease_row_swallows_database_locked_at_debug(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Regression (CI, #1253): the best-effort release-keeper/drain renewal runs
    # on the shared single-writer SQLite file and can lose the write-lock race
    # against the app's other DB work. A transient ``database is locked`` there
    # must NOT propagate or spam a warning — it is swallowed at DEBUG and the
    # next cadence retries. Any OTHER error still surfaces as a warning.
    class _LockedSession(_FakeSession):
        async def execute(self, statement: Any, params: Any = None) -> _FakeResult:
            raise _make_locked_error()

    session = _LockedSession("sqlite", [])
    _install(monkeypatch, session)

    election = leader_election_module.LeaderElection(leader_id="node-a")
    with caplog.at_level(logging.DEBUG, logger="app.core.scheduling.leader_election"):
        assert await election._renew_lease_row() is False

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings == []
    debug = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("contended on a locked database" in r.getMessage() for r in debug)


@pytest.mark.asyncio
async def test_renew_lease_row_still_warns_on_non_lock_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # A non-lock failure keeps its WARNING so genuine faults stay visible.
    class _BrokenSession(_FakeSession):
        async def execute(self, statement: Any, params: Any = None) -> _FakeResult:
            raise RuntimeError("db down")

    session = _BrokenSession("sqlite", [])
    _install(monkeypatch, session)

    election = leader_election_module.LeaderElection(leader_id="node-a")
    with caplog.at_level(logging.DEBUG, logger="app.core.scheduling.leader_election"):
        assert await election._renew_lease_row() is False

    assert any(
        r.levelno >= logging.WARNING and "Failed to renew leader lease" in r.getMessage() for r in caplog.records
    )


@pytest.mark.asyncio
async def test_release_swallows_database_locked_at_debug(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Regression (CI, #1253): a ``database is locked`` while deleting the lease
    # row at shutdown is not a release failure — the row is left to expire after
    # its TTL, the fallback the caller already tolerates. It must be swallowed at
    # DEBUG, not logged as "Failed to release scheduler leader lease" (WARNING),
    # which spammed the integration-core teardown when leader election contended
    # with the schema DROP TABLE.
    class _LockedDeleteSession(_FakeSession):
        async def execute(self, statement: Any, params: Any = None) -> _FakeResult:
            raise _make_locked_error()

    session = _LockedDeleteSession("sqlite", [])
    _install(monkeypatch, session)

    election = leader_election_module.LeaderElection(leader_id="node-a")
    election._is_leader = True
    with caplog.at_level(logging.DEBUG, logger="app.core.scheduling.leader_election"):
        await election.release()

    assert election.is_leader is False
    assert not any("Failed to release scheduler leader lease" in r.getMessage() for r in caplog.records)
    assert any("release contended on a locked database" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_run_if_leader_skips_body_when_not_leader(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession("postgresql", [0])
    _install(monkeypatch, session)

    election = leader_election_module.LeaderElection(leader_id="node-b")
    ran = False

    async def _body() -> str:
        nonlocal ran
        ran = True
        return "ran"

    assert await election.run_if_leader(_body) is None
    assert ran is False


@pytest.mark.asyncio
async def test_run_if_leader_returns_body_result(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession("postgresql", [1])
    _install(monkeypatch, session)

    election = leader_election_module.LeaderElection(leader_id="node-a")

    async def _body() -> float:
        return 12.5

    assert await election.run_if_leader(_body) == 12.5


@pytest.mark.asyncio
async def test_run_if_leader_demotes_when_renewal_hangs(monkeypatch: pytest.MonkeyPatch) -> None:
    # Renewal attempts are time-boxed to ttl / 6: a database that accepts the
    # acquire but then hangs on every renewal must demote the leader no later
    # than the lease TTL instead of extending leadership by the pool timeout.
    class _HangingRenewSession(_FakeSession):
        def __init__(self) -> None:
            super().__init__("postgresql", [1])
            self.calls = 0

        async def execute(self, statement: Any, params: Any = None) -> _FakeResult:
            self.calls += 1
            if self.calls == 1:
                return await super().execute(statement, params)
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    session = _HangingRenewSession()
    _install(monkeypatch, session, ttl=3)

    election = leader_election_module.LeaderElection(leader_id="node-a")
    body_cancelled = asyncio.Event()

    async def _body() -> str:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            body_cancelled.set()
            raise
        return "ran"

    loop = asyncio.get_running_loop()
    start = loop.time()
    result = await election.run_if_leader(_body)

    assert result is None
    assert election.is_leader is False
    assert body_cancelled.is_set()
    # Two time-boxed attempts (interval 1s + timeout 1s each) demote by ~4s,
    # i.e. within the 3s TTL plus scheduling slack — never the pool timeout.
    assert loop.time() - start < 6.0
    assert session.calls >= 3


@pytest.mark.asyncio
async def test_run_if_leader_demotes_when_renewal_cancellation_hangs(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: asyncio.wait_for awaits the cancelled renewal's unwinding
    # before returning, so a renewal whose cancellation/cleanup itself hangs
    # (e.g. a blocked rollback during session teardown) would pin the heartbeat
    # and never reach demotion, letting the gated body outlive an expired lease.
    # The heartbeat must count the attempt as an error on the timeout deadline
    # regardless of whether the renewal coroutine has finished unwinding.
    unblock = asyncio.Event()

    class _CancellationHangingRenewSession(_FakeSession):
        def __init__(self) -> None:
            super().__init__("postgresql", [1])
            self.calls = 0

        async def execute(self, statement: Any, params: Any = None) -> _FakeResult:
            self.calls += 1
            if self.calls == 1:
                return await super().execute(statement, params)
            # Model a renewal whose cancellation does not unwind promptly: it
            # swallows CancelledError and keeps hanging until explicitly
            # unblocked, as a stuck driver / blocked rollback would.
            while True:
                try:
                    await unblock.wait()
                    return _FakeResult(1, self._remaining)
                except asyncio.CancelledError:
                    if unblock.is_set():
                        raise
                    continue

    session = _CancellationHangingRenewSession()
    _install(monkeypatch, session, ttl=3)

    election = leader_election_module.LeaderElection(leader_id="node-a")
    body_cancelled = asyncio.Event()

    async def _body() -> str:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            body_cancelled.set()
            raise
        return "ran"

    loop = asyncio.get_running_loop()
    start = loop.time()
    result = await election.run_if_leader(_body)

    assert result is None
    assert election.is_leader is False
    assert body_cancelled.is_set()
    # Demotion happened on the time-box deadlines (~4s for ttl=3), not after
    # the still-hung renewal finished unwinding (which never happens here).
    assert loop.time() - start < 6.0
    assert session.calls >= 3
    # The stalled renewal was abandoned, not awaited.
    assert election._abandoned_renewals

    # Release the abandoned renewal task(s) so the loop can finalize cleanly.
    unblock.set()
    if election._abandoned_renewals:
        await asyncio.wait(set(election._abandoned_renewals), timeout=1)


@pytest.mark.asyncio
async def test_run_if_leader_preserved_acquire_does_not_extend_local_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression: try_acquire() may return True WITHOUT extending the database
    # expires_at — the "preserve active leadership on a transient acquire
    # error" path keeps an already-held lease alive but performs no DB write.
    # run_if_leader must NOT treat that preserved acquire as a fresh
    # acquisition: seeding its local heartbeat deadline with a full TTL would
    # push the local deadline PAST the true DB lease expiry, so the heartbeat
    # could keep believing it leads after a follower legitimately took over the
    # row. The local deadline must stay at the last DB-confirmed expiry, so a
    # leader whose renewals keep failing demotes no later than that expiry and a
    # follower can take over once the true DB lease expires.
    ttl = 6

    class _AllErrorSession(_FakeSession):
        # Every DB call fails: models a database that goes unreachable right as
        # the gate re-acquires, so try_acquire preserves the held lease and
        # every subsequent renewal errors. The failed acquire never extends the
        # stored expires_at.
        def __init__(self) -> None:
            super().__init__("postgresql", [])
            self.execute_calls = 0

        async def execute(self, statement: Any, params: Any = None) -> _FakeResult:
            self.execute_calls += 1
            raise RuntimeError("db down")

    session = _AllErrorSession()
    _install(monkeypatch, session, ttl=ttl)

    election = leader_election_module.LeaderElection(leader_id="node-a")
    loop = asyncio.get_running_loop()
    # This instance already holds a lease (e.g. a prior acquire/renew by another
    # singleton scheduler sharing this election) whose DB-confirmed deadline is
    # still valid at entry but expires well before the first renewal fires
    # (renew_interval = ttl // 3 = 2s).
    election._is_leader = True
    election._lease_deadline = loop.time() + 0.5

    body_cancelled = asyncio.Event()

    async def _body() -> str:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            body_cancelled.set()
            raise
        return "ran"

    start = loop.time()
    result = await election.run_if_leader(_body)

    assert result is None
    assert election.is_leader is False
    assert body_cancelled.is_set()
    # With the fix the local deadline stays at the ~0.5s DB-confirmed expiry.
    # Because that is within one renewal time-box (ttl / 6 = 1s) of the
    # deadline, the heartbeat renews IMMEDIATELY (capped sleep = 0) rather than
    # sleeping a renew interval; the two instantly-failing renewals demote via
    # the consecutive-error cap, all still at ~0s. The buggy full-TTL reset would
    # instead sleep a full 2s interval between renewals and keep leadership until
    # ~4s, outrunning the true DB lease.
    assert loop.time() - start < 3.0
    # One preserved acquire attempt + two instantly-failing renewals (the
    # consecutive-error cap), never a full-TTL renewal window.
    assert session.execute_calls == 3


@pytest.mark.asyncio
async def test_run_if_leader_detaches_shielded_body_after_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    # Bodies that shield in-flight singleton refreshes (usage/token
    # singleflights) drain them after a cancellation request. The gate must
    # stop awaiting after the bounded grace and let the shielded work finish
    # detached instead of pinning run_if_leader on the upstream call.
    session = _FakeSession("postgresql", [1, 0])  # acquire wins, renewal observes a stolen lease
    _install(monkeypatch, session, ttl=3)
    monkeypatch.setattr(leader_election_module, "_CANCEL_GRACE_SECONDS", 0.2)

    release = asyncio.Event()
    body_finished = asyncio.Event()
    inner_task: asyncio.Task[None] | None = None

    async def _inner() -> None:
        await release.wait()

    async def _body() -> None:
        nonlocal inner_task
        inner_task = asyncio.create_task(_inner())
        try:
            await asyncio.shield(inner_task)
        except asyncio.CancelledError:
            # Drain the shielded work before propagating, like the
            # auth-guardian and usage-refresh singleflight bodies do.
            await inner_task
            body_finished.set()
            raise

    election = leader_election_module.LeaderElection(leader_id="node-a")
    loop = asyncio.get_running_loop()
    start = loop.time()
    result = await election.run_if_leader(_body)

    assert result is None
    assert election.is_leader is False
    # The gate returned within the grace of the lease loss (~1s renew +
    # 0.2s grace), not after the shielded inner work completed.
    assert loop.time() - start < 3.0
    assert inner_task is not None
    assert not inner_task.done()
    assert not body_finished.is_set()

    release.set()
    await inner_task
    await asyncio.wait_for(body_finished.wait(), timeout=1)


async def _detach_shielded_body(
    monkeypatch: pytest.MonkeyPatch,
    session: _FakeSession,
) -> tuple[Any, asyncio.Event, asyncio.Event]:
    """Drive ``run_if_leader`` through lease loss so its body gets detached.

    Returns the election plus the release/finished events of the still
    draining shielded body.
    """
    monkeypatch.setattr(leader_election_module, "_CANCEL_GRACE_SECONDS", 0.2)

    release = asyncio.Event()
    body_finished = asyncio.Event()

    async def _body() -> None:
        inner = asyncio.create_task(release.wait())
        try:
            await asyncio.shield(inner)
        except asyncio.CancelledError:
            await inner
            body_finished.set()
            raise

    election = leader_election_module.LeaderElection(leader_id="node-a")
    assert await election.run_if_leader(_body) is None
    assert len(election._detached_bodies) == 1
    return election, release, body_finished


@pytest.mark.asyncio
async def test_release_waits_for_detached_body_then_deletes_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    # Shutdown ordering: scheduler stop() can return with a gated body
    # detached and still draining shielded work as the former leader.
    # release() must wait for that body before deleting the lease row so a
    # follower cannot become leader while the old work still runs.
    # acquire, stolen renewal, then drain-renewal(s) while waiting, then the
    # release delete. The list is padded so any extra drain renewal caused by
    # scheduling jitter cannot exhaust the rowcounts.
    session = _FakeSession("postgresql", [1, 0] + [1] * 8)
    _install(monkeypatch, session, ttl=3)
    election, release, body_finished = await _detach_shielded_body(monkeypatch, session)

    asyncio.get_running_loop().call_later(0.1, release.set)
    await election.release()

    assert body_finished.is_set()
    assert election.is_leader is False
    assert isinstance(session.statements[-1], Delete)
    assert election._detached_bodies == set()


@pytest.mark.asyncio
async def test_release_skips_delete_while_detached_body_still_draining(monkeypatch: pytest.MonkeyPatch) -> None:
    # If the detached body is still draining after the release drain grace,
    # deleting the lease row would hand leadership to a follower while this
    # process may still act as leader; release() must skip the early release
    # and let the lease expire after its TTL instead.
    # acquire, stolen renewal, then drain-renewal(s); no delete expected. Padded
    # so extra drain renewals from scheduling jitter cannot exhaust rowcounts.
    session = _FakeSession("postgresql", [1, 0] + [1] * 8)
    _install(monkeypatch, session, ttl=3)
    election, release, body_finished = await _detach_shielded_body(monkeypatch, session)
    monkeypatch.setattr(leader_election_module, "_RELEASE_DRAIN_GRACE_SECONDS", 0.2)
    statements_before_release = len(session.statements)

    await election.release()

    assert not body_finished.is_set()
    assert election.is_leader is False
    # The lease row is NOT deleted while the detached body still drains, but the
    # lease IS renewed meanwhile so it cannot expire under the still-running body.
    assert not any(isinstance(statement, Delete) for statement in session.statements)
    assert len(session.statements) > statements_before_release

    release.set()
    [detached] = election._detached_bodies
    with pytest.raises(asyncio.CancelledError):
        await detached
    assert body_finished.is_set()
    assert election._detached_bodies == set()


@pytest.mark.asyncio
async def test_run_if_leader_bounds_heartbeat_sleep_by_remaining_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression: the heartbeat must not sleep a full renew interval when the
    # seeded lease deadline is closer than that. A preserved acquire can leave
    # ``_lease_deadline`` only a fraction of a second out while renew_interval
    # (ttl // 3) is much larger; sleeping the whole interval would keep the
    # gated body running long after the DB row has expired, letting a follower
    # acquire and run the same singleton work concurrently. The heartbeat must
    # wake by the remaining lease to renew (and here, demote), not the interval.
    ttl = 30  # renew_interval = 10s, far larger than the ~0.3s remaining lease

    class _AllErrorSession(_FakeSession):
        # Every DB call fails: try_acquire preserves the still-valid held lease
        # (no DB write, so the stored expiry is unchanged) and every renewal
        # errors, so the only thing that can demote promptly is a bounded sleep.
        def __init__(self) -> None:
            super().__init__("postgresql", [])
            self.execute_calls = 0

        async def execute(self, statement: Any, params: Any = None) -> _FakeResult:
            self.execute_calls += 1
            raise RuntimeError("db down")

    session = _AllErrorSession()
    _install(monkeypatch, session, ttl=ttl)

    election = leader_election_module.LeaderElection(leader_id="node-a")
    loop = asyncio.get_running_loop()
    election._is_leader = True
    election._lease_deadline = loop.time() + 0.3

    body_cancelled = asyncio.Event()

    async def _body() -> str:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            body_cancelled.set()
            raise
        return "ran"

    start = loop.time()
    result = await election.run_if_leader(_body)

    assert result is None
    assert election.is_leader is False
    assert body_cancelled.is_set()
    # Bug: the heartbeat sleeps the full 10s renew_interval before its first
    # renewal, running the body ~10s past the true DB expiry. Fix: the ~0.3s
    # remaining is within one renewal time-box (ttl / 6 = 5s) of the deadline, so
    # the capped sleep is 0 and the renewals fire immediately; the two
    # instantly-failing renewals demote via the consecutive-error cap at ~0s,
    # never sleeping a renew interval.
    assert loop.time() - start < 3.0
    # One preserved acquire attempt + two instantly-failing renewals.
    assert session.execute_calls == 3


@pytest.mark.asyncio
async def test_run_if_leader_demotes_by_deadline_when_renewal_stalls_near_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression (P1): a preserved acquire can leave LESS than one renewal
    # time-box (ttl / 6) on the local deadline. Bounding only the sleep by the
    # remaining lease is not enough: if the renewal DB call / pool checkout then
    # STALLS, the heartbeat would start renew() at the deadline and let its full
    # ttl / 6 time-box run AFTER the DB lease has already expired, so the gated
    # body keeps acting as leader for up to ttl / 6 past expiry while a follower
    # is free to acquire the row. The heartbeat must instead begin the renewal
    # early enough to finish before the deadline AND never wait past the
    # deadline, so a renewal still in flight at the deadline is abandoned and the
    # body is cancelled the instant the deadline is reached.
    ttl = 6  # renew_interval = 2s, renew_timeout = ttl / 6 = 1.0s

    class _StallingRenewSession(_FakeSession):
        # The acquire fails (so try_acquire preserves the already-held lease with
        # no DB write) and the following renewal hangs forever, modelling a
        # stalled DB call / pool checkout on renewal.
        def __init__(self) -> None:
            super().__init__("postgresql", [])
            self.execute_calls = 0

        async def execute(self, statement: Any, params: Any = None) -> _FakeResult:
            self.execute_calls += 1
            if self.execute_calls == 1:
                raise RuntimeError("db down")  # acquire fails -> preserve held lease
            await asyncio.Event().wait()  # renewal stalls indefinitely
            raise AssertionError("unreachable")

    session = _StallingRenewSession()
    _install(monkeypatch, session, ttl=ttl)

    election = leader_election_module.LeaderElection(leader_id="node-a")
    loop = asyncio.get_running_loop()
    # Held lease with only ~0.5s left — less than the 1.0s renewal time-box.
    election._is_leader = True
    election._lease_deadline = loop.time() + 0.5

    body_cancelled = asyncio.Event()

    async def _body() -> str:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            body_cancelled.set()
            raise
        return "ran"

    start = loop.time()
    result = await election.run_if_leader(_body)
    elapsed = loop.time() - start

    assert result is None
    assert election.is_leader is False
    assert body_cancelled.is_set()
    # Fix: the stalled renewal is abandoned AT the ~0.5s deadline and the body is
    # cancelled there. The pre-fix code sleeps to the deadline, then lets the
    # renewal's full 1.0s time-box run past it (~1.5s total) with the body still
    # acting as leader after the DB lease expired.
    assert elapsed < 1.0
    # One preserved acquire attempt + exactly one renewal: the stalled renewal
    # consumed the wait budget up to the deadline and was abandoned there, so no
    # second renewal fires (unlike the instantly-failing case, which retries).
    assert session.execute_calls == 2

    # Drain any still-pending abandoned renewal task so the loop finalizes
    # cleanly (it may already have been discarded once its cancellation unwound).
    if election._abandoned_renewals:
        await asyncio.wait(set(election._abandoned_renewals), timeout=1)


@pytest.mark.asyncio
async def test_run_if_leader_preserves_shared_lease_renewed_by_concurrent_heartbeat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression (P2): run_if_leader is a SHARED singleton across every
    # scheduler, so two gated bodies can heartbeat the SAME LeaderElection
    # concurrently — each with its OWN local lease_deadline but sharing
    # self._is_leader and self._lease_deadline. If heartbeat A renews (advancing
    # the shared self._lease_deadline) while heartbeat B hits the consecutive
    # transient-renewal-error cap, B must NOT clear the shared leader flag /
    # deadline using B's stale local deadline: doing so would cancel A's
    # otherwise-valid leader work AND make A's next renew() return False without
    # touching the database, even though the DB lease is freshly held by the same
    # process. B may stop trusting its own renewals, but shared leadership must
    # survive on the authoritative shared lease state.
    # A large TTL keeps this test's assertions off the wall clock. The behaviour
    # under test is purely ordering (a sibling advances the SHARED
    # ``_lease_deadline`` while THIS heartbeat's renewals fail, so shared
    # leadership must survive) and does not depend on the absolute lease size.
    # With ttl=60 the renewal time-box is ``ttl / 6 = 10s``, so seeding the lease
    # only ``_LEASE_MARGIN`` (5s) ahead still keeps every heartbeat sleep at 0
    # (``max(0, remaining - renew_timeout)`` clamps to 0 whenever
    # ``remaining <= renew_timeout``), i.e. renewals still fire back-to-back
    # promptly — but the heartbeat's ``remaining <= 0`` demotion and the trailing
    # ``_lease_deadline > loop.time()`` assertion now have a full 5s of slack
    # instead of 0.3s. That margin is what makes the test deterministic on loaded
    # CI runners: the original 0.3s window flaked (``run_if_leader`` returned
    # ``None`` -> ``assert None == 'ran'``) whenever an event-loop stall between
    # the acquire and the first heartbeat tick, or between two renewals, exceeded
    # 0.3s. A 5s event-loop stall inside a unit test does not happen without a
    # true hang (which the suite's ``--timeout`` catches).
    ttl = 60  # renew_interval = 20s, renew_timeout = ttl / 6 = 10s
    _LEASE_MARGIN = 5.0
    loop = asyncio.get_running_loop()

    class _SiblingRenewedSession(_FakeSession):
        # The acquire succeeds; every renewal on THIS heartbeat then raises a
        # transient error, but each attempt first advances the SHARED
        # self._lease_deadline into the future to model a concurrent sibling
        # heartbeat that just renewed the row on the same election. Because
        # monotonic time strictly advances between renewals, the freshly stamped
        # deadline is always beyond this heartbeat's currently-adopted local
        # deadline, which is exactly the "sibling kept the shared lease fresh"
        # ordering the preservation branch must honour.
        def __init__(self) -> None:
            super().__init__("postgresql", [1])
            self.election: leader_election_module.LeaderElection | None = None
            self.renew_errors = 0

        async def execute(self, statement: Any, params: Any = None) -> _FakeResult:
            self.statements.append(statement)
            self.params.append(params)
            if self._rowcounts:
                # The one-time acquire.
                return _FakeResult(self._rowcounts.pop(0), self._remaining)
            # A renewal by THIS heartbeat: a concurrent sibling heartbeat keeps
            # the shared lease fresh (advancing it just ahead of this
            # heartbeat's local deadline), then this attempt fails transiently.
            assert self.election is not None
            self.election._lease_deadline = loop.time() + _LEASE_MARGIN
            self.renew_errors += 1
            raise RuntimeError("db down")

    session = _SiblingRenewedSession()
    _install(monkeypatch, session, ttl=ttl)
    # A small acquire remaining (< renew_timeout) so the heartbeat's first
    # renewal still fires promptly (sleep budget 0), but with 5s of slack against
    # event-loop stalls rather than the 0.3s window that flaked on loaded CI.
    session._remaining = _LEASE_MARGIN

    election = leader_election_module.LeaderElection(leader_id="node-a")
    session.election = election

    async def _body() -> str:
        # Run until this heartbeat has failed several renewals while the sibling
        # kept the shared lease fresh, then complete normally.
        while session.renew_errors < 3:
            await asyncio.sleep(0.02)
        return "ran"

    result = await election.run_if_leader(_body)

    # B's body ran to completion (never cancelled by a wrongful demotion) and the
    # shared leadership state survived B's exhausted local renewal errors.
    assert result == "ran"
    assert session.renew_errors >= 3
    assert election.is_leader is True
    assert election._lease_deadline is not None
    assert election._lease_deadline > loop.time()

    # A's next renew() succeeds because _is_leader was never cleared. Swap in a
    # session whose renewal affects the row (rowcount 1) and confirm renew().
    success = _FakeSession("postgresql", [1])
    _install(monkeypatch, success, ttl=ttl)
    assert await election.renew() is True
    assert election.is_leader is True


@pytest.mark.asyncio
async def test_run_if_leader_keeps_renewing_during_shutdown_drain(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression (P1): when run_if_leader is cancelled externally by graceful
    # shutdown (a scheduler's stop()) while the lease is still HELD, the gate
    # must keep renewing the lease while the gated body drains, and stop the
    # heartbeat only after the body exits. The pre-fix finally cancelled the
    # heartbeat FIRST, so a body honouring cancellation slower than the remaining
    # lease TTL (e.g. draining a shielded refresh) could outlive the DB lease and
    # let a follower acquire it and run duplicate singleton work. Assert a
    # renewal fires during the drain window (statements beyond the acquire).
    ttl = 3  # renew_interval = 1s
    session = _FakeSession("postgresql", [1, 1, 1, 1, 1, 1])  # acquire + renewals
    _install(monkeypatch, session, ttl=ttl)

    in_shield = asyncio.Event()
    body_done = asyncio.Event()

    async def _body() -> str:
        inner = asyncio.create_task(asyncio.sleep(1.5))
        in_shield.set()
        try:
            await asyncio.shield(inner)
        except asyncio.CancelledError:
            # Drain the shielded work before propagating, like the auth-guardian
            # and usage-refresh singleflight bodies do.
            await inner
            body_done.set()
            raise
        return "ran"

    election = leader_election_module.LeaderElection(leader_id="node-a")
    run_task: asyncio.Task[Any] = asyncio.ensure_future(election.run_if_leader(_body))

    await in_shield.wait()
    # Let the heartbeat settle into its first sleep before shutdown cancels.
    await asyncio.sleep(0.2)
    statements_at_cancel = len(session.statements)

    # Simulate graceful shutdown cancelling the gate while the lease is still held.
    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task

    # The body fully drained its shielded work (bounded by the cancel grace).
    assert body_done.is_set()
    # A renewal fired during the drain: the heartbeat kept the lease alive rather
    # than being cancelled before the body exited. Pre-fix only the acquire would
    # be present because the heartbeat was cancelled first.
    assert statements_at_cancel == 1
    assert len(session.statements) >= 2


@pytest.mark.asyncio
async def test_release_renews_lease_while_detached_body_drains(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression (P1): on graceful shutdown a gated body can still be draining
    # shielded work when the cancel grace elapses; run_if_leader then DETACHES
    # it and stops the heartbeat. The body is still the rightful leader. With
    # the minimum TTL (5s) the DB lease would expire during release()'s
    # drain-wait if release did not renew it, letting a follower acquire the
    # lease and run the same singleton work concurrently. release() MUST keep
    # renewing the lease on the heartbeat cadence for as long as the detached
    # body may still act as leader, and only delete the row once it has drained.
    ttl = 5  # the minimum allowed TTL; renew_interval = 1s
    # acquire, then drain renewals while release waits, then the delete. Padded
    # so extra renewals from scheduling jitter cannot exhaust the rowcounts.
    session = _FakeSession("postgresql", [1] + [1] * 12)
    _install(monkeypatch, session, ttl=ttl)
    monkeypatch.setattr(leader_election_module, "_CANCEL_GRACE_SECONDS", 0.2)

    in_shield = asyncio.Event()
    release_body = asyncio.Event()
    body_done = asyncio.Event()

    async def _body() -> str:
        inner = asyncio.create_task(release_body.wait())
        in_shield.set()
        try:
            await asyncio.shield(inner)
        except asyncio.CancelledError:
            # Drain the shielded work before propagating, like the auth-guardian
            # and usage-refresh singleflight bodies do. This outlives the cancel
            # grace, so run_if_leader detaches it.
            await inner
            body_done.set()
            raise
        return "ran"

    election = leader_election_module.LeaderElection(leader_id="node-a")
    run_task: asyncio.Task[Any] = asyncio.ensure_future(election.run_if_leader(_body))

    await in_shield.wait()
    # Cancel before the first heartbeat renewal fires so only the acquire has
    # touched the DB when the shutdown-cancel path begins.
    await asyncio.sleep(0.1)
    statements_at_cancel = len(session.statements)
    assert statements_at_cancel == 1

    # Graceful shutdown cancels the gate while the lease is still held. The body
    # is still draining after the 0.2s cancel grace, so it is detached.
    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task
    assert len(election._detached_bodies) == 1
    assert not body_done.is_set()

    # Release runs concurrently with the still-draining detached body.
    release_task: asyncio.Task[None] = asyncio.ensure_future(election.release())

    # Let release wait through several renew intervals (renew_interval = 1s)
    # while the body is still draining. On the pre-fix code release waits without
    # renewing, so no statement beyond the acquire appears and the DB lease would
    # expire under the still-running body; the fix renews on every interval so a
    # follower cannot acquire the lease.
    await asyncio.sleep(2.5)
    renewals_during_drain = len(session.statements) - statements_at_cancel
    assert not body_done.is_set()
    assert renewals_during_drain >= 2

    # The body finishes draining; release then deletes the row it still owns.
    release_body.set()
    await release_task

    assert body_done.is_set()
    assert election.is_leader is False
    assert any(isinstance(statement, Delete) for statement in session.statements)
    assert election._detached_bodies == set()


@pytest.mark.asyncio
async def test_release_keeper_renews_across_cross_scheduler_stop_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression (P1): graceful shutdown stops the singleton schedulers one at a
    # time and only releases the lease AFTER the last one stops. When an earlier
    # scheduler's stop() detaches a shielded gated body, run_if_leader cancels
    # that scheduler's heartbeat on detach — and release() (the only other
    # renewer) has not begun yet because later schedulers are still stopping. If
    # that stop sequence takes >= the minimum 5s TTL, on the pre-fix code nothing
    # renews the lease in the gap (heartbeat gone, release not started), so the DB
    # lease expires while the detached body still runs as leader and a follower
    # can acquire it and run duplicate singleton work. The single process-level
    # keeper, started at shutdown-begin BEFORE any scheduler stops, must renew the
    # lease continuously across that whole window.
    ttl = 5  # the minimum allowed TTL; renew_interval = 1s
    # acquire + keeper renewals across the > TTL gap + drain renewal(s) + delete.
    # Padded so scheduling jitter cannot exhaust the rowcounts.
    session = _FakeSession("postgresql", [1] + [1] * 40)
    _install(monkeypatch, session, ttl=ttl)
    monkeypatch.setattr(leader_election_module, "_CANCEL_GRACE_SECONDS", 0.2)

    in_shield = asyncio.Event()
    release_body = asyncio.Event()
    body_done = asyncio.Event()

    async def _body() -> str:
        inner = asyncio.create_task(release_body.wait())
        in_shield.set()
        try:
            await asyncio.shield(inner)
        except asyncio.CancelledError:
            # Drain the shielded work before propagating, like the auth-guardian
            # and usage-refresh singleflight bodies do. This outlives the cancel
            # grace, so run_if_leader detaches it.
            await inner
            body_done.set()
            raise
        return "ran"

    election = leader_election_module.LeaderElection(leader_id="node-a")
    # Scheduler A runs its leader-gated body.
    run_task: asyncio.Task[Any] = asyncio.ensure_future(election.run_if_leader(_body))
    await in_shield.wait()
    await asyncio.sleep(0.1)

    # Shutdown begins: main.py starts the single process-level keeper BEFORE
    # stopping any scheduler.
    election.start_release_keeper()

    # Scheduler A stops: the shutdown-cancel detaches the still-draining body and
    # stops scheduler A's own heartbeat.
    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task
    assert len(election._detached_bodies) == 1
    assert not body_done.is_set()

    # Scheduler B (and the rest) take longer than the TTL to stop while A's body
    # keeps draining. The keeper must renew the lease throughout this gap.
    statements_before_gap = len(session.statements)
    await asyncio.sleep(ttl + 0.5)  # exceed the TTL to prove the lease never lapses
    renewals_in_gap = len(session.statements) - statements_before_gap
    assert not body_done.is_set()
    # ~1 renewal/sec across a > TTL window; pre-fix this window has zero renewals.
    assert renewals_in_gap >= 3

    # The final release runs: it stops the keeper (handing renewal to its own
    # drain), drains the body — here we let it finish — then deletes the row it
    # still owns.
    release_task: asyncio.Task[None] = asyncio.ensure_future(election.release())
    release_body.set()
    await release_task

    assert body_done.is_set()
    assert election.is_leader is False
    assert any(isinstance(statement, Delete) for statement in session.statements)
    assert election._detached_bodies == set()
    # The keeper is fully stopped after release.
    assert election._release_keeper is None


@pytest.mark.asyncio
async def test_run_if_leader_runs_body_directly_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession("postgresql", [])
    _install(monkeypatch, session, enabled=False)

    election = leader_election_module.LeaderElection(leader_id="node-a")

    async def _body() -> str:
        return "ran"

    assert await election.run_if_leader(_body) == "ran"
    assert session.statements == []

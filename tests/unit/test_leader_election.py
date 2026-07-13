from __future__ import annotations

import asyncio
import importlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.sql.dml import Delete, Insert, Update
from sqlalchemy.sql.elements import TextClause

leader_election_module = importlib.import_module("app.core.scheduling.leader_election")

pytestmark = pytest.mark.unit


class _FakeResult:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _FakeSession:
    def __init__(self, dialect_name: str, rowcounts: list[int]) -> None:
        self._dialect_name = dialect_name
        self._rowcounts = rowcounts
        self.statements: list[Any] = []
        self.params: list[Any] = []
        self.commits = 0

    def get_bind(self) -> SimpleNamespace:
        return SimpleNamespace(dialect=SimpleNamespace(name=self._dialect_name))

    async def execute(self, statement: Any, params: Any = None) -> _FakeResult:
        self.statements.append(statement)
        self.params.append(params)
        return _FakeResult(self._rowcounts.pop(0))

    async def commit(self) -> None:
        self.commits += 1


def _install(
    monkeypatch: pytest.MonkeyPatch,
    session: _FakeSession,
    *,
    enabled: bool = True,
    ttl: int = 30,
) -> None:
    settings = SimpleNamespace(leader_election_enabled=enabled, leader_election_ttl_seconds=ttl)
    monkeypatch.setattr(leader_election_module, "get_settings", lambda: settings)

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
    assert "now()" in sql
    assert "make_interval" in sql
    assert "expires_at < now()" in sql
    [params] = session.params
    assert params["leader_id"] == "node-a"
    assert params["ttl"] == 30


@pytest.mark.asyncio
async def test_try_acquire_binds_host_clock_upsert_on_sqlite_dialect(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession("sqlite", [1])
    _install(monkeypatch, session)

    election = leader_election_module.LeaderElection(leader_id="node-a")

    assert await election.try_acquire() is True
    [statement] = session.statements
    assert isinstance(statement, Insert)
    compiled = str(statement.compile(dialect=sqlite.dialect()))
    assert "INSERT INTO scheduler_leader" in compiled
    assert "ON CONFLICT (id) DO UPDATE" in compiled


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
async def test_renew_demotes_on_rowcount_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession("postgresql", [1, 0])
    _install(monkeypatch, session)

    election = leader_election_module.LeaderElection(leader_id="node-a")
    assert await election.try_acquire() is True

    assert await election.renew() is False
    assert election.is_leader is False


@pytest.mark.asyncio
async def test_renew_extends_on_rowcount_one(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession("sqlite", [1, 1])
    _install(monkeypatch, session)

    election = leader_election_module.LeaderElection(leader_id="node-a")
    assert await election.try_acquire() is True

    assert await election.renew() is True
    assert election.is_leader is True
    assert isinstance(session.statements[1], Update)


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
    session = _FakeSession("postgresql", [1, 0, 1])  # acquire, stolen renewal, release delete
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
    session = _FakeSession("postgresql", [1, 0])  # acquire, stolen renewal; no delete expected
    _install(monkeypatch, session, ttl=3)
    election, release, body_finished = await _detach_shielded_body(monkeypatch, session)
    monkeypatch.setattr(leader_election_module, "_RELEASE_DRAIN_GRACE_SECONDS", 0.2)
    statements_before_release = len(session.statements)

    await election.release()

    assert not body_finished.is_set()
    assert election.is_leader is False
    assert len(session.statements) == statements_before_release
    assert not any(isinstance(statement, Delete) for statement in session.statements)

    release.set()
    [detached] = election._detached_bodies
    with pytest.raises(asyncio.CancelledError):
        await detached
    assert body_finished.is_set()
    assert election._detached_bodies == set()


@pytest.mark.asyncio
async def test_run_if_leader_runs_body_directly_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _FakeSession("postgresql", [])
    _install(monkeypatch, session, enabled=False)

    election = leader_election_module.LeaderElection(leader_id="node-a")

    async def _body() -> str:
        return "ran"

    assert await election.run_if_leader(_body) == "ran"
    assert session.statements == []

"""Unit tests for the model-source pin primitive (#2123 WP-C1, design §3, §8.5, §8.8).

Everything here runs without a database: the repository statements are
checked as text, the lookup and executor paths run against fake sessions on
the virtual scheduler so deadlines, deferred cancellation and the re-read
verification are exercised deterministically.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import settings as hypothesis_settings
from hypothesis import strategies as st

import app.modules.proxy.model_source_pins as pins_module
from app.db.models import DashboardSettings
from app.modules.proxy.affinity import _CodexBackendIdentity
from app.modules.proxy.model_source_pins import (
    PIN_KIND_ANCHOR,
    PIN_KIND_BOUNCE,
    PIN_KIND_THREAD,
    PIN_LOOKUP_DEADLINE_SECONDS,
    PIN_WRITE_ACQUIRE_DEADLINE_SECONDS,
    WS_BOUNCE_TTL_SECONDS,
    PinCache,
    PinIntent,
    PinLookupResult,
    PinLookupTimeout,
    PinRecord,
    PinWrite,
    PinWriteExecutor,
    anchor_pin_key,
    bounce_pin_key,
    build_model_source_pin_prune,
    classify_pin,
    drain_capped_expiry,
    drain_deadline_from_settings,
    lookup_pin_bounded,
    thread_pin_key,
)
from app.modules.settings.subscription_overflow import DRAIN_WINDOW, PIN_IDLE_TTL, PIN_TOMBSTONE_GRACE
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
_DAY = timedelta(days=1)
_LOGGER = "app.modules.proxy.model_source_pins"


def _thread_only_key(thread_id: str) -> str:
    key = _CodexBackendIdentity(process_session=None, thread_id=thread_id).thread_selection_key
    assert key is not None
    return key


def _process_thread_key(process_session: str, thread_id: str) -> str:
    key = _CodexBackendIdentity(process_session=process_session, thread_id=thread_id).thread_selection_key
    assert key is not None
    return key


def _record(
    pin_key: str = "thread\nkey",
    *,
    kind: str = PIN_KIND_THREAD,
    source_id: str = "src_a",
    created_at: datetime = _T0,
    last_seen_at: datetime | None = None,
    expires_at: datetime | None = None,
    purge_at: datetime | None = None,
) -> PinRecord:
    last_seen = created_at if last_seen_at is None else last_seen_at
    expires = last_seen + PIN_IDLE_TTL if expires_at is None else expires_at
    return PinRecord(
        pin_key=pin_key,
        kind=kind,
        source_id=source_id,
        api_key_id=None,
        created_at=created_at,
        last_seen_at=last_seen,
        expires_at=expires,
        purge_at=expires + PIN_TOMBSTONE_GRACE if purge_at is None else purge_at,
    )


# --- keys ------------------------------------------------------------------------


def test_thread_pin_key_is_the_thread_only_form_independent_of_session_and_api_key() -> None:
    key = _thread_only_key("thread-1")
    assert thread_pin_key(key) == "thread\n" + key
    # The same conversation from another Codex process or through another API
    # key builds the identical row: the key carries the thread id only.
    assert thread_pin_key(_thread_only_key("thread-1")) == thread_pin_key(key)
    assert list(inspect.signature(thread_pin_key).parameters) == ["thread_selection_key"]
    assert thread_pin_key(_thread_only_key("thread-2")) != thread_pin_key(key)


@pytest.mark.parametrize("bad_key", [_process_thread_key("session-a", "thread-1"), "thread-1", ""])
def test_thread_pin_key_rejects_anything_but_the_thread_only_selection_key(bad_key: str) -> None:
    with pytest.raises(ValueError, match="thread_only"):
        thread_pin_key(bad_key)
    with pytest.raises(ValueError, match="thread_only"):
        bounce_pin_key(bad_key)


def test_process_thread_keys_for_the_same_thread_would_differ_per_session_so_they_are_rejected() -> None:
    thread_only = _thread_only_key("thread-1")
    assert _process_thread_key("session-a", "thread-1") != _process_thread_key("session-b", "thread-1")
    assert thread_only not in {
        _process_thread_key("session-a", "thread-1"),
        _process_thread_key("session-b", "thread-1"),
    }


def test_anchor_pin_key_is_namespaced_by_api_key() -> None:
    assert anchor_pin_key(None, "resp_1") == "anchor\n-\nresp_1"
    assert anchor_pin_key("key_1", "resp_1") == "anchor\nkey_1\nresp_1"
    assert anchor_pin_key("key_1", "resp_1") != anchor_pin_key("key_2", "resp_1")
    with pytest.raises(ValueError):
        anchor_pin_key("key_1", "")


def test_pin_key_namespaces_are_disjoint() -> None:
    key = _thread_only_key("thread-1")
    keys = {thread_pin_key(key), bounce_pin_key(key), anchor_pin_key(None, key)}
    assert len(keys) == 3
    assert thread_pin_key(key).startswith(PIN_KIND_THREAD + "\n")
    assert bounce_pin_key(key).startswith(PIN_KIND_BOUNCE + "\n")
    assert anchor_pin_key(None, key).startswith(PIN_KIND_ANCHOR + "\n")


# --- drain cap -------------------------------------------------------------------


def test_drain_capped_expiry_without_a_drain_uses_the_idle_ttl() -> None:
    assert drain_capped_expiry(_T0, None) == (_T0 + PIN_IDLE_TTL, _T0 + PIN_IDLE_TTL + PIN_TOMBSTONE_GRACE)


def test_drain_capped_expiry_at_the_clear_instant_matches_the_idle_ttl() -> None:
    drain_until = _T0 + DRAIN_WINDOW
    expires_at, purge_at = drain_capped_expiry(_T0, drain_until)
    assert expires_at == _T0 + PIN_IDLE_TTL
    assert purge_at == drain_until - _DAY


def test_drain_capped_expiry_mid_drain_tombstones_immediately() -> None:
    drain_until = _T0 + 19 * _DAY  # armed ten days ago
    expires_at, purge_at = drain_capped_expiry(_T0, drain_until)
    assert expires_at == _T0 - 3 * _DAY
    assert purge_at == _T0 + 18 * _DAY
    assert purge_at < drain_until


def test_drain_capped_expiry_normalises_naive_utc_inputs() -> None:
    naive_now = _T0.replace(tzinfo=None)
    naive_drain = (_T0 + 10 * _DAY).replace(tzinfo=None)
    assert drain_capped_expiry(naive_now, naive_drain) == drain_capped_expiry(_T0, _T0 + 10 * _DAY)
    expires_at, purge_at = drain_capped_expiry(naive_now, None)
    assert expires_at.tzinfo is timezone.utc and purge_at.tzinfo is timezone.utc


def test_drain_capped_expiry_rejects_negative_ttl() -> None:
    with pytest.raises(ValueError):
        drain_capped_expiry(_T0, None, ttl=-_DAY)


@hypothesis_settings(max_examples=300, deadline=None)
@given(
    now=st.datetimes(
        min_value=datetime(2000, 1, 1),
        max_value=datetime(2100, 1, 1),
        timezones=st.just(timezone.utc),
    ),
    drain_offset=st.one_of(st.none(), st.timedeltas(min_value=-60 * _DAY, max_value=60 * _DAY)),
    ttl=st.timedeltas(min_value=timedelta(0), max_value=30 * _DAY),
)
def test_drain_cap_property_no_write_while_draining_outlives_the_deadline(
    now: datetime, drain_offset: timedelta | None, ttl: timedelta
) -> None:
    drain_until = None if drain_offset is None else now + drain_offset
    expires_at, purge_at = drain_capped_expiry(now, drain_until, ttl=ttl)
    assert purge_at == expires_at + PIN_TOMBSTONE_GRACE
    assert expires_at <= now + ttl
    if drain_until is None:
        assert expires_at == now + ttl
    else:
        assert purge_at < drain_until


def test_daily_touch_across_the_drain_never_slides_past_the_cap() -> None:
    """CL-3 arithmetic: a pin touched every day of a drain expires at the cap and purges before the deadline."""

    drain_until = _T0 + DRAIN_WINDOW
    cap = _T0 + PIN_IDLE_TTL
    expires_at, purge_at = drain_capped_expiry(_T0 - 3 * _DAY, None)  # written before the clear
    for day in range(0, 45):
        now = _T0 + day * _DAY
        state = classify_pin(_record(expires_at=expires_at, purge_at=purge_at), now).state
        if state == "live":
            expires_at, purge_at = drain_capped_expiry(now, drain_until)
        assert expires_at <= cap
        assert purge_at < drain_until
        if day < 7:
            assert state == "live"
        elif day < 28:
            assert state == "expired"
        else:
            assert state == "none"
    assert classify_pin(_record(expires_at=expires_at, purge_at=purge_at), drain_until).state == "none"


def test_drain_deadline_from_settings_normalises_the_naive_settings_column() -> None:
    naive = (_T0 + 29 * _DAY).replace(tzinfo=None)
    assert drain_deadline_from_settings(DashboardSettings(subscription_overflow_drain_until=naive)) == _T0 + 29 * _DAY
    assert drain_deadline_from_settings(DashboardSettings(subscription_overflow_drain_until=None)) is None


# --- lookup states ---------------------------------------------------------------


def test_classify_pin_states() -> None:
    record = _record()
    assert classify_pin(None, _T0) == PinLookupResult("none", None)
    assert classify_pin(record, _T0) == PinLookupResult("live", record)
    assert classify_pin(record, record.expires_at - timedelta(microseconds=1)).state == "live"
    assert classify_pin(record, record.expires_at).state == "expired"
    assert classify_pin(record, record.purge_at - timedelta(microseconds=1)).state == "expired"
    assert classify_pin(record, record.purge_at) == PinLookupResult("none", None)
    bounce = _record(
        "bounce\nkey",
        kind=PIN_KIND_BOUNCE,
        expires_at=_T0 + timedelta(seconds=60),
        purge_at=_T0 + timedelta(seconds=60),
    )
    assert classify_pin(bounce, _T0) == PinLookupResult("bounce", bounce)
    assert classify_pin(bounce, _T0 + timedelta(seconds=60)).state == "none"
    # Naive ``now`` is taken as UTC.
    assert classify_pin(record, _T0.replace(tzinfo=None)).state == "live"


# --- cache -----------------------------------------------------------------------


def test_pin_cache_is_positive_only_ttl_bounded_and_lru() -> None:
    cache = PinCache(ttl_seconds=60.0, max_entries=2)
    first, second, third = _record("thread\na"), _record("thread\nb"), _record("thread\nc")
    assert cache.get("thread\na", now=0.0) is None
    cache.put(first, now=0.0)
    cache.put(second, now=1.0)
    assert cache.get("thread\na", now=30.0) is first  # touches ``a``: ``b`` is now least recent
    cache.put(third, now=2.0)
    assert len(cache) == 2
    assert cache.get("thread\nb", now=2.0) is None
    assert cache.get("thread\nc", now=2.0) is third
    assert cache.get("thread\na", now=59.999) is first
    assert cache.get("thread\na", now=60.0) is None  # TTL counted from the put
    cache.invalidate("thread\nc")
    assert cache.get("thread\nc", now=2.0) is None
    assert len(cache) == 0


def test_pin_cache_rejects_degenerate_bounds() -> None:
    with pytest.raises(ValueError):
        PinCache(ttl_seconds=0)
    with pytest.raises(ValueError):
        PinCache(max_entries=0)


# --- bounded lookup (virtual time) ----------------------------------------------


@pytest.fixture
def virtual() -> tuple[VirtualClock, VirtualScheduler]:
    clock = VirtualClock(epoch_value=_T0.timestamp())
    return clock, VirtualScheduler(clock)


@pytest.mark.asyncio
async def test_lookup_pin_bounded_times_out_after_the_lookup_deadline(monkeypatch, virtual) -> None:
    clock, scheduler = virtual

    async def hang(pin_key: str, session_factory: object) -> PinRecord | None:
        await asyncio.Event().wait()
        return None

    monkeypatch.setattr(pins_module, "_read_pin", hang)
    task = scheduler.create_task(lookup_pin_bounded("thread\nkey", cache=None, scheduler=scheduler, clock=clock))
    await scheduler.advance(PIN_LOOKUP_DEADLINE_SECONDS - 0.001)
    assert not task.done()
    await scheduler.advance(0.002)
    assert task.done()
    with pytest.raises(PinLookupTimeout):
        task.result()
    await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_lookup_pin_bounded_serves_a_live_cache_hit_without_reading(monkeypatch, virtual) -> None:
    clock, scheduler = virtual
    reads: list[str] = []

    async def read(pin_key: str, session_factory: object) -> PinRecord | None:
        reads.append(pin_key)
        return None

    monkeypatch.setattr(pins_module, "_read_pin", read)
    cache = PinCache()
    live = _record(created_at=clock.now())
    cache.put(live, now=clock.monotonic())
    result = await lookup_pin_bounded(live.pin_key, cache=cache, scheduler=scheduler, clock=clock)
    assert result == PinLookupResult("live", live)
    assert reads == []


@pytest.mark.asyncio
async def test_lookup_pin_bounded_never_caches_absence(monkeypatch, virtual) -> None:
    clock, scheduler = virtual
    answers: list[PinRecord | None] = [None, _record(created_at=clock.now())]
    reads: list[str] = []

    async def read(pin_key: str, session_factory: object) -> PinRecord | None:
        reads.append(pin_key)
        return answers.pop(0)

    monkeypatch.setattr(pins_module, "_read_pin", read)
    cache = PinCache()
    first = await lookup_pin_bounded("thread\nkey", cache=cache, scheduler=scheduler, clock=clock)
    assert first == PinLookupResult("none", None)
    assert len(cache) == 0
    # Another replica pinned the thread in between: the next lookup must see it.
    second = await lookup_pin_bounded("thread\nkey", cache=cache, scheduler=scheduler, clock=clock)
    assert second.state == "live"
    assert reads == ["thread\nkey", "thread\nkey"]
    assert cache.get("thread\nkey", now=clock.monotonic()) == second.record


@pytest.mark.asyncio
async def test_lookup_pin_bounded_drops_cached_records_that_are_no_longer_live(monkeypatch, virtual) -> None:
    clock, scheduler = virtual
    reads: list[str] = []
    tombstone = _record(created_at=clock.now() - 8 * _DAY)

    async def read(pin_key: str, session_factory: object) -> PinRecord | None:
        reads.append(pin_key)
        return tombstone

    monkeypatch.setattr(pins_module, "_read_pin", read)
    cache = PinCache()
    cache.put(tombstone, now=clock.monotonic())  # expired per the clock: must not be served
    result = await lookup_pin_bounded(tombstone.pin_key, cache=cache, scheduler=scheduler, clock=clock)
    assert result.state == "expired"
    assert reads == [tombstone.pin_key]
    assert len(cache) == 0  # a tombstone is never cached


@pytest.mark.asyncio
async def test_lookup_pin_bounded_rereads_after_the_cache_ttl(monkeypatch, virtual) -> None:
    clock, scheduler = virtual
    reads: list[str] = []
    live = _record(created_at=clock.now())

    async def read(pin_key: str, session_factory: object) -> PinRecord | None:
        reads.append(pin_key)
        return live

    monkeypatch.setattr(pins_module, "_read_pin", read)
    cache = PinCache()
    await lookup_pin_bounded(live.pin_key, cache=cache, scheduler=scheduler, clock=clock)
    await scheduler.advance(30)
    await lookup_pin_bounded(live.pin_key, cache=cache, scheduler=scheduler, clock=clock)
    assert reads == [live.pin_key]
    await scheduler.advance(31)
    await lookup_pin_bounded(live.pin_key, cache=cache, scheduler=scheduler, clock=clock)
    assert reads == [live.pin_key, live.pin_key]


# --- executor (fake sessions, virtual time) -------------------------------------


class _FakeResult:
    def __init__(self, rows: list[tuple[object, ...]] | None = None, rowcount: int = 1) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    def all(self) -> list[tuple[object, ...]]:
        return list(self._rows)

    def first(self) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self) -> object | None:
        return self._rows[0][0] if self._rows else None


class _FakeSession:
    def __init__(
        self,
        *,
        gate: asyncio.Event | None = None,
        fail_statement: Exception | None = None,
        rows: list[tuple[object, ...]] | None = None,
    ) -> None:
        self.gate = gate
        self.fail_statement = fail_statement
        self.rows = rows or []
        self.statements: list[str] = []
        self.committed = False
        self.closed = False

    async def connection(self) -> None:
        return None

    async def execute(self, statement: object, params: object = None) -> _FakeResult:
        self.statements.append(" ".join(str(statement).split()))
        if self.gate is not None:
            await self.gate.wait()
        if self.fail_statement is not None:
            raise self.fail_statement
        return _FakeResult(self.rows)

    async def commit(self) -> None:
        self.committed = True


def _session_factory(sessions: list[_FakeSession]):
    @asynccontextmanager
    async def factory():
        session = sessions.pop(0)
        try:
            yield session
        finally:
            session.closed = True

    return factory


@asynccontextmanager
async def _open_writer_section():
    yield


def _blocked_writer_section():
    @asynccontextmanager
    async def section():
        await asyncio.Event().wait()
        yield

    return section


def _executor(*sessions: _FakeSession) -> PinWriteExecutor:
    return PinWriteExecutor(session_factory=_session_factory(list(sessions)), writer_section=_open_writer_section)


def _intent(*keys: str, source_id: str = "src_a") -> PinIntent:
    keys = keys or ("thread\nkey",)
    return PinIntent(
        writes=tuple(PinWrite(key, key.split("\n", 1)[0], source_id, None) for key in keys),
        thread_key=keys[0],
    )


def _row(pin_key: str, *, last_seen_at: datetime, source_id: str = "src_a") -> tuple[object, ...]:
    return (
        pin_key,
        pin_key.split("\n", 1)[0],
        source_id,
        None,
        last_seen_at,
        last_seen_at,
        last_seen_at + PIN_IDLE_TTL,
        last_seen_at + PIN_IDLE_TTL + PIN_TOMBSTONE_GRACE,
    )


@pytest.mark.asyncio
async def test_executor_acquisition_timeout_is_not_written_with_no_statement_issued(virtual, caplog) -> None:
    clock, scheduler = virtual
    sessions = [_FakeSession()]
    executor = PinWriteExecutor(session_factory=_session_factory(sessions), writer_section=_blocked_writer_section())
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    task = scheduler.create_task(executor.commit(_intent(), drain_until=None, scheduler=scheduler, clock=clock))
    await scheduler.advance(PIN_WRITE_ACQUIRE_DEADLINE_SECONDS - 0.01)
    assert not task.done()
    await scheduler.advance(0.02)
    assert task.result() == "not_written"
    assert len(sessions) == 1 and sessions[0].statements == []  # the session was never even taken
    assert "model_source_pin_write outcome=not_written" in caplog.text
    assert "reason=acquire_timeout" in caplog.text


@pytest.mark.asyncio
async def test_executor_acquisition_failure_is_not_written_with_no_statement_issued(virtual, caplog) -> None:
    clock, scheduler = virtual

    class _BrokenCheckout(_FakeSession):
        async def connection(self) -> None:
            raise RuntimeError("pool exhausted")

    session = _BrokenCheckout()
    executor = _executor(session)
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    outcome = await executor.commit(_intent(), drain_until=None, scheduler=scheduler, clock=clock)
    assert outcome == "not_written"
    assert session.statements == [] and session.closed is True
    assert "model_source_pin_write outcome=not_written" in caplog.text
    assert "reason=acquire_failed" in caplog.text


@pytest.mark.asyncio
async def test_executor_caller_cancellation_during_acquisition_issues_nothing(virtual) -> None:
    clock, scheduler = virtual
    sessions = [_FakeSession()]
    executor = PinWriteExecutor(session_factory=_session_factory(sessions), writer_section=_blocked_writer_section())
    task = scheduler.create_task(executor.commit(_intent(), drain_until=None, scheduler=scheduler, clock=clock))
    await scheduler.drain()
    task.cancel()
    await scheduler.drain()
    assert task.cancelled()
    assert len(sessions) == 1 and sessions[0].statements == []


@pytest.mark.asyncio
async def test_executor_defers_caller_cancellation_until_the_statement_completes(virtual, caplog) -> None:
    clock, scheduler = virtual
    gate = asyncio.Event()
    session = _FakeSession(gate=gate)
    executor = _executor(session)
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    task = scheduler.create_task(executor.commit(_intent(), drain_until=None, scheduler=scheduler, clock=clock))
    await scheduler.drain()
    assert len(session.statements) == 1 and "INSERT INTO model_source_pins" in session.statements[0]
    task.cancel()
    await scheduler.drain()
    assert not task.done()  # the issued statement runs to completion first
    gate.set()
    await scheduler.drain()
    assert task.done() and task.cancelled()  # the deferred cancellation is honoured afterwards
    assert session.committed is True
    assert session.closed is True
    assert "model_source_pin_write outcome=written" in caplog.text


@pytest.mark.asyncio
async def test_executor_commit_is_written_when_the_statement_and_commit_succeed(virtual) -> None:
    clock, scheduler = virtual
    session = _FakeSession()
    executor = _executor(session)
    outcome = await executor.commit(
        _intent("thread\nkey", "anchor\n-\nresp"), drain_until=None, scheduler=scheduler, clock=clock
    )
    assert outcome == "written"
    assert session.committed and session.closed
    assert len(session.statements) == 1  # one executemany upsert for both rows


@pytest.mark.asyncio
async def test_executor_empty_intent_is_vacuously_written(virtual) -> None:
    clock, scheduler = virtual
    sessions: list[_FakeSession] = []
    executor = PinWriteExecutor(session_factory=_session_factory(sessions), writer_section=_open_writer_section)
    assert await executor.commit(PinIntent((), None), drain_until=None, scheduler=scheduler, clock=clock) == "written"


@pytest.mark.asyncio
async def test_executor_refuses_an_intent_that_still_owes_its_anchor(virtual, caplog) -> None:
    """Decision 78: an unresolved anchored intent is ``not_written`` with nothing issued -- not even the thread pin."""

    clock, scheduler = virtual
    session = _FakeSession()
    executor = _executor(session)
    unresolved = PinIntent(
        writes=(PinWrite("thread\nkey", PIN_KIND_THREAD, "src_a", "key-1"),),
        thread_key="key",
        source_id="src_a",
        anchor_api_key_id="key-1",
        anchor=True,
    )
    caplog.set_level(logging.DEBUG, logger=_LOGGER)

    outcome = await executor.commit(unresolved, drain_until=None, scheduler=scheduler, clock=clock)

    assert outcome == "not_written"
    assert session.statements == [] and session.committed is False
    assert "model_source_pin_write outcome=not_written" in caplog.text
    assert "reason=anchor_unresolved" in caplog.text
    assert f"kinds={PIN_KIND_ANCHOR}" in caplog.text


@pytest.mark.asyncio
async def test_executor_refuses_a_keyless_owed_anchor_instead_of_reporting_it_vacuously_written(
    virtual, caplog
) -> None:
    """The mainstream SDK shape (no thread key): without the refusal the turn would land zero durable rows."""

    clock, scheduler = virtual
    session = _FakeSession()
    executor = _executor(session)
    keyless = PinIntent(writes=(), thread_key=None, source_id="src_a", anchor=True)
    caplog.set_level(logging.DEBUG, logger=_LOGGER)

    outcome = await executor.commit(keyless, drain_until=None, scheduler=scheduler, clock=clock)

    assert outcome == "not_written", "the empty-write-set short-circuit must not swallow an owed anchor"
    assert session.statements == []
    assert "reason=anchor_unresolved" in caplog.text


@pytest.mark.asyncio
async def test_executor_commits_the_thread_pin_alone_when_no_anchor_is_owed(virtual) -> None:
    """Mutant guard: rejecting *every* intent with an unappended anchor row would break native ``store: false``."""

    clock, scheduler = virtual
    session = _FakeSession()
    executor = _executor(session)
    unanchored = PinIntent(
        writes=(PinWrite("thread\nkey", PIN_KIND_THREAD, "src_a", None),), thread_key="key", source_id="src_a"
    )
    assert unanchored.anchor_pending is False

    outcome = await executor.commit(unanchored, drain_until=None, scheduler=scheduler, clock=clock)

    assert outcome == "written"
    assert len(session.statements) == 1 and session.committed is True


@pytest.mark.asyncio
async def test_executor_commits_a_resolved_anchored_intent(virtual) -> None:
    clock, scheduler = virtual
    session = _FakeSession()
    executor = _executor(session)
    resolved = PinIntent(
        writes=(PinWrite("thread\nkey", PIN_KIND_THREAD, "src_a", "key-1"),),
        thread_key="key",
        source_id="src_a",
        anchor_api_key_id="key-1",
        anchor=True,
    ).resolve("resp_source_1")

    outcome = await executor.commit(resolved, drain_until=None, scheduler=scheduler, clock=clock)

    assert outcome == "written"
    assert len(session.statements) == 1  # one executemany upsert for the thread pin and the anchor


@pytest.mark.asyncio
async def test_executor_resolves_a_failed_statement_by_reread_as_written(virtual, caplog) -> None:
    clock, scheduler = virtual
    failing = _FakeSession(fail_statement=RuntimeError("commit lost"))
    verify = _FakeSession(rows=[_row("thread\nkey", last_seen_at=clock.now())])
    executor = _executor(failing, verify)
    caplog.set_level(logging.DEBUG, logger=_LOGGER)
    outcome = await executor.commit(_intent(), drain_until=None, scheduler=scheduler, clock=clock)
    assert outcome == "written"
    assert failing.committed is False and failing.closed is True
    assert verify.statements and "SELECT" in verify.statements[0]
    assert "statement failed" in caplog.text
    assert "model_source_pin_write outcome=written" in caplog.text


@pytest.mark.asyncio
async def test_executor_verification_normalises_a_naive_clock_like_every_other_now_boundary(virtual, caplog) -> None:
    """``_record_from_row`` yields aware UTC timestamps; a ``Clock`` seam that returns naive UTC (the settings row's
    convention, accepted by ``upsert``/``touch``/``classify_pin``) must verify instead of raising ``TypeError``."""

    clock, scheduler = virtual

    class _NaiveClock:
        def now(self) -> datetime:
            return clock.now().replace(tzinfo=None)

        def monotonic(self) -> float:
            return clock.monotonic()

        def time(self) -> float:
            return clock.time()

    failing = _FakeSession(fail_statement=RuntimeError("commit lost"))
    verify = _FakeSession(rows=[_row("thread\nkey", last_seen_at=clock.now())])
    executor = _executor(failing, verify)
    caplog.set_level(logging.WARNING, logger=_LOGGER)

    outcome = await executor.commit(_intent(), drain_until=None, scheduler=scheduler, clock=_NaiveClock())

    assert outcome == "written"
    assert verify.statements and "SELECT" in verify.statements[0]
    assert "TypeError" not in caplog.text


@pytest.mark.asyncio
async def test_executor_resolves_a_failed_statement_by_reread_as_not_written(virtual, caplog) -> None:
    clock, scheduler = virtual
    stale = _row("thread\nkey", last_seen_at=clock.now() - _DAY)  # an older write, not ours
    for rows in ([], [stale]):
        failing = _FakeSession(fail_statement=RuntimeError("boom"))
        verify = _FakeSession(rows=rows)
        executor = _executor(failing, verify)
        caplog.set_level(logging.WARNING, logger=_LOGGER)
        outcome = await executor.commit(_intent(), drain_until=None, scheduler=scheduler, clock=clock)
        assert outcome == "not_written"
    assert "model_source_pin_write outcome=not_written" in caplog.text
    assert "reason=statement_failed" in caplog.text


@pytest.mark.asyncio
async def test_executor_partial_reread_is_unknown(virtual) -> None:
    clock, scheduler = virtual
    failing = _FakeSession(fail_statement=RuntimeError("boom"))
    verify = _FakeSession(rows=[_row("thread\nkey", last_seen_at=clock.now())])  # anchor missing
    executor = _executor(failing, verify)
    outcome = await executor.commit(
        _intent("thread\nkey", "anchor\n-\nresp"), drain_until=None, scheduler=scheduler, clock=clock
    )
    assert outcome == "unknown"


@pytest.mark.asyncio
async def test_executor_reread_failure_is_unknown(virtual, caplog) -> None:
    clock, scheduler = virtual
    failing = _FakeSession(fail_statement=RuntimeError("boom"))
    verify = _FakeSession(fail_statement=RuntimeError("read boom"))
    executor = _executor(failing, verify)
    caplog.set_level(logging.WARNING, logger=_LOGGER)
    outcome = await executor.commit(_intent(), drain_until=None, scheduler=scheduler, clock=clock)
    assert outcome == "unknown"
    assert "model_source_pin_write outcome=unknown" in caplog.text
    assert "reason=reread_failed" in caplog.text


@pytest.mark.asyncio
async def test_executor_reread_timeout_is_unknown(virtual) -> None:
    clock, scheduler = virtual
    failing = _FakeSession(fail_statement=RuntimeError("boom"))
    verify = _FakeSession(gate=asyncio.Event())  # never answers
    executor = _executor(failing, verify)
    task = scheduler.create_task(executor.commit(_intent(), drain_until=None, scheduler=scheduler, clock=clock))
    await scheduler.advance(PIN_LOOKUP_DEADLINE_SECONDS - 0.001)
    assert not task.done()
    await scheduler.advance(0.002)
    assert task.result() == "unknown"


@pytest.mark.asyncio
async def test_executor_deadline_is_never_an_outcome_once_the_statement_is_issued(virtual) -> None:
    """A slow statement is waited for, not timed out: the acquisition deadline does not apply after issuance."""

    clock, scheduler = virtual
    gate = asyncio.Event()
    session = _FakeSession(gate=gate)
    executor = _executor(session)
    task = scheduler.create_task(executor.commit(_intent(), drain_until=None, scheduler=scheduler, clock=clock))
    await scheduler.advance(PIN_WRITE_ACQUIRE_DEADLINE_SECONDS * 5)
    assert not task.done()
    gate.set()
    await scheduler.drain()
    assert task.result() == "written"


@pytest.mark.asyncio
async def test_delete_durably_invalidates_the_cache_and_verifies(virtual) -> None:
    clock, scheduler = virtual
    cache = PinCache()
    record = _record(created_at=clock.now())
    cache.put(record, now=clock.monotonic())
    session = _FakeSession()
    executor = _executor(session)
    outcome = await executor.delete_durably(record.pin_key, scheduler=scheduler, cache=cache)
    assert outcome == "written"
    assert "DELETE FROM model_source_pins" in session.statements[0]
    assert cache.get(record.pin_key, now=clock.monotonic()) is None

    failing = _FakeSession(fail_statement=RuntimeError("boom"))
    still_there = _FakeSession(rows=[_row(record.pin_key, last_seen_at=clock.now())])
    executor = _executor(failing, still_there)
    assert await executor.delete_durably(record.pin_key, scheduler=scheduler) == "not_written"

    failing = _FakeSession(fail_statement=RuntimeError("boom"))
    gone = _FakeSession(rows=[])
    executor = _executor(failing, gone)
    assert await executor.delete_durably(record.pin_key, scheduler=scheduler) == "written"


# --- statements ------------------------------------------------------------------


def test_prune_statements_use_the_database_clock_in_keyed_batches() -> None:
    postgres = " ".join(str(build_model_source_pin_prune(dialect_name="postgresql")).split())
    assert "purge_at <= statement_timestamp()" in postgres
    assert "pin_key IN ( SELECT pin_key FROM model_source_pins" in postgres
    assert "LIMIT :batch_size" in postgres
    sqlite = str(build_model_source_pin_prune(dialect_name="sqlite"))
    assert "(strftime('%Y-%m-%d %H:%M:%f', 'now') || '000')" in sqlite
    assert "CURRENT_TIMESTAMP" not in sqlite
    with pytest.raises(RuntimeError, match="Unsupported database dialect"):
        build_model_source_pin_prune(dialect_name="mysql")


def test_upsert_statement_keeps_created_at_and_slides_the_rest() -> None:
    upsert = " ".join(str(pins_module._UPSERT).split())
    conflict_clause = upsert.split("ON CONFLICT (pin_key) DO UPDATE SET", 1)[1]
    assert "created_at" not in conflict_clause
    assert "kind" not in conflict_clause
    for column in ("source_id", "api_key_id", "last_seen_at", "expires_at", "purge_at"):
        assert f"{column} = excluded.{column}" in conflict_clause


def test_touch_statement_slides_live_thread_and_anchor_rows_only() -> None:
    touch = " ".join(str(pins_module._TOUCH).split())
    assert "WHERE pin_key = :pin_key AND expires_at > :now AND kind <> 'bounce'" in touch


def test_bounce_ttl_matches_the_design_constant() -> None:
    assert WS_BOUNCE_TTL_SECONDS == 60.0
    expires_at, purge_at = pins_module._bounce_expiry(_T0, None)
    assert expires_at == purge_at == _T0 + timedelta(seconds=60)
    capped_expires, capped_purge = pins_module._bounce_expiry(_T0, _T0 + 10 * _DAY)
    assert capped_expires == capped_purge < _T0 + 10 * _DAY


# --- PinIntent.resolve, the process cache and the multi-key lookup (#2123 WP-C2, C1 gaps) -----


def test_pin_intent_resolve_appends_the_anchor_only_when_anchored_and_minted() -> None:
    thread_write = PinWrite("thread\nkey", PIN_KIND_THREAD, "src_a", "key-1")
    anchored = PinIntent(
        writes=(thread_write,), thread_key="key", source_id="src_a", anchor_api_key_id="key-1", anchor=True
    )

    resolved = anchored.resolve("resp_source_1")
    assert resolved is not anchored
    assert resolved.writes == (
        thread_write,
        PinWrite("anchor\nkey-1\nresp_source_1", PIN_KIND_ANCHOR, "src_a", "key-1"),
    )
    assert resolved.thread_key == "key" and resolved.anchor is True
    assert resolved.anchor_resolved is True and resolved.anchor_pending is False
    # No response id (a source that never sent ``response.created``): the intent is unchanged, and it still
    # *owes* its anchor -- ``PinWriteExecutor.commit`` refuses it rather than writing the thread pin alone.
    assert anchored.resolve(None) is anchored
    assert anchored.resolve("") is anchored
    assert anchored.anchor_pending is True
    assert anchored.resolve(None).anchor_pending is True
    assert anchored.resolve("resp_source_1").anchor_pending is False
    # Idempotent: a second ``resolve`` never appends a duplicate anchor write.
    assert resolved.resolve("resp_other") is resolved
    # An intent that owes no anchor is never pending, however empty its write set.
    assert PinIntent(writes=(), thread_key="k").anchor_pending is False
    # Keyless client: the anchor namespace uses ``-``.
    keyless = PinIntent(writes=(), thread_key=None, source_id="src_a", anchor=True).resolve("resp_2")
    assert keyless.writes == (PinWrite("anchor\n-\nresp_2", PIN_KIND_ANCHOR, "src_a", None),)
    assert keyless.anchor_pending is False


def test_pin_intent_store_false_never_anchors() -> None:
    """Mutant: anchor row for ``store: false`` (``anchor=False``)."""

    intent = PinIntent(writes=(), thread_key="key", source_id="src_a", anchor_api_key_id="key-1", anchor=False)
    assert intent.resolve("resp_source_1") is intent
    assert intent.resolve("resp_source_1").writes == ()


def test_pin_intent_anchor_requires_the_source_id() -> None:
    with pytest.raises(ValueError):
        PinIntent(writes=(), thread_key=None, anchor=True)
    # The pre-C2 shape (writes + thread key) still constructs.
    assert PinIntent(writes=(), thread_key=None).anchor is False
    # ``anchor_resolved`` is only meaningful for an anchored intent.
    with pytest.raises(ValueError):
        PinIntent(writes=(), thread_key=None, source_id="src_a", anchor=False, anchor_resolved=True)


def test_get_pin_cache_is_a_process_singleton(monkeypatch) -> None:
    monkeypatch.setattr(pins_module, "_PIN_CACHE", None)
    first = pins_module.get_pin_cache()
    assert pins_module.get_pin_cache() is first
    assert isinstance(first, PinCache)
    assert len(first) == 0


@pytest.mark.asyncio
async def test_lookup_pins_bounded_reads_the_missing_keys_in_one_bounded_read(monkeypatch, virtual) -> None:
    clock, scheduler = virtual
    reads: list[tuple[str, ...]] = []
    live = _record(created_at=clock.now())
    bounce_key = bounce_pin_key(_thread_only_key("thread-2"))
    bounce = PinRecord(
        pin_key=bounce_key,
        kind=PIN_KIND_BOUNCE,
        source_id="src_a",
        api_key_id=None,
        created_at=clock.now(),
        last_seen_at=clock.now(),
        expires_at=clock.now() + timedelta(seconds=WS_BOUNCE_TTL_SECONDS),
        purge_at=clock.now() + timedelta(seconds=WS_BOUNCE_TTL_SECONDS),
    )

    async def read(pin_keys, session_factory: object) -> dict[str, PinRecord]:
        reads.append(tuple(pin_keys))
        return {bounce.pin_key: bounce}

    monkeypatch.setattr(pins_module, "_read_pins", read)
    cache = PinCache()
    cache.put(live, now=clock.monotonic())
    results = await pins_module.lookup_pins_bounded(
        [live.pin_key, bounce_key, "thread\nabsent", live.pin_key], cache=cache, scheduler=scheduler, clock=clock
    )
    assert reads == [(bounce_key, "thread\nabsent")], (
        "the cached live key is served without a read; keys are deduplicated"
    )
    assert results[live.pin_key] == PinLookupResult("live", live)
    assert results[bounce_key] == PinLookupResult("bounce", bounce)
    assert results["thread\nabsent"] == PinLookupResult("none", None)
    assert cache.get("thread\nabsent", now=clock.monotonic()) is None, "absence is never cached"
    assert cache.get(bounce_key, now=clock.monotonic()) is None, "only live records are cached"


@pytest.mark.asyncio
async def test_lookup_pins_bounded_times_out_after_the_lookup_deadline(monkeypatch, virtual) -> None:
    clock, scheduler = virtual

    async def hang(pin_keys, session_factory: object) -> dict[str, PinRecord]:
        await asyncio.Event().wait()
        return {}

    monkeypatch.setattr(pins_module, "_read_pins", hang)
    task = scheduler.create_task(
        pins_module.lookup_pins_bounded(["thread\nkey", "bounce\nkey"], cache=None, scheduler=scheduler, clock=clock)
    )
    await scheduler.advance(PIN_LOOKUP_DEADLINE_SECONDS + 0.01)
    assert task.done()
    with pytest.raises(PinLookupTimeout):
        task.result()
    await scheduler.cancel_owned_tasks()


@pytest.mark.asyncio
async def test_lookup_pins_bounded_with_every_key_cached_never_reads(monkeypatch, virtual) -> None:
    clock, scheduler = virtual
    live = _record(created_at=clock.now())

    async def read(pin_keys, session_factory: object) -> dict[str, PinRecord]:
        raise AssertionError("no read expected")

    monkeypatch.setattr(pins_module, "_read_pins", read)
    cache = PinCache()
    cache.put(live, now=clock.monotonic())
    results = await pins_module.lookup_pins_bounded([live.pin_key], cache=cache, scheduler=scheduler, clock=clock)
    assert results == {live.pin_key: PinLookupResult("live", live)}

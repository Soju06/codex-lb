"""Fast-decline set, overflow pin executor and pin toucher (#2123 WP-C2, design v3 §8.5, §8.10, §6.3).

Fake sessions on the virtual scheduler, as in ``test_model_source_pins.py``:
the executor's own verification discipline is pinned there; here only the
overflow layer on top of it is under test (mutant: no fast-decline mark).
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.modules.proxy import overflow as overflow_module
from app.modules.proxy.model_source_pins import (
    PIN_TOUCH_INTERVAL_SECONDS,
    PIN_WRITE_ACQUIRE_DEADLINE_SECONDS,
    PinIntent,
    PinRecord,
    PinWrite,
    PinWriteExecutor,
)
from app.modules.proxy.overflow import (
    FAST_DECLINE_MAX_KEYS,
    PIN_FAILURE_FAST_DECLINE_SECONDS,
    FastDeclineSet,
    OverflowPinExecutor,
    PinToucher,
    get_fast_decline_set,
    get_overflow_pin_executor,
    get_pin_toucher,
)
from app.modules.settings.subscription_overflow import PIN_IDLE_TTL, PIN_TOMBSTONE_GRACE
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
THREAD_KEY = "codex:thread_header:thread_only:abc"


# --- FastDeclineSet -------------------------------------------------------------------------


def test_fast_decline_marks_for_sixty_seconds_then_expires() -> None:
    declines = FastDeclineSet()
    declines.mark(THREAD_KEY, 100.0)
    assert declines.contains(THREAD_KEY, 100.0)
    assert declines.contains(THREAD_KEY, 100.0 + PIN_FAILURE_FAST_DECLINE_SECONDS - 0.001)
    assert not declines.contains(THREAD_KEY, 100.0 + PIN_FAILURE_FAST_DECLINE_SECONDS)
    assert len(declines) == 0  # lazily evicted on the expired read
    assert not declines.contains("other", 100.0)


def test_fast_decline_remark_extends_the_deadline() -> None:
    declines = FastDeclineSet(ttl_seconds=10.0)
    declines.mark(THREAD_KEY, 0.0)
    declines.mark(THREAD_KEY, 5.0)
    assert declines.contains(THREAD_KEY, 14.9)
    assert not declines.contains(THREAD_KEY, 15.0)


def test_fast_decline_is_bounded_evicting_expired_then_earliest_deadlines() -> None:
    declines = FastDeclineSet(ttl_seconds=60.0, max_keys=3)
    declines.mark("a", 0.0)
    declines.mark("b", 1.0)
    declines.mark("c", 2.0)
    assert len(declines) == 3
    # Nothing expired yet: the earliest deadline ("a") goes.
    declines.mark("d", 3.0)
    assert len(declines) == 3
    assert not declines.contains("a", 3.0)
    assert declines.contains("b", 3.0) and declines.contains("c", 3.0) and declines.contains("d", 3.0)
    # Expired entries go first when the bound is hit later.
    declines.mark("e", 61.5)  # "b" (61.0) expired, "c"/"d" still live
    assert len(declines) == 3
    assert not declines.contains("b", 61.5)
    assert declines.contains("c", 61.5) and declines.contains("d", 61.5) and declines.contains("e", 61.5)


def test_fast_decline_rejects_degenerate_bounds() -> None:
    with pytest.raises(ValueError):
        FastDeclineSet(ttl_seconds=0)
    with pytest.raises(ValueError):
        FastDeclineSet(max_keys=0)
    assert FAST_DECLINE_MAX_KEYS == 10_000


# --- OverflowPinExecutor (fake sessions, virtual time) -------------------------------------


class _FakeResult:
    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self._rows = rows or []
        self.rowcount = 1

    def all(self) -> list[tuple[object, ...]]:
        return list(self._rows)

    def first(self) -> tuple[object, ...] | None:
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(
        self, *, fail_statement: Exception | None = None, rows: list[tuple[object, ...]] | None = None
    ) -> None:
        self.fail_statement = fail_statement
        self.rows = rows or []
        self.statements: list[str] = []
        self.committed = False

    async def connection(self) -> None:
        return None

    async def execute(self, statement: object, params: object = None) -> _FakeResult:
        self.statements.append(" ".join(str(statement).split()))
        if self.fail_statement is not None:
            raise self.fail_statement
        return _FakeResult(self.rows)

    async def commit(self) -> None:
        self.committed = True


def _session_factory(sessions: list[_FakeSession]):
    @asynccontextmanager
    async def factory():
        yield sessions.pop(0)

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


def _intent(thread_key: str | None = THREAD_KEY) -> PinIntent:
    return PinIntent(
        writes=(PinWrite("thread\n" + THREAD_KEY, "thread", "src_a", None),),
        thread_key=thread_key,
        source_id="src_a",
    )


@pytest.fixture
def virtual() -> tuple[VirtualClock, VirtualScheduler]:
    clock = VirtualClock(epoch_value=_T0.timestamp())
    return clock, VirtualScheduler(clock)


@pytest.mark.asyncio
async def test_written_commit_leaves_no_fast_decline_mark(virtual) -> None:
    clock, scheduler = virtual
    declines = FastDeclineSet()
    executor = OverflowPinExecutor(
        fast_decline=declines,
        session_factory=_session_factory([_FakeSession()]),
        writer_section=_open_writer_section,
    )
    outcome = await executor.commit(_intent(), drain_until=None, scheduler=scheduler, clock=clock)
    assert outcome == "written"
    assert not declines.contains(THREAD_KEY, clock.monotonic())
    assert executor.fast_decline is declines


@pytest.mark.asyncio
async def test_not_written_commit_marks_the_thread_key(virtual) -> None:
    """Mutant: no fast-decline mark => Codex's retry pays a second dispatch or lands on subscription."""

    clock, scheduler = virtual
    declines = FastDeclineSet()
    executor = OverflowPinExecutor(
        fast_decline=declines,
        session_factory=_session_factory([_FakeSession()]),
        writer_section=_blocked_writer_section(),
    )
    task = scheduler.create_task(executor.commit(_intent(), drain_until=None, scheduler=scheduler, clock=clock))
    await scheduler.advance(PIN_WRITE_ACQUIRE_DEADLINE_SECONDS + 0.01)  # the acquisition deadline decides the outcome
    assert task.result() == "not_written"
    marked_at = PIN_WRITE_ACQUIRE_DEADLINE_SECONDS  # the mark is stamped when the outcome is known, not at the call
    assert declines.contains(THREAD_KEY, clock.monotonic())
    assert declines.contains(THREAD_KEY, marked_at + PIN_FAILURE_FAST_DECLINE_SECONDS - 0.001)
    assert not declines.contains(THREAD_KEY, marked_at + PIN_FAILURE_FAST_DECLINE_SECONDS + 0.01)


@pytest.mark.asyncio
async def test_unknown_outcome_marks_the_thread_key(virtual) -> None:
    clock, scheduler = virtual
    declines = FastDeclineSet()
    # Statement fails, the verification re-read fails too -> ``unknown``.
    sessions = [_FakeSession(fail_statement=RuntimeError("boom")), _FakeSession(fail_statement=RuntimeError("reread"))]
    executor = OverflowPinExecutor(
        fast_decline=declines, session_factory=_session_factory(sessions), writer_section=_open_writer_section
    )
    outcome = await executor.commit(_intent(), drain_until=None, scheduler=scheduler, clock=clock)
    assert outcome == "unknown"
    assert declines.contains(THREAD_KEY, clock.monotonic())


@pytest.mark.asyncio
async def test_intent_without_a_thread_key_never_marks(virtual) -> None:
    """An anchored-only intent (no thread) has nothing to fast-decline."""

    clock, scheduler = virtual
    declines = FastDeclineSet()
    executor = OverflowPinExecutor(
        fast_decline=declines,
        session_factory=_session_factory([_FakeSession()]),
        writer_section=_blocked_writer_section(),
    )
    task = scheduler.create_task(
        executor.commit(_intent(thread_key=None), drain_until=None, scheduler=scheduler, clock=clock)
    )
    await scheduler.advance(20.0)
    assert task.result() == "not_written"
    assert len(declines) == 0


@pytest.mark.asyncio
async def test_base_executor_never_marks_so_bounce_rows_do_not_fast_decline(virtual) -> None:
    clock, scheduler = virtual
    declines = FastDeclineSet()
    base = PinWriteExecutor(
        session_factory=_session_factory([_FakeSession()]), writer_section=_blocked_writer_section()
    )
    task = scheduler.create_task(base.commit(_intent(), drain_until=None, scheduler=scheduler, clock=clock))
    await scheduler.advance(20.0)
    assert task.result() == "not_written"
    assert len(declines) == 0


def test_get_overflow_pin_executor_shares_the_process_fast_decline_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(overflow_module, "_PIN_EXECUTOR", None)
    monkeypatch.setattr(overflow_module, "_FAST_DECLINE", None)
    executor = get_overflow_pin_executor()
    assert get_overflow_pin_executor() is executor
    assert executor.fast_decline is get_fast_decline_set()
    assert isinstance(executor, PinWriteExecutor)


# --- PinToucher -----------------------------------------------------------------------------


class _CleanupRecorder:
    def __init__(self) -> None:
        self.actions: list[str] = []
        self.coroutines: list[Coroutine[Any, Any, None]] = []

    def _schedule_cancel_safe_cleanup(self, coro: Coroutine[Any, Any, None], *, action: str, request_id: str) -> None:
        self.actions.append(action)
        self.coroutines.append(coro)

    def close(self) -> None:
        for coro in self.coroutines:
            coro.close()


def _record(last_seen_at: datetime, pin_key: str = "thread\n" + THREAD_KEY) -> PinRecord:
    return PinRecord(
        pin_key=pin_key,
        kind="thread",
        source_id="src_a",
        api_key_id=None,
        created_at=last_seen_at,
        last_seen_at=last_seen_at,
        expires_at=last_seen_at + PIN_IDLE_TTL,
        purge_at=last_seen_at + PIN_IDLE_TTL + PIN_TOMBSTONE_GRACE,
    )


def test_toucher_schedules_a_background_touch_once_the_record_is_older_than_the_interval() -> None:
    toucher = PinToucher()
    recorder = _CleanupRecorder()
    try:
        fresh = _record(_T0)
        assert (
            toucher.maybe_touch(fresh, cleanup_scheduler=recorder, drain_until=None, now=_T0 + timedelta(minutes=59))
            is False
        )
        assert recorder.actions == []
        stale_now = _T0 + timedelta(seconds=PIN_TOUCH_INTERVAL_SECONDS)
        assert toucher.maybe_touch(fresh, cleanup_scheduler=recorder, drain_until=None, now=stale_now) is True
        assert recorder.actions == ["model_source_pin_touch"]
        # Served from the positive cache the record still reads stale: the memo keeps it to one touch per interval.
        assert (
            toucher.maybe_touch(
                fresh, cleanup_scheduler=recorder, drain_until=None, now=stale_now + timedelta(minutes=30)
            )
            is False
        )
        assert (
            toucher.maybe_touch(fresh, cleanup_scheduler=recorder, drain_until=None, now=stale_now + timedelta(hours=1))
            is True
        )
        assert len(recorder.actions) == 2
    finally:
        recorder.close()


def test_toucher_without_a_cleanup_scheduler_touches_nothing() -> None:
    toucher = PinToucher()
    assert (
        toucher.maybe_touch(_record(_T0), cleanup_scheduler=None, drain_until=None, now=_T0 + timedelta(days=1))
        is False
    )


def test_toucher_accepts_a_naive_now_and_bounds_its_memo() -> None:
    toucher = PinToucher(max_keys=2)
    recorder = _CleanupRecorder()
    try:
        now = (_T0 + timedelta(days=1)).replace(tzinfo=None)
        for index in range(4):
            assert toucher.maybe_touch(
                _record(_T0, pin_key=f"thread\nk{index}"), cleanup_scheduler=recorder, drain_until=None, now=now
            )
        assert len(recorder.actions) == 4
        assert len(toucher._scheduled_at) == 2
    finally:
        recorder.close()


def test_toucher_rejects_degenerate_bounds() -> None:
    with pytest.raises(ValueError):
        PinToucher(interval_seconds=0)
    with pytest.raises(ValueError):
        PinToucher(max_keys=0)


def test_get_pin_toucher_is_a_process_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(overflow_module, "_PIN_TOUCHER", None)
    first = get_pin_toucher()
    assert get_pin_toucher() is first


@pytest.mark.asyncio
async def test_touch_coroutine_slides_the_row_inside_the_writer_section(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, datetime, datetime | None]] = []
    order: list[str] = []

    class _Repository:
        def __init__(self, session: object) -> None:
            self.session = session

        async def touch(self, pin_key: str, *, now: datetime, drain_until: datetime | None) -> bool:
            calls.append((pin_key, now, drain_until))
            return True

    class _Session:
        async def commit(self) -> None:
            order.append("commit")

    @asynccontextmanager
    async def writer_section():
        order.append("writer_enter")
        yield
        order.append("writer_exit")

    @asynccontextmanager
    async def session_factory():
        order.append("session_enter")
        yield _Session()
        order.append("session_exit")

    monkeypatch.setattr(overflow_module, "ModelSourcePinRepository", _Repository)
    monkeypatch.setattr(overflow_module, "sqlite_writer_section", writer_section)
    monkeypatch.setattr(overflow_module, "get_background_session", session_factory)
    drain_until = _T0 + timedelta(days=29)
    await overflow_module._touch_pin("thread\n" + THREAD_KEY, now=_T0, drain_until=drain_until)
    assert calls == [("thread\n" + THREAD_KEY, _T0, drain_until)]
    assert order == ["writer_enter", "session_enter", "commit", "session_exit", "writer_exit"]

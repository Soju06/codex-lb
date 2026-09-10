"""Database round trips for the model-source pin primitive (#2123 WP-C1).

Runs against the suite's configured database: file-backed SQLite by default and
PostgreSQL when ``CODEX_LB_TEST_DATABASE_URL`` points at one (the CI matrix), so
the same assertions prove SQLite/PostgreSQL parity for the tz-aware pin
columns, the ``ON CONFLICT`` upsert, the DB-clock purge and the writer path.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.db.session import SessionLocal, sqlite_writer_section
from app.modules.proxy.model_source_pins import (
    PIN_KIND_ANCHOR,
    PIN_KIND_BOUNCE,
    PIN_KIND_THREAD,
    WS_BOUNCE_TTL_SECONDS,
    ModelSourcePinRepository,
    PinCache,
    PinIntent,
    PinLookupResult,
    PinRecord,
    PinWrite,
    PinWriteExecutor,
    lookup_pin_bounded,
)
from app.modules.settings.subscription_overflow import DRAIN_WINDOW, PIN_IDLE_TTL, PIN_TOMBSTONE_GRACE
from tests.simulation.virtual_time import VirtualClock, VirtualScheduler

pytestmark = pytest.mark.integration

_DAY = timedelta(days=1)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _write(
    pin_key: str, *, kind: str = PIN_KIND_THREAD, source_id: str = "src_a", api_key_id: str | None = None
) -> PinWrite:
    return PinWrite(pin_key=pin_key, kind=kind, source_id=source_id, api_key_id=api_key_id)


async def _upsert(*writes: PinWrite, now: datetime, drain_until: datetime | None = None) -> None:
    async with SessionLocal() as session:
        async with sqlite_writer_section():
            await ModelSourcePinRepository(session).upsert(list(writes), now=now, drain_until=drain_until)
            await session.commit()


async def _lookup(pin_key: str, *, now: datetime) -> PinLookupResult:
    async with SessionLocal() as session:
        return await ModelSourcePinRepository(session).lookup(pin_key, now=now)


async def _reread(pin_key: str) -> PinRecord | None:
    async with SessionLocal() as session:
        return await ModelSourcePinRepository(session).reread(pin_key)


@pytest.mark.asyncio
async def test_upsert_round_trip_and_lookup_states(db_setup) -> None:
    now = _now()
    await _upsert(_write("thread\nkey", api_key_id="key_1"), now=now)

    result = await _lookup("thread\nkey", now=now)
    assert result.state == "live"
    record = result.record
    assert record is not None
    assert record.pin_key == "thread\nkey"
    assert record.kind == PIN_KIND_THREAD
    assert record.source_id == "src_a"
    assert record.api_key_id == "key_1"
    assert record.created_at == now
    assert record.last_seen_at == now
    assert record.expires_at == now + PIN_IDLE_TTL
    assert record.purge_at == now + PIN_IDLE_TTL + PIN_TOMBSTONE_GRACE
    assert all(value.tzinfo is timezone.utc for value in (record.created_at, record.expires_at, record.purge_at))

    assert (await _lookup("thread\nkey", now=record.expires_at - timedelta(microseconds=1))).state == "live"
    assert (await _lookup("thread\nkey", now=record.expires_at)).state == "expired"
    assert (await _lookup("thread\nkey", now=record.purge_at - timedelta(microseconds=1))).state == "expired"
    assert (await _lookup("thread\nkey", now=record.purge_at)) == PinLookupResult("none", None)
    assert (await _lookup("thread\nmissing", now=now)) == PinLookupResult("none", None)


@pytest.mark.asyncio
async def test_upsert_slides_the_row_and_keeps_created_at(db_setup) -> None:
    now = _now()
    await _upsert(_write("thread\nkey"), now=now)
    await _upsert(_write("thread\nkey", source_id="src_b", api_key_id="key_2"), now=now + _DAY)
    record = await _reread("thread\nkey")
    assert record is not None
    assert record.created_at == now
    assert record.last_seen_at == now + _DAY
    assert record.expires_at == now + _DAY + PIN_IDLE_TTL
    assert record.purge_at == now + _DAY + PIN_IDLE_TTL + PIN_TOMBSTONE_GRACE
    assert record.source_id == "src_b"
    assert record.api_key_id == "key_2"


@pytest.mark.asyncio
async def test_upsert_rejects_keys_outside_their_kind_namespace(db_setup) -> None:
    async with SessionLocal() as session:
        repository = ModelSourcePinRepository(session)
        with pytest.raises(ValueError):
            await repository.upsert(
                [PinWrite("anchor\n-\nresp", PIN_KIND_THREAD, "src_a", None)], now=_now(), drain_until=None
            )
        with pytest.raises(ValueError):
            await repository.upsert([PinWrite("other\nkey", "other", "src_a", None)], now=_now(), drain_until=None)


@pytest.mark.asyncio
async def test_bounce_rows_expire_and_purge_together(db_setup) -> None:
    now = _now()
    await _upsert(_write("bounce\nkey", kind=PIN_KIND_BOUNCE), now=now)
    result = await _lookup("bounce\nkey", now=now)
    assert result.state == "bounce"
    assert result.record is not None
    assert result.record.expires_at == result.record.purge_at == now + timedelta(seconds=WS_BOUNCE_TTL_SECONDS)
    assert (await _lookup("bounce\nkey", now=now + timedelta(seconds=WS_BOUNCE_TTL_SECONDS))).state == "none"


@pytest.mark.asyncio
async def test_touch_slides_live_rows_only(db_setup) -> None:
    now = _now()
    await _upsert(_write("thread\nkey"), now=now)
    async with SessionLocal() as session:
        async with sqlite_writer_section():
            repository = ModelSourcePinRepository(session)
            assert await repository.touch("thread\nkey", now=now + _DAY, drain_until=None) is True
            await session.commit()
    record = await _reread("thread\nkey")
    assert record is not None
    assert record.created_at == now
    assert record.last_seen_at == now + _DAY
    assert record.expires_at == now + _DAY + PIN_IDLE_TTL

    await _upsert(_write("bounce\nkey", kind=PIN_KIND_BOUNCE), now=now)
    bounce = await _reread("bounce\nkey")
    async with SessionLocal() as session:
        async with sqlite_writer_section():
            repository = ModelSourcePinRepository(session)
            # A tombstone is never revived, an absent key never created, a bounce row never slid.
            assert await repository.touch("thread\nkey", now=record.expires_at, drain_until=None) is False
            assert await repository.touch("thread\nmissing", now=now, drain_until=None) is False
            assert await repository.touch("bounce\nkey", now=now + timedelta(seconds=1), drain_until=None) is False
            await session.commit()
    assert await _reread("thread\nkey") == record
    assert await _reread("bounce\nkey") == bounce


@pytest.mark.asyncio
async def test_delete_and_counts(db_setup) -> None:
    now = _now()
    async with SessionLocal() as session:
        repository = ModelSourcePinRepository(session)
        assert await repository.count_live_by_kind(now=now) == {}
        assert await repository.max_purge_at() is None
    await _upsert(
        _write("thread\na"),
        _write("thread\nb"),
        _write("anchor\n-\nresp", kind=PIN_KIND_ANCHOR),
        _write("bounce\nc", kind=PIN_KIND_BOUNCE),
        now=now,
    )
    await _upsert(_write("thread\nold"), now=now - 10 * _DAY)  # tombstone: not live
    async with SessionLocal() as session:
        repository = ModelSourcePinRepository(session)
        assert await repository.count_live_by_kind(now=now) == {"thread": 2, "anchor": 1, "bounce": 1}
        assert await repository.max_purge_at() == now + PIN_IDLE_TTL + PIN_TOMBSTONE_GRACE
        async with sqlite_writer_section():
            assert await repository.delete("thread\na") is True
            assert await repository.delete("thread\na") is False
            await session.commit()
        assert await repository.count_live_by_kind(now=now) == {"thread": 1, "anchor": 1, "bounce": 1}
    assert (await _lookup("thread\na", now=now)).state == "none"


@pytest.mark.asyncio
async def test_prune_purged_uses_the_database_clock_in_keyed_batches(db_setup) -> None:
    now = _now()
    await _upsert(_write("thread\np1"), _write("thread\np2"), _write("thread\np3"), now=now - 40 * _DAY)
    await _upsert(_write("thread\nlive"), now=now)
    await _upsert(_write("thread\ntombstone"), now=now - 10 * _DAY)  # expired but not purgeable yet

    async def prune(batch_size: int) -> int:
        async with SessionLocal() as session:
            async with sqlite_writer_section():
                deleted = await ModelSourcePinRepository(session).prune_purged(batch_size=batch_size)
                await session.commit()
        return deleted

    assert await prune(2) == 2
    assert await prune(2) == 1
    assert await prune(2) == 0
    assert (await _lookup("thread\nlive", now=now)).state == "live"
    assert (await _lookup("thread\ntombstone", now=now)).state == "expired"
    assert await _reread("thread\np1") is None
    with pytest.raises(ValueError):
        await prune(0)


@pytest.mark.asyncio
async def test_upsert_applies_the_drain_cap(db_setup) -> None:
    now = _now()
    drain_until = now + 10 * _DAY  # armed 19 days ago
    await _upsert(_write("thread\nkey"), now=now, drain_until=drain_until)
    result = await _lookup("thread\nkey", now=now)
    assert result.state == "expired"  # already past the cap: tombstone from the start
    assert result.record is not None
    assert result.record.expires_at == now - 12 * _DAY
    assert result.record.purge_at == now + 9 * _DAY
    assert result.record.purge_at < drain_until


@pytest.mark.asyncio
async def test_daily_touch_across_the_drain_never_slides_past_the_cap(db_setup) -> None:
    """CL-3 against the real table: touched every day, the pin expires at the cap and is gone at the deadline."""

    clock = VirtualClock(epoch_value=_now().timestamp())
    armed_at = clock.now()
    drain_until = armed_at + DRAIN_WINDOW
    cap = armed_at + PIN_IDLE_TTL
    await _upsert(_write("thread\nkey"), now=armed_at - 3 * _DAY)  # pinned before the operator cleared the setting

    states: list[str] = []
    for _day in range(0, 45):
        now = clock.now()
        result = await _lookup("thread\nkey", now=now)
        states.append(result.state)
        if result.state == "live":
            async with SessionLocal() as session:
                async with sqlite_writer_section():
                    assert await ModelSourcePinRepository(session).touch(
                        "thread\nkey", now=now, drain_until=drain_until
                    )
                    await session.commit()
        record = await _reread("thread\nkey")
        assert record is not None
        assert record.expires_at <= cap
        assert record.purge_at < drain_until
        clock.advance(_DAY.total_seconds())

    assert states[:7] == ["live"] * 7
    assert states[7:28] == ["expired"] * 21
    assert states[28:] == ["none"] * 17
    async with SessionLocal() as session:
        repository = ModelSourcePinRepository(session)
        latest = await repository.max_purge_at()
        assert latest is not None and latest < drain_until
        assert await repository.count_live_by_kind(now=drain_until) == {}
    assert (await _lookup("thread\nkey", now=drain_until)).state == "none"


@pytest.mark.asyncio
async def test_lookup_pin_bounded_reads_through_and_never_caches_absence(db_setup) -> None:
    cache = PinCache()
    clock = VirtualClock(epoch_value=_now().timestamp())
    scheduler = VirtualScheduler(clock)
    first = await lookup_pin_bounded("thread\nkey", cache=cache, scheduler=scheduler, clock=clock)
    assert first == PinLookupResult("none", None)
    assert len(cache) == 0
    await _upsert(_write("thread\nkey"), now=clock.now())
    second = await lookup_pin_bounded("thread\nkey", cache=cache, scheduler=scheduler, clock=clock)
    assert second.state == "live"
    assert cache.get("thread\nkey", now=clock.monotonic()) == second.record
    # The positive cache serves the next lookup even after the row is deleted underneath.
    async with SessionLocal() as session:
        async with sqlite_writer_section():
            await ModelSourcePinRepository(session).delete("thread\nkey")
            await session.commit()
    assert (await lookup_pin_bounded("thread\nkey", cache=cache, scheduler=scheduler, clock=clock)).state == "live"
    cache.invalidate("thread\nkey")
    assert (await lookup_pin_bounded("thread\nkey", cache=cache, scheduler=scheduler, clock=clock)).state == "none"


@pytest.mark.asyncio
async def test_executor_commit_and_delete_round_trip(db_setup) -> None:
    executor = PinWriteExecutor()
    now = _now()
    intent = PinIntent(
        writes=(
            _write("thread\nkey", api_key_id="key_1"),
            _write("anchor\nkey_1\nresp", kind=PIN_KIND_ANCHOR, api_key_id="key_1"),
        ),
        thread_key="thread\nkey",
    )
    assert await executor.commit(intent, drain_until=None) == "written"
    assert (await _lookup("thread\nkey", now=now)).state == "live"
    assert (await _lookup("anchor\nkey_1\nresp", now=now)).state == "live"

    drain_until = now + DRAIN_WINDOW
    assert await executor.commit(intent, drain_until=drain_until) == "written"
    record = await _reread("thread\nkey")
    assert record is not None and record.purge_at < drain_until

    cache = PinCache()
    cache.put(record, now=0.0)
    assert await executor.delete_durably("thread\nkey", cache=cache) == "written"
    assert cache.get("thread\nkey", now=0.0) is None
    assert await _reread("thread\nkey") is None
    assert await executor.delete_durably("thread\nkey") == "written"  # already gone: still verifiably released
    assert (await _lookup("anchor\nkey_1\nresp", now=now)).state == "live"


@pytest.mark.asyncio
async def test_executor_storm_p99_stays_under_the_acquisition_budget(db_setup) -> None:
    """CL-9: 50 concurrent verified writes on the configured database; the p99 must stay well under 5 s."""

    executor = PinWriteExecutor()
    latencies: list[float] = []

    async def one(index: int) -> str:
        started = time.perf_counter()
        outcome = await executor.commit(
            PinIntent(writes=(_write(f"thread\nstorm-{index}"),), thread_key=f"thread\nstorm-{index}"),
            drain_until=None,
        )
        latencies.append(time.perf_counter() - started)
        return outcome

    outcomes = await asyncio.gather(*(one(index) for index in range(50)))
    assert outcomes == ["written"] * 50
    p99 = statistics.quantiles(latencies, n=100)[98]
    assert p99 < 5.0, f"pin write p99 {p99:.3f}s (max {max(latencies):.3f}s)"
    async with SessionLocal() as session:
        assert await ModelSourcePinRepository(session).count_live_by_kind(now=_now()) == {"thread": 50}

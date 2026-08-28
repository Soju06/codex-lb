from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models import ApiKey
from app.db.session import SessionLocal
from app.modules.api_keys.last_used_coalescer import (
    SHUTDOWN_FLUSH_ATTEMPTS,
    ApiKeyLastUsedCoalescer,
    ApiKeyLastUsedFlushScheduler,
)

_BASE = datetime(2026, 8, 6, 12, 0, 0)


def _key_row(key_id: str) -> ApiKey:
    return ApiKey(
        id=key_id,
        name=f"key-{key_id}",
        key_hash=f"hash-{key_id}",
        key_prefix="sk-clb-test",
    )


async def _insert_keys(*key_ids: str) -> None:
    async with SessionLocal() as session:
        for key_id in key_ids:
            session.add(_key_row(key_id))
        await session.commit()


async def _stored_last_used(key_id: str) -> datetime | None:
    async with SessionLocal() as session:
        result = await session.execute(select(ApiKey.last_used_at).where(ApiKey.id == key_id))
        return result.scalar_one()


@pytest.mark.asyncio
async def test_record_coalesces_per_key_and_keeps_latest() -> None:
    coalescer = ApiKeyLastUsedCoalescer()

    await coalescer.record("key-a", _BASE)
    await coalescer.record("key-a", _BASE + timedelta(seconds=5))
    await coalescer.record("key-a", _BASE + timedelta(seconds=2))  # out-of-order, must not win
    await coalescer.record("key-b", _BASE + timedelta(seconds=1))

    assert coalescer.pending_snapshot() == {
        "key-a": _BASE + timedelta(seconds=5),
        "key-b": _BASE + timedelta(seconds=1),
    }


@pytest.mark.asyncio
async def test_flush_writes_latest_recorded_value_once(db_setup) -> None:
    del db_setup
    await _insert_keys("key-a", "key-b")
    coalescer = ApiKeyLastUsedCoalescer()
    await coalescer.record("key-a", _BASE)
    await coalescer.record("key-a", _BASE + timedelta(seconds=30))
    await coalescer.record("key-b", _BASE + timedelta(seconds=1))

    assert await coalescer.flush() == 2

    assert await _stored_last_used("key-a") == _BASE + timedelta(seconds=30)
    assert await _stored_last_used("key-b") == _BASE + timedelta(seconds=1)
    assert coalescer.pending_snapshot() == {}
    assert await coalescer.flush() == 0


@pytest.mark.asyncio
async def test_flush_never_regresses_a_newer_stored_value(db_setup) -> None:
    del db_setup
    await _insert_keys("key-a")
    newer = _BASE + timedelta(minutes=5)
    async with SessionLocal() as session:
        row = await session.get(ApiKey, "key-a")
        assert row is not None
        row.last_used_at = newer
        await session.commit()

    coalescer = ApiKeyLastUsedCoalescer()
    await coalescer.record("key-a", _BASE)  # older than stored (e.g. another replica flushed later)
    await coalescer.flush()

    assert await _stored_last_used("key-a") == newer


@pytest.mark.asyncio
async def test_flush_failure_retains_pending_touches(db_setup, monkeypatch: pytest.MonkeyPatch) -> None:
    del db_setup
    await _insert_keys("key-a")
    coalescer = ApiKeyLastUsedCoalescer()
    await coalescer.record("key-a", _BASE)

    async def _boom(session, batch) -> None:
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(ApiKeyLastUsedCoalescer, "_apply_batch", staticmethod(_boom))
    with pytest.raises(RuntimeError):
        await coalescer.flush()

    # The failed batch is merged back and a newer in-between record wins.
    await coalescer.record("key-a", _BASE + timedelta(seconds=10))
    assert coalescer.pending_snapshot() == {"key-a": _BASE + timedelta(seconds=10)}

    monkeypatch.undo()
    assert await coalescer.flush() == 1
    assert await _stored_last_used("key-a") == _BASE + timedelta(seconds=10)


@pytest.mark.asyncio
async def test_flush_cancelled_mid_write_retains_pending(db_setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """CancelledError is a BaseException; the swapped batch must be merged back."""
    del db_setup
    await _insert_keys("key-a")
    coalescer = ApiKeyLastUsedCoalescer()
    await coalescer.record("key-a", _BASE)

    async def _cancelled(session, batch) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(ApiKeyLastUsedCoalescer, "_apply_batch", staticmethod(_cancelled))
    with pytest.raises(asyncio.CancelledError):
        await coalescer.flush()

    assert coalescer.pending_snapshot() == {"key-a": _BASE}

    monkeypatch.undo()
    assert await coalescer.flush() == 1
    assert await _stored_last_used("key-a") == _BASE


@pytest.mark.asyncio
async def test_flush_with_retries_recovers_from_transient_failure(db_setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient DB error during the shutdown flush is retried, not dropped."""
    del db_setup
    await _insert_keys("key-a")
    coalescer = ApiKeyLastUsedCoalescer()
    await coalescer.record("key-a", _BASE)

    original_apply = ApiKeyLastUsedCoalescer._apply_batch
    attempts = 0

    async def _fail_once(session, batch) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient db error")
        await original_apply(session, batch)

    monkeypatch.setattr(ApiKeyLastUsedCoalescer, "_apply_batch", staticmethod(_fail_once))
    monkeypatch.setattr("app.modules.api_keys.last_used_coalescer.SHUTDOWN_FLUSH_RETRY_DELAY_SECONDS", 0.01)

    await coalescer.flush_with_retries()

    assert attempts == 2
    assert await _stored_last_used("key-a") == _BASE
    assert coalescer.pending_snapshot() == {}


@pytest.mark.asyncio
async def test_flush_with_retries_logs_pending_touches_after_exhaustion(
    db_setup, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """When every shutdown flush attempt fails, the pending keys and
    timestamps must land in a WARNING so operators can reconstruct them."""
    del db_setup
    await _insert_keys("key-a")
    coalescer = ApiKeyLastUsedCoalescer()
    await coalescer.record("key-a", _BASE)

    attempts = 0

    async def _boom(session, batch) -> None:
        nonlocal attempts
        attempts += 1
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(ApiKeyLastUsedCoalescer, "_apply_batch", staticmethod(_boom))
    monkeypatch.setattr("app.modules.api_keys.last_used_coalescer.SHUTDOWN_FLUSH_RETRY_DELAY_SECONDS", 0.01)

    with caplog.at_level(logging.WARNING, logger="app.modules.api_keys.last_used_coalescer"):
        await coalescer.flush_with_retries()  # must not raise

    assert attempts == SHUTDOWN_FLUSH_ATTEMPTS
    # Touches are retained in memory and their contents are in the final WARNING.
    assert coalescer.pending_snapshot() == {"key-a": _BASE}
    final_warnings = [record.getMessage() for record in caplog.records if "unflushed touches" in record.getMessage()]
    assert len(final_warnings) == 1
    assert "key-a" in final_warnings[0]
    assert _BASE.isoformat() in final_warnings[0]


@pytest.mark.asyncio
async def test_scheduler_stop_final_flush_retries_transient_failure(db_setup, monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: the shutdown final flush used to log-and-ignore a transient
    DB error; the periodic loop was already gone, so the restored batch died
    with the process. stop() must retry and persist the batch."""
    del db_setup
    await _insert_keys("key-a")
    coalescer = ApiKeyLastUsedCoalescer()
    scheduler = ApiKeyLastUsedFlushScheduler(coalescer, interval_seconds=3600)

    original_apply = ApiKeyLastUsedCoalescer._apply_batch
    attempts = 0

    async def _fail_once(session, batch) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient db error")
        await original_apply(session, batch)

    monkeypatch.setattr(ApiKeyLastUsedCoalescer, "_apply_batch", staticmethod(_fail_once))
    monkeypatch.setattr("app.modules.api_keys.last_used_coalescer.SHUTDOWN_FLUSH_RETRY_DELAY_SECONDS", 0.01)

    await scheduler.start()
    await coalescer.record("key-a", _BASE)
    await scheduler.stop()

    assert attempts == 2
    assert await _stored_last_used("key-a") == _BASE
    assert coalescer.pending_snapshot() == {}


@pytest.mark.asyncio
async def test_record_after_scheduler_stop_writes_through(db_setup) -> None:
    """Regression: a settlement task that outlives the shutdown drain can
    record() after the scheduler's final flush; with no flusher left the touch
    used to be silently lost. Post-stop records must write through to the DB
    immediately."""
    del db_setup
    await _insert_keys("key-a", "key-b")
    coalescer = ApiKeyLastUsedCoalescer()
    scheduler = ApiKeyLastUsedFlushScheduler(coalescer, interval_seconds=3600)

    await scheduler.start()
    await coalescer.record("key-a", _BASE)
    await scheduler.stop()
    assert await _stored_last_used("key-a") == _BASE

    # Late producer, e.g. a drain-timeout survivor settling after stop().
    await coalescer.record("key-b", _BASE + timedelta(seconds=7))

    assert await _stored_last_used("key-b") == _BASE + timedelta(seconds=7)
    assert coalescer.pending_snapshot() == {}


@pytest.mark.asyncio
async def test_scheduler_start_resets_write_through_mode(db_setup) -> None:
    del db_setup
    coalescer = ApiKeyLastUsedCoalescer()
    scheduler = ApiKeyLastUsedFlushScheduler(coalescer, interval_seconds=3600)

    await scheduler.start()
    await scheduler.stop()

    await scheduler.start()
    try:
        # Back in write-behind mode: records park in the pending map for the
        # periodic loop instead of writing through.
        await coalescer.record("key-a", _BASE)
        assert coalescer.pending_snapshot() == {"key-a": _BASE}
    finally:
        await scheduler.stop()


@pytest.mark.asyncio
async def test_scheduler_stop_during_inflight_flush_does_not_drop_batch(
    db_setup, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stop() must let an in-flight flush run to completion, not cancel it.

    Regression: stop() used to task.cancel() the loop; a CancelledError landing
    after ``flush()`` swapped the pending map bypassed the ``except Exception``
    restore path and dropped the batch, so the final flush wrote nothing.
    """
    del db_setup
    await _insert_keys("key-a")
    coalescer = ApiKeyLastUsedCoalescer()
    scheduler = ApiKeyLastUsedFlushScheduler(coalescer, interval_seconds=0.01)

    original_apply = ApiKeyLastUsedCoalescer._apply_batch
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _gated(session, batch) -> None:
        entered.set()
        await release.wait()
        await original_apply(session, batch)

    monkeypatch.setattr(ApiKeyLastUsedCoalescer, "_apply_batch", staticmethod(_gated))

    await coalescer.record("key-a", _BASE)
    await scheduler.start()
    await asyncio.wait_for(entered.wait(), timeout=5)

    stop_task = asyncio.create_task(scheduler.stop())
    await asyncio.sleep(0.05)
    assert not stop_task.done()  # stop() waits for the in-flight flush

    release.set()
    await asyncio.wait_for(stop_task, timeout=5)

    assert await _stored_last_used("key-a") == _BASE
    assert coalescer.pending_snapshot() == {}


@pytest.mark.asyncio
async def test_scheduler_stop_flushes_pending(db_setup) -> None:
    del db_setup
    await _insert_keys("key-a")
    coalescer = ApiKeyLastUsedCoalescer()
    scheduler = ApiKeyLastUsedFlushScheduler(coalescer, interval_seconds=3600)

    await scheduler.start()
    await coalescer.record("key-a", _BASE)
    await scheduler.stop()

    assert await _stored_last_used("key-a") == _BASE
    assert coalescer.pending_snapshot() == {}


@pytest.mark.asyncio
async def test_scheduler_periodic_tick_flushes(db_setup) -> None:
    del db_setup
    await _insert_keys("key-a")
    coalescer = ApiKeyLastUsedCoalescer()
    scheduler = ApiKeyLastUsedFlushScheduler(coalescer, interval_seconds=0.01)

    await coalescer.record("key-a", _BASE)
    await scheduler.start()
    try:
        for _ in range(200):
            if await _stored_last_used("key-a") == _BASE:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("periodic flush did not persist the recorded touch")
    finally:
        await scheduler.stop()

"""Regression tests for the per-session ``fast_lock`` constructor.

The HTTP bridge relay loop acquires its session locks several times per
upstream message for microseconds of synchronous bookkeeping. anyio's default
``Lock`` yields a full event-loop turn after every uncontended acquire; the
factory pins the ``fast_acquire=True`` choice so the hot loop does not pay a
scheduler round trip per acquire while mutual exclusion and ``acquire_nowait``
semantics stay intact.
"""

from __future__ import annotations

import asyncio

import anyio
import pytest

from app.core.utils.locks import fast_lock
from app.modules.proxy._service.support import _HTTPBridgeSession

pytestmark = pytest.mark.unit


async def _acquire_yields(lock: anyio.Lock) -> bool:
    """Return True when an uncontended acquire let a call_soon callback run."""

    loop = asyncio.get_running_loop()
    probe_ran = False

    def _probe() -> None:
        nonlocal probe_ran
        probe_ran = True

    loop.call_soon(_probe)
    async with lock:
        observed = probe_ran
    # Drain the probe so it never leaks into the next measurement.
    await asyncio.sleep(0)
    return observed


async def test_fast_lock_uncontended_acquire_does_not_yield() -> None:
    assert await _acquire_yields(fast_lock()) is False
    # Control: the default constructor is the one that yields.
    assert await _acquire_yields(anyio.Lock()) is True


async def test_fast_lock_keeps_mutual_exclusion_and_nowait_semantics() -> None:
    lock = fast_lock()
    assert isinstance(lock, anyio.Lock)
    held = asyncio.Event()
    release = asyncio.Event()

    async def _hold() -> None:
        async with lock:
            held.set()
            await release.wait()

    holder = asyncio.create_task(_hold())
    await asyncio.wait_for(held.wait(), timeout=1.0)
    assert lock.locked() is True
    # The idle-prune probe (_http_bridge_pending_count_nowait) relies on
    # acquire_nowait raising WouldBlock while another task holds the lock.
    with pytest.raises(anyio.WouldBlock):
        lock.acquire_nowait()
    entered: list[bool] = []

    async def _wait_turn() -> None:
        async with lock:
            entered.append(True)

    waiter = asyncio.create_task(_wait_turn())
    await asyncio.sleep(0)
    assert waiter.done() is False
    assert entered == []
    release.set()
    await asyncio.wait_for(holder, timeout=1.0)
    await asyncio.wait_for(waiter, timeout=1.0)
    assert entered == [True]
    assert lock.locked() is False
    lock.acquire_nowait()
    lock.release()


async def test_bridge_session_default_locks_are_fast_acquire() -> None:
    for field_name in ("lifecycle_lock", "recovery_alias_lock"):
        factory = _HTTPBridgeSession.__dataclass_fields__[field_name].default_factory
        assert callable(factory), field_name
        lock = factory()
        assert isinstance(lock, anyio.Lock)
        assert await _acquire_yields(lock) is False, field_name

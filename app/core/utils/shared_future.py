"""Await shared futures without per-waiter callbacks on the shared object.

``asyncio.wait_for(asyncio.shield(shared), timeout)`` attaches done callbacks
to ``shared`` for every waiter and removes them with O(n) list scans when a
waiter is cancelled or times out. With many waiters piled onto one long-lived
future (the http-bridge inflight/capacity registries, refresh singleflight),
a mass timeout turns the event loop into an O(N^2) callback grinder. Python
3.14's ``shield`` additionally leaks one ``_clear_awaited_by_callback`` per
attempt onto the still-pending future, so each retry cycle makes every later
scan more expensive. In the 2026-08-20 production incident this starved the
event loop for hours (98% of GIL samples inside ``Future.remove_done_callback``)
with zero client sessions attached.

``wait_on_shared_future`` keeps exactly one fan-out callback on the shared
future regardless of waiter count. Each waiter awaits its own single-use proxy
future, so waiter timeout and cancellation are O(1) set operations that never
touch the shared future's callback list.
"""

from __future__ import annotations

import asyncio
from typing import TypeVar

_T = TypeVar("_T")

_WAITERS_ATTR = "_shared_future_fanout_waiters"


def _fan_out(shared: "asyncio.Future[_T]", waiters: "set[asyncio.Future[_T]]") -> None:
    for waiter in waiters:
        if waiter.done():
            continue
        if shared.cancelled():
            waiter.cancel()
            continue
        exc = shared.exception()
        if exc is not None:
            waiter.set_exception(exc)
            # Consume eagerly: a waiter whose task was cancelled between this
            # fan-out and its resumption would otherwise log
            # "exception was never retrieved" from the proxy destructor.
            waiter.exception()
        else:
            waiter.set_result(shared.result())
    waiters.clear()


async def wait_on_shared_future(
    shared: "asyncio.Future[_T]",
    *,
    timeout: float | None = None,
) -> _T:
    """Drop-in equivalent of ``wait_for(shield(shared), timeout)`` for futures
    awaited by many concurrent waiters.

    - ``shared``'s result, exception, or cancellation propagates to every
      waiter exactly as with ``shield``.
    - ``timeout`` raises ``TimeoutError``; ``shared`` is never cancelled or
      otherwise mutated by a waiter timing out or being cancelled.
    - Cancelling the awaiting task detaches its proxy in O(1) and leaves
      ``shared`` (and the work it represents) running.
    """
    if shared.done():
        return shared.result()
    waiters: set[asyncio.Future[_T]] | None = getattr(shared, _WAITERS_ATTR, None)
    if waiters is None:
        # No await between the ``done()`` check above and this registration,
        # so the fan-out callback cannot have fired with an empty set.
        waiters = set()
        setattr(shared, _WAITERS_ATTR, waiters)
        shared.add_done_callback(lambda done, _waiters=waiters: _fan_out(done, _waiters))
    proxy: asyncio.Future[_T] = asyncio.get_running_loop().create_future()
    waiters.add(proxy)
    try:
        if timeout is None:
            return await proxy
        return await asyncio.wait_for(proxy, timeout)
    finally:
        waiters.discard(proxy)

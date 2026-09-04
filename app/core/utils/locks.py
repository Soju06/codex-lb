"""Lock constructors for per-session hot paths.

anyio's default ``Lock`` runs a cancel-shielded scheduling checkpoint
(``sleep(0)`` under a shielded ``CancelScope``) after every *uncontended*
acquire so that lock-heavy loops still yield to the event loop. The HTTP
bridge relay loop acquires its session ``pending_lock`` several times per
upstream message, each time for a few microseconds of synchronous
bookkeeping, and already yields on real network I/O every iteration — so the
checkpoint only adds a full event-loop round trip per acquire (~4 us plus one
loop iteration under uvloop; 1.4% of GIL samples in the 2026-09-03 production
profile).

``fast_lock`` documents the choice once: ``fast_acquire=True`` skips only the
post-acquire checkpoint on the uncontended path. Contended acquires still
suspend, ``checkpoint_if_cancelled`` still runs first, and ``acquire_nowait``
/ ``WouldBlock`` keep working, so mutual exclusion and cancellation delivery
are unchanged. Use it for locks whose critical sections are short and whose
callers await I/O between acquisitions; keep the default constructor for
module-level locks and for loops that could otherwise spin without awaiting.
"""

from __future__ import annotations

import anyio


def fast_lock() -> anyio.Lock:
    """Return an ``anyio.Lock`` whose uncontended acquire does not yield to the loop."""

    return anyio.Lock(fast_acquire=True)


__all__ = ["fast_lock"]

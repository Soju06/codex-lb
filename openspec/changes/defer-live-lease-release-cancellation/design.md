## Context

HTTP-bridge and other proxy cleanup paths already detach lease ownership, start
the release in an owned task, and wait through cancellation with the
`wait_on_shared_future`-based deferral helper. The Live handler instead awaits
`release_account_lease` directly from its cancelled `finally` path.

Python cancellation remains active while cleanup runs. A release that suspends
therefore can be cancelled before the load balancer returns the account slot.
The existing Live test misses this because its fake release completes
synchronously.

## Goals / Non-Goals

**Goals:**

- Complete Live account-lease release exactly once under cancellation.
- Reraise the original cancellation only after release completion.
- Preserve peer close behavior and error handling.

**Non-Goals:**

- Fixing direct WebSocket mixin cleanup in the same PR.
- Adding a new cleanup framework or shield loop.
- Changing lease selection, capacity, retries, or Live protocol envelopes.

## Decisions

Capture the Live lease in the existing handler scope, start its release as an
owned task, and wait with the established livelock-safe cancellation-deferral
helper. If cancellation was deferred, reraise it after the release task has
settled.

Alternative: wrap the raw await in `asyncio.shield`. Rejected because repeated
level cancellation can interrupt shield waits and recreate the known cleanup
livelock/leak class.

Alternative: combine all WebSocket cleanup points. Rejected because Live and
direct WebSocket have different lifecycle owners and proof seams.

## Risks / Trade-offs

- Cancellation returns slightly later while a contended release completes;
  this is required to return the account slot.
- Cleanup errors must retain their existing semantics; only cancellation timing
  changes.

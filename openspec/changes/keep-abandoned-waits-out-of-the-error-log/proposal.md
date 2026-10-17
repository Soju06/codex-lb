# Change: keep-abandoned-waits-out-of-the-error-log

## Why

`1.25.0-beta.9` on production logs, at `ERROR` with a traceback, for requests that returned 200:

```
ERROR: asyncio Task exception was never retrieved
future: <Task finished coro=<inject_sse_keepalives.<locals>._next_chunk() done,
  defined at /app/app/core/utils/sse.py:71> exception=StopAsyncIteration()>
```

Two in the first two hours after cutover. The mechanism is in `wait_on_shared_future`'s fan-out, not in the SSE code: `_fan_out` read `shared.exception()` *inside* the waiter loop, so a shared future that failed while its waiter set was empty had nobody retrieve it, and asyncio's destructor logs any unretrieved exception.

An empty waiter set is routine, not exotic. A waiter that times out discards its proxy, so between one timed wait and the next the shared future is unattended. The SSE keepalive injector sits in exactly that gap by design: after a keepalive timeout it yields the frame and suspends until the consumer asks again, leaving the chunk-pull task running with no waiter. A source that ends there finishes with `StopAsyncIteration` — the normal end of a stream — and the log line is emitted for a request that succeeded.

The cost is not the noise alone. `Task exception was never retrieved` is the signal operators use to find genuinely dropped exceptions, and an entry that fires on ordinary stream ends trains them to ignore it.

## What Changes

- `_fan_out` reads the shared future's exception **once, before the waiter loop**, so it is consumed whether or not a waiter is still attached. Delivery to live waiters is byte-for-byte unchanged, as is cancellation propagation.
- No behavioural change to what any waiter observes: a waiter still receives the same exception, result, or cancellation it did before. The only observable difference is that asyncio no longer reports the abandoned failure as an unretrieved exception.

No new setting, no schema change, no migration.

### What this deliberately does not do

- **It does not suppress unretrieved exceptions generally.** Only futures registered with `wait_on_shared_future` are affected, i.e. work whose result is already delivered through proxies to its owner. A task nobody ever waited on through this helper still reports normally.
- **It does not change the SSE injector.** The injector's `finally` still cancels a pull task that is genuinely in flight; the fix is in the shared-future helper because every caller of it has the same gap.

## Impact

- Code: `app/core/utils/shared_future.py` (one read hoisted out of a loop).
- Operators: one fewer false `ERROR` class in the proxy log. Nothing else in the log changes.

## Capabilities

- `proxy-runtime-observability`

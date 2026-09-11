## Why

`AccountDeletionScheduler.stop()` relied on `Task.cancel()` alone to end the
worker's idle `wait_for(self._wake.wait(), timeout=DELETION_INTERVAL_SECONDS)`.
The loop never observes `_stop` inside that wait, so any tick body that absorbs
the single cancellation leaves the loop parked for the full 30 s interval before
it re-reads the stop gate — and the tick's own session teardown is such a path.
When the tick's `SELECT` fails, `get_background_session` runs `_safe_rollback`
(`app/db/session.py`) in the loop's own frame, and that helper discards a cancel
landing inside it: the bounded SQLite teardown drops it in
`_shielded_bounded`'s `except asyncio.CancelledError: continue`, and the
unbounded (PostgreSQL) teardown re-raises it into `except BaseException: return`.
A 30 s park exceeds `shutdown_drain_timeout_seconds` and the owned launcher's
25 s lifespan-cleanup bound, so graceful shutdown degrades to a kill. #2338
reports the same shape in the suite — `tests/unit/test_otel.py` runs hanging
~30 s with the worker as the only scheduled timer on the loop — but the causal
link from this fix to that flake is inferred, not demonstrated (see the PR).

## What Changes

- `stop()` sets the wake signal before cancelling, so the interval wait ends on
  the same event-loop turn regardless of whether the cancellation is delivered.
- The released wait is a stop, not a nudge: the loop exits on the `_stop` gate
  without running another deletion pass.

## Capabilities

### Modified Capabilities
- graceful-shutdown: the account-deletion worker's stop no longer consumes a
  whole worker interval of the shutdown budget.

## Impact

`app/modules/accounts/deletion.py` only. No schema, API, or configuration
change; the drain semantics of an in-progress pass are unchanged.

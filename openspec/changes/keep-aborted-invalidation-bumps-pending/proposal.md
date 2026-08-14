## Why

The invalidation-bus spec already requires that "coalesced (`request_bump`) namespaces MUST remain pending and be retried on subsequent poll cycles until a bump succeeds". The implementation violated it for one case.

`_flush_pending_bumps` clears each namespace's pending marker before awaiting its write — deliberately, so a `request_bump()` arriving mid-write re-queues instead of being coalesced into the version already being written. But it restored the marker only when `bump()` returned `False`. A write that was **cancelled** or **raised** left the namespace neither written nor pending, with nothing logged and no retry holding it. Since `_run` swallows poll exceptions and keeps cycling, a raising write silently lost its namespace during ordinary operation.

## What Changes

- Restore the pending marker when the bump write is cancelled or raises, so the required retry actually happens. `except BaseException` rather than `Exception`, because `CancelledError` is the case that matters.

The restore is unconditional even when the abort's outcome is ambiguous (cancellation arriving after the database accepted the commit): a redundant bump only re-runs peers' idempotent invalidation callbacks, while dropping an unconfirmed write leaves them stale until the fallback TTL. The bus already tolerates extra version increments — `request_bump` arriving mid-flush deliberately produces one.

Process shutdown is deliberately out of scope: `stop()` cancels the polling task, so a bump queued at that moment has no cycle left to drain it. That is already the documented contract — "a lost bump still converges within the fallback TTL" — and guaranteeing delivery against an unresponsive database at shutdown is a separate concern with its own bounding and task-ownership design.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `query-caching`: state explicitly that an aborted (not merely failed) write keeps its namespace queued.

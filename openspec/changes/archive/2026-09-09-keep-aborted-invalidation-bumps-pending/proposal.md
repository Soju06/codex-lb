## Why

The invalidation-bus spec already requires that "coalesced (`request_bump`) namespaces MUST remain pending and be retried on subsequent poll cycles until a bump succeeds". The implementation violated it for one case.

`_flush_pending_bumps` clears each namespace's pending marker before awaiting its write — deliberately, so a `request_bump()` arriving mid-write re-queues instead of being coalesced into the version already being written. But it restored the marker only when `bump()` returned `False`. A write that was **cancelled** or **raised** left the namespace neither written nor pending, with nothing logged and no retry holding it. Since `_run` swallows poll exceptions and keeps cycling, a raising write silently lost its namespace during ordinary operation.

## What Changes

- Restore the pending marker when the bump write aborts, so the required retry actually happens. The two abort kinds are handled differently: `CancelledError` restores and re-raises (task teardown must abort the flush), while an ordinary `Exception` — abnormal, since `bump()` reports failure by returning `False` — restores, logs at warning, and continues, so a persistently raising namespace cannot starve the namespaces sorting after it.

The restore is unconditional even when the abort's outcome is ambiguous (cancellation or a driver error arriving after the database accepted the commit): a redundant bump only re-runs peers' idempotent invalidation callbacks, while dropping an unconfirmed write leaves them stale until the fallback TTL. The bus already tolerates extra version increments — `request_bump` arriving mid-flush deliberately produces one.

Process shutdown is deliberately out of scope: `stop()` cancels the polling task, so a bump queued at that moment has no cycle left to drain it. That is already the documented contract — "a lost bump still converges within the fallback TTL" — and guaranteeing delivery against an unresponsive database at shutdown is a separate concern with its own bounding and task-ownership design.

## Why the ambiguous case still restores

A cancellation or driver error can arrive after the database accepted the commit, so the restore can produce a redundant bump. That is the deliberate trade: a redundant bump only re-runs peers' idempotent invalidation callbacks, while dropping an unconfirmed write leaves them stale until the fallback TTL. The bus already tolerates extra increments — a `request_bump` arriving mid-flush produces one by design.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `query-caching`: state explicitly that an aborted (not merely failed) write keeps its namespace queued.

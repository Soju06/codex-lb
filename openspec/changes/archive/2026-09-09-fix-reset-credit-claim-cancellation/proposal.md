## Why

SQLite reset-credit redemption uses a durable per-account claim. If
cancellation is delivered again while holder-fenced release is suspended, the
operation terminates without releasing the row and healthy contenders wait for
lease expiry.

## What Changes

- Own heartbeat shutdown and holder-fenced release as one cleanup operation.
- Defer repeated caller cancellation until cleanup finishes.
- Add an event-gated repeated-cancellation regression.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `rate-limit-reset-credits`: live SQLite claims become immediately reclaimable
  after cancellation.

## Impact

- Shared dashboard and `/v1/reset-credit` serialization context.
- No migration, setting, API shape, lease timing, PostgreSQL, or in-process
  locking change.

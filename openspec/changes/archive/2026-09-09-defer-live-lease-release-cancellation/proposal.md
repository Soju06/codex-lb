## Why

The Live WebSocket handler releases its account stream lease from the same task
that handles downstream cancellation. If cancellation is delivered while the
lease release is suspended, cleanup can be interrupted even though the handler
is the lease's only remaining owner.

## What Changes

- Defer caller cancellation until Live account-lease release completes.
- Preserve exactly-once release and reraised cancellation semantics.
- Add event-gated regression coverage that cancels during a contended release.
- Reuse the existing cancellation-deferring cleanup helper without adding a
  new lifecycle abstraction.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-admission-control`: Extend post-detach cancel-safe lease settlement to
  Live stream leases.
- `realtime-api-compat`: Require Live cancellation to release the selected
  account lease exactly once before cancellation returns.

## Impact

- Live handler cleanup in
  `app/modules/proxy/_service/realtime_live.py`.
- Live cancellation tests in `tests/unit/test_realtime_live.py`.
- No direct WebSocket mixin, HTTP bridge, routing, quota, schema, database, or
  frontend changes.

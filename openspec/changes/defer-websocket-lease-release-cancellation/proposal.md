## Why

Direct WebSocket cancellation can interrupt an account-lease release after the request has cleared its ownership reference. The lost release consumes account-local stream capacity indefinitely, so cancellation must remain pending until lease cleanup completes.

## What Changes

- Require direct WebSocket connection and request-stream leases to finish releasing exactly once before cancellation is propagated.
- Cover connect-attempt, terminal-stream, current-account, and response-create cleanup seams only where ownership is cleared before release.
- Preserve existing non-cancellation success and failure behavior.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `proxy-admission-control`: Require direct WebSocket account capacity to be returned before cancellation escapes lease cleanup.

## Impact

- Affects direct WebSocket lifecycle cleanup in `app/modules/proxy/_service/websocket/mixin.py` and focused cancellation tests.
- Reuses the common cancellation-deferring task helper; no new setting, API, dependency, database change, HTTP bridge behavior, or Live behavior.

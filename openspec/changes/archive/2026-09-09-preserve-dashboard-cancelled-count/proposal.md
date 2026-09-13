## Why

The dashboard overview API emits `cancelledCount`, but the frontend Zod
boundary omits that field and silently strips it from otherwise valid metrics
payloads. Operators therefore cannot distinguish a window with no
cancellations from one whose cancellation count was discarded client-side.

## What Changes

- Preserve `cancelledCount` when the dashboard parses overview metrics.
- Add a frontend contract regression covering the backend's documented
  requests/error/cancelled breakdown.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `usage-error-metrics`: The dashboard frontend preserves the overview
  cancellation count emitted by the API.

## Impact

Frontend dashboard schema and its focused tests only. No backend, database, or
navigation changes.

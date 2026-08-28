## Why

Operators inspecting request details currently cannot see request latency in the detail dialog. The `latency_ms` field is already persisted and available in the API response but not rendered anywhere. Adding it next to the `Plan` field gives operators immediate visibility into response times.

## What Changes

- Add `formatElapsed(ms)` formatter that shows `<1000 ms` or `X.X s`
- Render `Elapsed` field in the request detail dialog, adjacent to `Plan`

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `frontend-architecture`: request detail dialog SHALL display latency as `Elapsed` next to `Plan`

## Impact

- `frontend/src/utils/formatters.ts` — new `formatElapsed` function
- `frontend/src/features/dashboard/components/recent-requests-table.tsx` — new `Elapsed` field in dialog

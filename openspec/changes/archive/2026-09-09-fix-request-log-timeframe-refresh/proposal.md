## Why

Request Logs `1h`, `24h`, and `7d` filters freeze one browser-derived `since`
timestamp for the selected filter lifetime. Refetches stop moving the rolling
window and inherit browser clock skew. Separately, a failed background listing
refresh hides rows TanStack Query retained.

## What Changes

- Add server-authoritative symbolic timeframes to listing and options.
- Keep literal `since`/`until`; reject ambiguous `timeframe + since`.
- Give count metadata semantic timeframe cache identity.
- Send symbolic timeframes from the dashboard.
- Keep retained rows visible with an announced refresh error and Retry.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `query-caching`
- `frontend-architecture`

## Impact

Request-log query parameters/cache identity, dashboard API/hook/rendering, and
focused tests. No response schema, setting, migration, dependency, or new UI
primitive.

## Why

Request logs persist downstream disconnects as `status='cancelled'`, but the
Request Logs read path only includes persisted `success` and `error` rows.
Cancelled requests therefore disappear from the unfiltered operator list and
cannot be selected through the status filter even though cancellation metrics
remain visible elsewhere.

## What Changes

- Include persisted cancelled rows in the default Request Logs listing and its
  rollup-backed total.
- Expose cancelled rows as the distinct public status `cancelled`.
- Add a `cancelled` status facet whose filter remains separate from genuine
  errors.
- Render and localize a distinct cancelled badge in the dashboard.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `usage-error-metrics`: Persisted cancellations remain visible and
  distinguishable from errors on the Request Logs operator surface.

## Impact

Request-log repository filtering/counting, service status mapping, dashboard
status labels, and focused backend/frontend regression tests. No database
migration, request producer, metric calculation, setting, navigation item, or
deployment change.

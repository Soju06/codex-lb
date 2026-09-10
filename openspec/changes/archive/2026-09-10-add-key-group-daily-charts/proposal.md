## Why

Group members can compare thirty-day totals but cannot see which keys consumed tokens or incurred cost on each day. A daily chart makes trends and spikes visible without exposing request details.

## What Changes

- Add per-member daily token and USD cost series to the existing authenticated group response.
- Render a chart in Group keys with token/cost selection, member visibility controls, daily values, and an accessible data table.
- Preserve the existing rolling thirty-day window, privacy boundary, and retained history semantics.

## Capabilities

### New Capabilities

### Modified Capabilities

- `api-key-dashboard`: Privacy-safe daily group usage series and interactive charts.

## Impact

Group usage repository, response schemas, service, React chart, integration tests, screenshots, and existing API-key documentation. Reuses stored request logs and hourly aggregates, with no migration, new setting, or dependency.

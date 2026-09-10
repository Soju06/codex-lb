## Why

Production 7-day reports take 44–53 seconds; 90-day reports fail with a driver timeout. Filter options reuse the full report endpoint; selecting a model or User-Agent triggers an additional full report query. Raw aggregates lose historical totals after retention.

## What Changes

- Add authenticated `GET /api/reports/options`, scoped by dates, timezone, accounts and API keys, without median or full report queries.
- Add a permanent report rollup keyed by UTC hour, account, API key, model, User-Agent and normalized conversation. Preserve additive measures and exact distinct counts for filtered daily and summary reads. UTC hours are the storage grain so timezone days, including DST, can be assembled correctly.
- Backfill in bounded background slices with an independent watermark; mirror account lifecycle mutations and gate retention on coverage. Read folded buckets and the raw complement in one SQL snapshot.
- Limit exact raw speed metrics to seven-day windows. Long reports explicitly disclose their omission and hide unavailable speed charts.
- Cache successful reports briefly on the server, coalesce concurrent loads, cache dashboard queries and remove periodic report polling. Provide manual refresh.

## Capabilities

### New Capabilities
- `report-aggregation`: report rollup, options and bounded speed computation.

### Modified Capabilities
- `frontend-architecture`: dedicated options reads, report freshness and explicit speed availability.
- `query-caching`: report distinct counts use the report history source for every filter.
- `data-retention`: pruning also waits for report fold coverage.

## Impact

One DDL-only Alembic migration; background backfill; report read path, account lifecycle/retention integration, backend and frontend regression tests. No new settings. Existing totals/filter semantics are preserved. Exact speed query rewrite is outside this change.

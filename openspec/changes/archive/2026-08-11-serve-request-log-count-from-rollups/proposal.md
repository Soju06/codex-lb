## Why

The request-log listing total is an exact `COUNT(*)` over the filtered set.
With the default filters that is a full pass over `request_logs` (no time
bound at all), re-run every 30 s when the short-TTL cache expires exactly at
the dashboard's poll interval — measured 4.4 s warm and 9–19 s under
contention at 4.5M rows, and it grows with history (full-history retention
is a supported configuration). The 30 s TTL cache bounds the frequency but
not the cost of each recount.

## What Changes

- Serve the listing total from the demand quarter rollup plus the un-folded
  raw tail whenever every active filter maps onto a demand dimension. The
  demand grain already carries `status` (unlike the hourly rollup), so time
  bounds, accounts, api keys, model/effort pairs, statuses, and soft-delete
  exclusion are all expressible; free-text search and error-code splits fall
  back to the exact raw count.
- The folded part of the window is one SQL `SUM(request_count)` bounded by
  the hourly watermark (new `sum_demand_window` read primitive following the
  shared partitioning rule); the complement raw windows are counted with the
  exact listing conditions. Counts remain exact — the total equals the
  legacy raw `COUNT(*)` for every watermark position, including the
  degenerate no-watermark state (empty folded sum, full-range raw count),
  so there is no kill switch.
- The per-filter-signature TTL cache stays in front of both paths.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `query-caching`: the request-log listing total MUST be computed from the
  demand rollup plus the raw tail for rollup-expressible filter
  signatures, with cost bounded by the un-folded tail instead of total
  history size, and MUST stay exactly equal to the raw count.

## Impact

`app/modules/request_logs/repository.py`,
`app/modules/accounts/usage_time_rollup_read.py` (new `sum_demand_window`),
rollup parity harness extended with listing-count shapes (including
cancelled-status corpus rows). No schema or API change.

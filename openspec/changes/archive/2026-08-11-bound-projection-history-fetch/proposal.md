## Why

`GET /api/dashboard/projections` fetches usage history for all accounts with
a single shared lookback equal to the *widest* account window
(`_load_projection_histories` takes the min `since` across accounts). One
weekly-window (or window-less fallback) account therefore widens the
PostgreSQL fetch to 7 days for every account, even though most accounts use
5-hour primary windows; the surplus rows are discarded in Python immediately
after the fetch. On the reference deployment this read returns ~165k rows
per window per poll (25 s under contention) where the per-account bounds
would keep it in the low thousands.

## What Changes

- `UsageRepository.bulk_history_since` accepts optional per-account
  `cutoffs`; on PostgreSQL the recency predicate becomes an OR of
  per-account `(account_id, recorded_at >= cutoff)` ranges (each range is a
  btree range scan over the existing window/account/recorded_at indexes)
  instead of one shared `recorded_at >= min(cutoffs)` bound.
- `_load_projection_histories` passes the per-account cutoff maps it already
  computes.
- The SQLite path is unchanged (its snapshot cache is keyed on the shared
  floor) and callers keep their existing per-account Python trimming, so
  results are byte-identical on every backend — only the number of rows
  read changes.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `query-caching`: projection history reads MUST bound the fetched rows per
  account by that account's own window lookback on PostgreSQL.

## Impact

`app/modules/usage/repository.py`, `app/modules/dashboard/repository.py`,
`app/modules/dashboard/service.py`. No schema or API change.

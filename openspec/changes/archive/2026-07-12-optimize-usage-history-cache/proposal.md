## Why

Large SQLite `usage_history` tables can make usage refresh and dashboard projection reads spike CPU. The latest-row path has already moved away from full-table window scans, but dashboard history caching still re-read and fingerprinted the entire cached window on every cache hit.

## What Changes

- Treat SQLite bulk usage-history cache entries as append-optimized snapshots.
- On cache hits, fetch only rows with IDs newer than the cached max ID and append them to the cached grouped history.
- Clear the cache from repository-owned account merge/delete paths that mutate existing usage-history rows.
- Add a SQLite fast path for additional-usage latest-per-account reads so additional quota lookups avoid `row_number()` window scans.

## Impact

- SQLite dashboard projection reads over large usage history.
- Additional quota latest-per-account reads on SQLite.
- Account repository usage-history mutation paths.

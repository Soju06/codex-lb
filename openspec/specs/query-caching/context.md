## Overview

The query-caching capability is broader than cache TTLs. It also owns the database query shapes that sit on hot request and dashboard paths, especially when SQLite is the default backend.

## Decisions

- Keep the public request-log and usage APIs unchanged; optimize query shape and indexing underneath them.
- Preserve legacy `usage_history.window IS NULL` semantics as `"primary"` instead of forcing a data backfill in this change.
- Avoid related-table joins on request-log listing unless search actually needs `accounts.email` or `api_keys.name`.

## Operational Notes

- Primary-window usage reads should normalize on `coalesce(window, 'primary')`.
- Latest usage selection should be backed by a composite latest-row index, not by Python-side deduplication.
- Default request-log listing should sort by latest-first timestamp and tie-breaker ID.

## Example

These rows must both participate in a primary-window lookup:

```text
usage_history(window=NULL, account_id='acc_1', recorded_at='2026-03-08T10:00:00Z')
usage_history(window='primary', account_id='acc_1', recorded_at='2026-03-08T11:00:00Z')
```

`latest_by_account("primary")` should return only the later row while still treating both rows as part of the same logical primary window.

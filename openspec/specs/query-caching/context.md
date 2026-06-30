## Overview

The query-caching capability is broader than cache TTLs. It also owns the database query shapes that sit on hot request and dashboard paths, especially when SQLite is the default backend.

## Decisions

- Keep the public request-log and usage APIs unchanged; optimize query shape and indexing underneath them.
- Preserve legacy `usage_history.window IS NULL` semantics as `"primary"` instead of forcing a data backfill in this change.
- Avoid related-table joins on request-log listing unless search actually needs `accounts.email` or `api_keys.name`.
- Avoid full window-ranking scans on hot selector and dashboard aggregate reads; prefer grouped latest-id or PostgreSQL `DISTINCT ON` shapes backed by matching indexes.
- Keep hot-path index migrations idempotent so manual production hotfix indexes do not break later schema upgrades.

## Operational Notes

- Primary-window usage reads should normalize on `coalesce(window, 'primary')`.
- Latest usage selection should be backed by a composite latest-row index, not by Python-side deduplication.
- Default request-log listing should sort by latest-first timestamp and tie-breaker ID.
- Do not hold the load-balancer runtime lock across network-bound usage refresh calls; only protect the in-memory selection and runtime-state mutation step.
- Stale usage refreshes should collapse into one in-flight refresh per account, with followers re-checking persisted primary-window data before calling the upstream usage API again.
- On 2026-06-29, production `10.0.0.113` saw Postgres backend OOM kills while dashboard/account-selection requests ran large `request_logs` and `additional_usage_history` window-ranking queries. The durable mitigation is to keep additional-quota latest lookups and account request usage summaries off `row_number()` hot paths, then restore any temporary production registry/timeout workarounds after deployment verification.

## Example

These rows must both participate in a primary-window lookup:

```text
usage_history(window=NULL, account_id='acc_1', recorded_at='2026-03-08T10:00:00Z')
usage_history(window='primary', account_id='acc_1', recorded_at='2026-03-08T11:00:00Z')
```

`latest_by_account("primary")` should return only the later row while still treating both rows as part of the same logical primary window.

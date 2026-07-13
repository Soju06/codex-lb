## Why

`GET /api/accounts` computes lifetime per-account request usage by aggregating the entire `request_logs` table on every page load: a dedupe subquery groups the whole table by `(account_id, request_id, requested_at)` (a nearly unique key) and joins it back to itself (`app/modules/accounts/repository.py:82-142`). No index can bound this — it is a guaranteed full scan plus self-join whose cost grows without limit as logs accumulate, and it is the single largest contributor to slow dashboard loads on PostgreSQL deployments. It also blocks any future log-retention policy: pruning old `request_logs` rows today would silently shrink the lifetime totals shown on the accounts page.

## What Changes

- Add a persistent per-account usage rollup table (`account_usage_rollups`) storing folded lifetime sums (request count, input/output/cached tokens, cost), plus a migration-seeded one-row watermark table (`account_usage_rollup_state`) holding the fold high-water mark (`folded_through`).
- Add a background fold job that periodically advances the rollup: it locks the state row, aggregates request-log rows in `(folded_through, now - lag]` using the existing dedupe semantics, adds them to the rollup, then advances the mark. The lag exceeds the maximum request duration so late-inserted rows (a log row is written at stream end but dated at request start) stay on the live side; because duplicates share an identical `requested_at`, a `requested_at`-based boundary can never split a duplicate group.
- Change `list_request_usage_summary_by_account` to return rollup sums plus a live dedupe aggregate over only the unfolded tail (`requested_at > folded_through`), which is bounded by the fold cadence and served by the existing `(account_id, requested_at)` index.
- Fold rollup maintenance into account lifecycle: deleting an account deletes its rollup row; deleting an account's history resets it.
- Alembic migration creates the table; the first fold run performs the one-time historical backfill in the background (no migration-time data copy).
- Result semantics are unchanged: same totals, same dedupe behavior, same warmup/deleted-row filtering. Only the query shape and freshness of the folded portion change (folded rows are immutable by construction).

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `query-caching`: the "Account request usage summary avoids request-log window ranking" contract is strengthened — account request-usage summaries MUST NOT scan the full `request_logs` history per read; they MUST combine a persistent rollup with a bounded live-tail aggregate while preserving dedupe and filtering semantics, and rollup state MUST follow account deletion/history-deletion lifecycle.

## Impact

- **Schema**: new `account_usage_rollups` table + Alembic migration (single-head, downgrade drops the table).
- **Code**: `app/modules/accounts/repository.py` (summary query, delete paths), new fold job under the existing background scheduler (`app/core/scheduling`/runtime module), `app/db/models.py`.
- **APIs**: `GET /api/accounts` response values unchanged; latency profile improves. No external contract change.
- **Ops**: one new periodic background job; fold cadence/lag are internal constants (no new operator settings unless design finds a need).
- **Interaction**: unlocks the planned `request_logs` retention/pruning change — pruned rows will already be folded, so lifetime account totals survive pruning.

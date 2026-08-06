# Add conversation presence rollup

## Why

The dashboard overview's conversation metrics are the last rollup-uncovered aggregates on the 30 s poll path: `aggregate_conversations_by_bucket`, the conversation half of `_aggregate_activity`, and the reports `aggregate_summary`/per-day conversation counts all run `COUNT(DISTINCT normalized conversation_id)` over the FULL raw `request_logs` window on every read (7–13 s observed in production). The `add-request-log-usage-rollups` change deliberately left them raw-bound ("distinct counts are not additive" was a documented non-goal); this change reverses that non-goal with a presence satellite whose reads dedup across the fold boundary, and as a consequence the conversation statistics now also survive request-log retention pruning.

## What Changes

- Add a permanent conversation presence satellite `request_conversation_hourly_rollups` keyed by `(bucket_epoch, conversation_id, account_id, is_deleted)` with an additive `request_count` measure. `conversation_id` is the normalized (trimmed, non-blank) value; `is_deleted` is a dimension because the dashboard conversation reads exclude soft-deleted rows while the reports reads include them; `account_id` (NULL-sentinel encoded) exists only so the account lifecycle mirrors stay exact.
- Add a dedicated `conversation_folded_through` watermark on `account_usage_rollup_state` (same single row and `FOR UPDATE` lock; existing rows backfilled to the epoch) and a `run_conversation_fold_pass` with the established slice contract (DELETE-then-INSERT, hour-aligned half-open windows, paced backfill), run as a third leg of the existing scheduler tick.
- Switch four conversation read sites (dashboard buckets + activity, reports summary + per-day) to a single-statement UNION of the folded presence and its exact raw complement, with `COUNT(DISTINCT)` deduplicating conversations that straddle the fold boundary. Reports reads take the rollup path only when unfiltered (the satellite has no model/useragent dimensions and pre-merges accounts). Epoch/missing watermark degrades to the exact legacy raw query — no kill switch.
- Extend the account lifecycle mirrors (soft delete, hard history delete, duplicate consolidation) to the satellite via the shared mirror table registry, and extend the retention prune gate's min-watermark to include the conversation watermark.
- One guarded DDL-only migration (one table, one state column); backfill is owned by the runtime fold pass.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `query-caching`: add the conversation presence satellite, its fold pass and watermark, and the rollup-plus-raw-tail switch for the distinct-conversation read paths (reversing the previous "conversation distinct counts stay raw-only" non-goal). This capability owns the rollup + live-tail pattern and the conversation aggregate query shapes.
- `data-retention`: request-log pruning gates on the minimum of ALL fold watermarks, now including `conversation_folded_through`, so conversation statistics are never destroyed before the satellite has folded them.

## Impact

- Affected code: `app/db/models.py` (one model, one state column), one Alembic revision, `app/modules/accounts/usage_time_rollup.py` (fold pass + mirror registry), `usage_time_rollup_read.py` (presence-union read primitives), `usage_rollup_scheduler.py` (third fold leg), `app/core/retention/job.py` (min gate), read paths in `app/modules/request_logs/repository.py` and `app/modules/reports/repository.py`.
- Affected tests: parity harness extended with conversation bucket series and reports snapshots plus conversation folds at every watermark state; retention-survival expectations for conversation metrics REVERSED (they now survive); new fold/dedup/lifecycle-mirror tests; a migration round-trip test; retention min-gate test for the conversation watermark.
- No API request/response schema change, no frontend change, no new settings.

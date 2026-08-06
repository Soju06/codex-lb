# Design — conversation presence rollup

## Context

Reference: `origin/main` 201281b5, mirroring the prior art in `add-request-log-usage-rollups` (hourly/error/demand rollups, `usage_time_rollup.py` + `usage_time_rollup_read.py`). Verified read-site filters as implemented today:

- Dashboard (`request_logs/repository.py`, `aggregate_conversations_by_bucket` + `_aggregate_activity`): `deleted_at IS NULL`, `request_kind NOT IN ('warmup','limit_warmup')`, normalized `conversation_id` non-blank (`nullif(ltrim(rtrim(...)), '')`).
- Reports (`reports/repository.py`, `aggregate_summary` + `_daily_rows_stmt`): `_normal_traffic_clause()` — same warmup-kind exclusion plus a belt-and-braces `source != 'limit_warmup'` — and NO `deleted_at` filter; optional account/model/useragent filters.

The two reader families disagree on soft-deleted rows, which forces `is_deleted` to be a dimension, not a fold filter.

## Decisions

### D1: Key = (bucket_epoch, conversation_id, account_id, is_deleted); measure = request_count

Distinct counts are not additive, so the satellite stores per-hour *presence* and the readers count `DISTINCT conversation_id` over folded ∪ raw. `request_count` stays additive for `conversation_request_count`. `is_deleted` reconciles the dashboard/reports filter split at read time. `account_id` (NULL-sentinel encoded, never read by the aggregates) is carried so ALL THREE lifecycle mirrors stay exact — the satellite simply joins the shared `_ROLLUP_TABLES` mirror registry:

- soft delete → re-key to `(sentinel, is_deleted=true)`, exactly mirroring the raw `account_id=NULL, deleted_at=now` UPDATE;
- hard history delete → `DELETE WHERE account_id = X`, exactly mirroring the raw row deletion (a conversation shared with a surviving account keeps the survivor's presence);
- duplicate consolidation → re-key to the canonical account.

This deliberately deviates from the sketched "no account dimension + watermark reset on hard delete" design: a reset would re-backfill the whole satellite from raw on every account deletion and permanently lose all conversation history older than the raw retention window each time. One extra key column buys exact O(account-history) mirrors and no reset machinery. The cost is one satellite row per (hour × conversation × account × deleted-split) instead of per (hour × conversation) — in practice a conversation rarely spans accounts within an hour.

### D2: Separate `conversation_folded_through` watermark, same state row

Same reasoning as `hourly_folded_through`: the satellite backfills from the epoch without rewinding the other rollups, and the single state row keeps one `FOR UPDATE` lock serializing every fold and mirror. The fold pass reuses the slice contract verbatim (DELETE-then-INSERT, 48 h slices, ≤20 per pass, hour-aligned half-open windows, FOLD_LAG target); the empty-prefix jump uses the fold-filtered `min(requested_at)` because only conversation-bearing non-warmup rows contribute.

### D3: Reads are ONE statement — the state row joined into both UNION branches

Distinct-dedup across the fold boundary needs folded ids and raw-tail ids in the same query. Rather than pre-reading the watermark (a torn read against the operator escape hatch), both UNION ALL branches join the state row: the folded branch takes buckets in `[ceil_hour(since), min(W, floor_hour(until)))`, the raw branch takes the exact complement `t < ceil_hour(since) OR t >= least(W, floor_hour(until))` (windows-bounded), OUTER-joining state so a missing row degrades to the full window. One statement = one snapshot, preserving the established consistency argument. The reports per-local-day variant (`conversation_labeled_presence_union`) carries per-day fold bounds as CTE literals, so half-hour-offset timezones and DST days partition exactly with no hour-alignment assumption.

### D4: Fold filter = normalized cid present AND non-warmup kind; `source` clause not folded

The reports readers' `source != 'limit_warmup'` clause cannot be a fold filter (the dashboard readers don't apply it) and is not worth a dimension: every writer couples `source='limit_warmup'` with `request_kind='warmup'` (`limit_warmup/service.py`), so the folded kind filter subsumes it for every row the system writes. The raw-tail side still applies the full `_normal_traffic_clause`. Documented residual: a hand-crafted row with a limit-warmup source but normal kind would be counted by the folded segment and excluded by a pure raw scan.

### D5: Reports rollup path only when unfiltered

`aggregate_summary`/`aggregate_daily_rows` accept account/model/useragent filters the satellite cannot express; filtered calls keep the legacy raw statement (including its inline conversation count). The unfiltered calls (the hot dashboard-shaped ones) split the conversation count out, like `_aggregate_activity` did in the prior change. Daily-report day-row membership stays raw-driven (the day CTE INNER-joins `request_logs`): a day whose raw rows are fully pruned drops out of the report exactly as before.

### D6: Retention gate includes the conversation watermark

`_prune_request_logs` takes `min(folded_through, hourly_folded_through, conversation_folded_through)` under the same `FOR UPDATE` read; the currency check is unchanged. Consequence (accepted, same as the hourly introduction): pruning pauses entirely until the conversation backfill catches up after deploy.

## Risks / Trade-offs

- Satellite cardinality is unbounded in `conversation_id`. Presence rows are bounded by request rows ever folded and are far smaller than `request_logs`; the error satellite already accepted the unbounded-dimension pattern.
- Escape hatch now spans four tables + two watermark columns (truncate + reset in one transaction). The parity harness pins the degrade-and-rebackfill behaviour.
- Mixed-version rolling deploys: an old replica neither folds nor reads the satellite (degrades to raw); the retention gate change rides the same release as the satellite, so no pruning can outrun it.

## Migration Plan

One guarded DDL-only revision (`20260806_010000_add_conversation_presence_rollup`, parent = the 20260803 merge head): create the empty satellite, add `conversation_folded_through` with an epoch server default (existing state rows backfill to epoch → paced runtime backfill). Downgrade drops both. No data migration ever.

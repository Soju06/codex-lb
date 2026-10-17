# Optimize dashboard hot-path indexes

## Why

On a production PostgreSQL deployment (3.07M `request_logs` rows, 1 GB heap), the quota-planner demand-bin slot aggregation (`QuotaPlannerRepository` in `app/modules/quota_planner/repository.py`, which filters `deleted_at IS NULL` and drives `GET /api/dashboard/*` usage views) executed as an index scan with per-row heap fetches, touching ~652,600 shared buffers (~5.1 GB of buffer traffic) per 7-day call. Inside a memory-limited container this page-cache churn repeatedly drove the cgroup to its limit; the kernel memcg OOM killer then killed PostgreSQL backends mid-query (six kills across 2026-07-15/16), forcing full crash recovery, dropping every application connection, and surfacing as 500 bursts on `/v1/chat/completions` and `/api/*`.

Two secondary factors amplified the pressure:

- `request_logs` carried 19 indexes (5.5 GB for a 1 GB heap), two of which were strict-prefix duplicates of wider indexes on the same table — pure write amplification with no read benefit (`pg_stat_user_indexes.idx_scan = 0` while their wider twins served all scans).
- The recurring distinct quota-label lookup over `additional_usage_history` (756k rows) ran as a full sequential scan (~38,800 buffers) on every dashboard quota poll.

Crash recovery also resets PostgreSQL's cumulative statistics, which kept autovacuum from ever triggering on the insert-heavy tables — leaving the visibility map empty (blocking index-only scans) and planner estimates off by 7x, making the whole cycle self-reinforcing.

## What Changes

- Add a forward-only Alembic migration `20260717_000000_optimize_dashboard_hot_path_indexes` that:
  - Creates `idx_logs_dash_usage_covering` on `request_logs (requested_at)` — on PostgreSQL with `INCLUDE (account_id, api_key_id, model, reasoning_effort, request_kind, status, input_tokens, cached_input_tokens, output_tokens, reasoning_tokens, cost_usd, id) WHERE deleted_at IS NULL` so the quota-planner slot aggregation runs as an index-only scan; on SQLite as a partial index on `requested_at`. On PostgreSQL the index is built with `CREATE INDEX CONCURRENTLY`, and a leftover invalid index from an interrupted concurrent build is detected via `pg_index.indisvalid` and rebuilt instead of being silently accepted by `IF NOT EXISTS`.
  - Creates `ix_additional_usage_distinct_labels` on `additional_usage_history (account_id, quota_key, limit_name, metered_feature)` so the distinct quota-label lookup is index-only.
  - Drops the redundant indexes `idx_logs_requested_at` (⊂ `idx_logs_requested_at_id`), `idx_logs_api_key_time_account` (filter phase fully served by `idx_logs_api_key_time`; zero scans in production), and `ix_additional_usage_history_account_id` (⊂ the new labels index) — on PostgreSQL via `DROP INDEX CONCURRENTLY` so a hot `request_logs` never queues writers behind an `ACCESS EXCLUSIVE` lock during startup migration.
  - Modifies the owning `api-keys` requirement ("API key 7-day account-cost queries use a composite request-log index") via this change's delta: the account-cost breakdown keeps an index-supported filter phase (`api_key_id`, descending `requested_at` as leading key columns of `idx_logs_api_key_time`), while the `account_id` grouping column is fetched from the heap — the per-key 7-day window bounds the row count, and production plan evidence showed the wider variant was never selected (`idx_scan = 0`).
  - Keeps `idx_logs_request_status_api_key_time`: the sessionless response-owner fallback (`find_latest_account_id_for_response_id`) orders by `requested_at DESC, id DESC` after an equality prefix, and `idx_logs_request_status_api_key_session_time` cannot serve that order because `session_id` sits between the prefix and the ordering columns.
  - On PostgreSQL, sets per-table autovacuum storage parameters (`autovacuum_vacuum_insert_scale_factor = 0.02`, `autovacuum_vacuum_insert_threshold = 50000`, `autovacuum_analyze_scale_factor = 0.02`) on `request_logs` and `additional_usage_history` so visibility-map freshness survives statistics resets after crash recovery.
- Register the new indexes in ORM metadata and the manual drift index requirements; remove the dropped indexes from both.
- Keep index creation idempotent (`CREATE INDEX IF NOT EXISTS` / `if_not_exists`), matching the live-hotfix path where the indexes may already exist before the migration is applied.

## Impact

Measured on the production PostgreSQL deployment after applying the same changes live (2026-07-16):

- Dashboard 7-day slot aggregation: 652,597 → 17,770 shared-buffer accesses (37x less buffer traffic), 5.1 s → 2.2 s, plan switched to a parallel index-only scan with ~1,500 heap fetches.
- 28-day slot aggregation now touches ~40,800 buffers (~320 MB) instead of multi-GB heap churn, executing in ~4.4 s warm.
- Distinct quota-label lookup: 38,828 → 826 buffer accesses (47x), ~320 ms → ~190 ms warm, and no longer rescans the heap on every dashboard quota poll.
- `request_logs` sheds ~1 GB of dead index weight and two index maintenance operations per insert; `additional_usage_history` sheds one.
- New index cost: `idx_logs_dash_usage_covering` measured 497 MB at 3.07M rows; `ix_additional_usage_distinct_labels` ~50 MB at 756k rows.
- No query semantics change; all reads previously served by the dropped indexes are served by their wider twins.

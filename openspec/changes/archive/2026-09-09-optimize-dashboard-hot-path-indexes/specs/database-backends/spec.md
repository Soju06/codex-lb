## ADDED Requirements

### Requirement: Dashboard usage aggregation reads are index-only on PostgreSQL

PostgreSQL deployments MUST maintain a covering partial index (`idx_logs_dash_usage_covering`) on `request_logs (requested_at)` that includes every column referenced by the quota-planner slot aggregation (`account_id`, `api_key_id`, `model`, `reasoning_effort`, `request_kind`, `status`, `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_tokens`, `cost_usd`, `id`) and is filtered to `deleted_at IS NULL`, so the aggregation can be satisfied without heap access. SQLite deployments MUST maintain the same index as a partial index on `requested_at`.

#### Scenario: Slot aggregation avoids heap churn

- **GIVEN** the database backend is PostgreSQL
- **AND** `request_logs` contains live (non-deleted) rows in the requested timeframe
- **WHEN** the dashboard requests the usage slot aggregation for any timeframe
- **THEN** PostgreSQL MUST be able to satisfy the time-range filter and every aggregated column from `idx_logs_dash_usage_covering` alone
- **AND** the aggregation result MUST remain semantically identical to the previous heap-backed plan

#### Scenario: Migration is safe after a live hotfix

- **GIVEN** `idx_logs_dash_usage_covering` or `ix_additional_usage_distinct_labels` was already created manually as a live hotfix
- **WHEN** the schema migration is applied
- **THEN** the migration MUST complete without failing on duplicate index creation

#### Scenario: Interrupted concurrent build is repaired, not accepted

- **GIVEN** the database backend is PostgreSQL
- **AND** a previous `CREATE INDEX CONCURRENTLY` for `idx_logs_dash_usage_covering` or `ix_additional_usage_distinct_labels` was interrupted, leaving an invalid index (`pg_index.indisvalid = false`) under the same name
- **WHEN** the schema migration is applied
- **THEN** the migration MUST drop the invalid index and rebuild it rather than accepting it via `IF NOT EXISTS`

### Requirement: Distinct quota-label lookups are index-only

Deployments MUST maintain a composite index (`ix_additional_usage_distinct_labels`) on `additional_usage_history (account_id, quota_key, limit_name, metered_feature)` so the recurring distinct quota-label lookup does not scan the table heap.

#### Scenario: Quota-label poll uses the composite index

- **GIVEN** `additional_usage_history` contains usage rows for one or more accounts
- **WHEN** the dashboard polls for the distinct `(quota_key, limit_name, metered_feature)` labels of a set of accounts
- **THEN** the lookup MUST be satisfiable from `ix_additional_usage_distinct_labels` without reading table rows

### Requirement: Insert-heavy dashboard tables keep autovacuum effective on PostgreSQL

PostgreSQL deployments MUST set per-table autovacuum storage parameters on `request_logs` and `additional_usage_history` (`autovacuum_vacuum_insert_scale_factor = 0.02`, `autovacuum_vacuum_insert_threshold = 50000`, `autovacuum_analyze_scale_factor = 0.02`) so that visibility-map freshness and planner statistics recover promptly even after crash recovery resets the cumulative statistics counters.

#### Scenario: Autovacuum triggers after bounded insert volume

- **GIVEN** the database backend is PostgreSQL
- **AND** the cumulative statistics counters were recently reset (for example by crash recovery)
- **WHEN** inserts accumulate on `request_logs` or `additional_usage_history`
- **THEN** autovacuum MUST become eligible for the table after the configured insert threshold instead of the global default scale factor

### Requirement: Redundant request-log indexes are not maintained

The schema MUST NOT maintain indexes on `request_logs` or `additional_usage_history` whose read paths are fully served by another maintained index on the same table. Specifically, `idx_logs_requested_at`, `idx_logs_api_key_time_account`, and `ix_additional_usage_history_account_id` are dropped; their read paths are served by `idx_logs_requested_at_id`, `idx_logs_api_key_time`, and `ix_additional_usage_distinct_labels` respectively. `idx_logs_request_status_api_key_time` MUST be kept: the sessionless response-owner fallback lookup orders by `requested_at DESC, id DESC` after an equality prefix, and the session-scoped index (`idx_logs_request_status_api_key_session_time`) cannot return that order because `session_id` precedes the ordering columns. On PostgreSQL the redundant indexes MUST be dropped with `DROP INDEX CONCURRENTLY` so writers are not queued behind an `ACCESS EXCLUSIVE` lock during startup migration.

#### Scenario: Reads previously served by a dropped index use its wider twin

- **WHEN** a query filters or orders by the leading columns of a dropped redundant index
- **THEN** the query MUST be satisfiable by the wider index that shares the same leading key columns
- **AND** query results MUST remain semantically identical

#### Scenario: Sessionless response-owner fallback keeps ordered retrieval

- **WHEN** the response-owner lookup falls back to a sessionless search by `request_id` and `status`
- **THEN** the newest matching row by `requested_at DESC, id DESC` MUST be retrievable from `idx_logs_request_status_api_key_time` in index order

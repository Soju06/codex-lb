# data-retention Specification

## ADDED Requirements

### Requirement: Retention is opt-in and validated

Retention MUST be disabled by default. `request_log_retention_days` and `usage_history_retention_days` MUST accept `0` (disabled) or values at or above their safety floors (30 days for request logs, 45 days for usage history); configurations between 1 and the floor MUST be rejected at startup.

#### Scenario: Default configuration deletes nothing

- **GIVEN** neither retention setting is configured
- **WHEN** the retention job runs
- **THEN** no rows are deleted from `request_logs`, `usage_history`, or `additional_usage_history`

#### Scenario: Unsafe retention values fail fast

- **WHEN** an operator sets `request_log_retention_days=7` or `usage_history_retention_days=10`
- **THEN** settings validation MUST raise an error at startup naming the violated floor

### Requirement: Request-log pruning never deletes unfolded rows

Request-log pruning MUST run only while the fold is current (watermark within two fold lags of now) and MUST delete only rows with `requested_at` older than the retention cutoff AND at least one fold lag below the watermark, so concurrent summary readers holding a slightly older watermark can never lose rows from a just-folded window. When no rollup watermark exists, or the fold is catching up (initial backfill, stalled scheduler), request-log pruning MUST be skipped.

#### Scenario: Unfolded rows survive pruning

- **GIVEN** a request-log row older than the retention cutoff whose `requested_at` is above the fold watermark
- **WHEN** the retention job runs
- **THEN** the row MUST NOT be deleted

#### Scenario: Stalled fold suspends pruning

- **GIVEN** a fold watermark older than two fold lags
- **WHEN** the retention job runs with request-log retention enabled
- **THEN** no `request_logs` rows are deleted

#### Scenario: Lifetime totals are unchanged by pruning

- **GIVEN** folded request-log rows older than the retention cutoff
- **WHEN** the retention job deletes them and account usage summaries are read afterwards
- **THEN** per-account lifetime totals MUST equal their pre-pruning values

#### Scenario: Pruning is skipped before the first fold

- **GIVEN** no `account_usage_rollup_state` row exists
- **WHEN** the retention job runs with request-log retention enabled
- **THEN** no `request_logs` rows are deleted

### Requirement: Usage-history pruning preserves each identity's latest row

Usage-history pruning MUST delete only rows older than the retention cutoff and MUST always retain each identity's latest row per `(account_id, coalesce(window,'primary'))` in `usage_history` and per `(account_id, quota_key, window)` in `additional_usage_history`, regardless of age. "Latest" MUST follow the readers' ordering — newest `recorded_at`, protecting every row tied at that timestamp — not insertion order, so backfilled out-of-chronology rows cannot displace the last-known sample. On SQLite, the bulk-history cache MUST be invalidated after pruning.

#### Scenario: Idle account keeps its last-known usage

- **GIVEN** an account whose only usage rows are older than the retention cutoff
- **WHEN** the retention job runs
- **THEN** the newest row per window for that account MUST remain
- **AND** older rows for the same window MUST be deleted

#### Scenario: Out-of-chronology inserts keep the true latest sample

- **GIVEN** an identity whose highest-id row carries an older `recorded_at` than an earlier-inserted row
- **WHEN** the retention job runs
- **THEN** the row with the newest `recorded_at` MUST remain

### Requirement: Retention runs leader-gated in bounded batches

The retention job MUST run on at most one instance at a time and MUST delete in bounded batches, each committed in its own transaction, so a large backlog never holds one long transaction.

#### Scenario: Backlog is pruned incrementally

- **GIVEN** more prunable rows than one batch
- **WHEN** a retention pass runs
- **THEN** rows are deleted across multiple bounded transactions until no prunable rows remain

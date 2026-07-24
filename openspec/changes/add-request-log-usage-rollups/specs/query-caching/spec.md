## ADDED Requirements

### Requirement: Time-axis usage rollups persist hourly statistics permanently

The system SHALL maintain three permanent time-axis rollup tables folded from `request_logs`: an hourly UTC-bucketed usage rollup dimensioned by `(bucket_epoch, account_id, api_key_id, model, service_tier, request_kind, is_deleted)` with additive measures (request count, error count, input/output/reasoning tokens, output-or-reasoning tokens, cached input tokens, per-row-clamped cached input tokens, cost, non-NULL-cost row count); an hourly error-code rollup dimensioned by `(bucket_epoch, account_id, error_code)`; and a quarter-hour demand rollup dimensioned by `(slot_epoch, account_id, request_kind, is_deleted)` with the demand measures the quota planner consumes. Bucket keys MUST be integer epoch seconds aligned to the bucket size. Nullable raw dimensions (`account_id`, `api_key_id`, `service_tier`) MUST be stored as the empty-string sentinel so they can participate in the primary key identically on SQLite and PostgreSQL. The hourly rollup MUST fold all request-log rows, including warmup kinds and soft-deleted rows, as dimension values rather than fold-time filters, and MUST NOT apply the lifetime rollup's duplicate collapsing. Rollup rows MUST NOT be deleted by data retention.

#### Scenario: Statistics survive raw pruning

- **GIVEN** folded hourly rollup rows whose source `request_logs` rows have been pruned by retention
- **WHEN** dashboard bucket aggregation, activity aggregates, top error, planner demand bins, or API-key trends are read over that period
- **THEN** the returned statistics MUST equal the values reported before the pruning
- **AND** only the documented raw-only metrics (conversation distinct counts, reports medians) may change

#### Scenario: Clamped and fallback measures are folded at write time

- **GIVEN** request-log rows with reasoning-only output, NULL cost, and cached input tokens exceeding input tokens
- **WHEN** the rows are folded
- **THEN** the hourly rollup MUST carry `sum(coalesce(output, reasoning, 0))`, the per-row `max(0, min(cached, input))` clamp sum, and the count of non-NULL-cost rows as distinct columns, because none of them can be derived from the plain sums after raw rows are pruned

#### Scenario: Warmup and soft-deleted rows are dimensions, not fold filters

- **GIVEN** warmup-kind and soft-deleted request-log rows
- **WHEN** they are folded
- **THEN** they MUST be present in the hourly rollup under their `request_kind` and `is_deleted` dimension values
- **AND** readers that exclude them MUST do so by dimension filtering at read time

### Requirement: An incremental hourly fold pass advances a separate hour-aligned watermark

A fold pass SHALL run inside the existing account-usage-rollup scheduler tick, after the lifetime fold, under the same leader gate and the same fold-state row lock, advancing a dedicated `hourly_folded_through` watermark on `account_usage_rollup_state`. The watermark MUST always be hour-aligned; fold windows are half-open `[start, end)` intervals; the fold target MUST lag `now` by the safety lag so late-inserted rows (log rows are written at stream end but dated at request start) and post-hoc rewrites are absorbed. Each slice MUST, in one transaction, delete existing rollup rows in the slice window, insert the recomputed aggregates, and advance the watermark, so repeated, resumed, or rewound folding always converges to the same table contents. Historical backfill MUST be performed by the same pass in bounded slices with a bounded number of slices per pass, and MUST NOT be performed by a migration. The lifetime fold watermark, `account_usage_rollups`, and `api_key_usage_rollups` MUST NOT be modified by this pass.

#### Scenario: Fold is idempotent and crash-safe

- **GIVEN** a fold pass that has committed some slices and then fails
- **WHEN** the pass is re-run to completion
- **THEN** the three rollup tables MUST be byte-identical to those produced by one uninterrupted run
- **AND** re-running a completed pass with the same clock MUST change nothing

#### Scenario: A rewound watermark self-heals

- **GIVEN** rollup rows exist and `hourly_folded_through` is reset to epoch while raw history is still present
- **WHEN** subsequent fold passes run
- **THEN** re-folding MUST converge to exactly the recomputed aggregates with no double counting and no stale rows in re-folded windows

#### Scenario: Folded buckets are never recomputed from raw

- **GIVEN** a bucket entirely below `hourly_folded_through`
- **WHEN** any scheduled fold pass runs
- **THEN** no code path may recompute that bucket from `request_logs`
- **AND** the only permitted post-fold mutations are the lifecycle mirror operations

#### Scenario: Backfill is paced and immediate

- **GIVEN** a deployment with months of request-log history and an epoch watermark
- **WHEN** the scheduler becomes leader
- **THEN** the first tick MUST begin folding immediately, skipping empty history via a minimum-timestamp pre-scan
- **AND** each pass MUST fold at most the configured constant number of bounded slices, resuming on the next tick

### Requirement: Time-series reads combine the hourly rollups with a raw live tail

The dashboard bucket aggregation, activity aggregates, top-error, earliest-activity, quota-planner demand-bin, and API-key trends reads MUST serve history below the hourly watermark from the rollup tables and rows at or above the watermark from `request_logs`, merged through one shared helper. The watermark and rollup aggregates MUST be fetched in a single statement so both come from one database snapshot. Because the watermark is hour-aligned and display buckets are multiples of an hour, the folded and tail segments MUST partition the data exactly and merged results MUST equal the legacy full-raw aggregation whenever the underlying raw rows still exist; a parity harness enforcing per-field equality across all switched paths at epoch, mid-history, and current watermarks is a required deliverable. Return shapes, service layers, API schemas, and the dashboard frontend MUST be unchanged. When the watermark is at epoch the reads MUST degrade to exactly the legacy raw queries, which is the designated fallback: no kill switch, setting, or environment variable is added. Non-additive metrics (conversation distinct counts, reports medians, timezone-day reports aggregation) remain raw-only and are out of scope.

#### Scenario: Folded history is not rescanned at read time

- **GIVEN** a populated hourly rollup with watermark `W`
- **WHEN** a switched read spans a window extending below `W`
- **THEN** the raw `request_logs` aggregate MUST be constrained to `requested_at >= W` (plus the path's existing filters)
- **AND** the sub-`W` contribution MUST come from the rollup tables

#### Scenario: Switched reads equal legacy reads while raw exists

- **GIVEN** a corpus containing warmup kinds, soft-deleted rows, NULL account/key/tier dimensions, duplicate rows, reasoning-only outputs, NULL costs, and cached-exceeding-input rows
- **WHEN** each switched read runs with the watermark at epoch, mid-history on an hour boundary, and at the fold target
- **THEN** every result MUST equal the legacy raw-only implementation field-for-field, with cost compared at floating-point tolerance
- **AND** top-error ties MUST resolve deterministically (highest count, then error code ascending)

#### Scenario: Reader is snapshot-consistent under a concurrent fold

- **GIVEN** a fold slice may commit between a reader's rollup fetch and its tail fetch
- **WHEN** the read merges the two segments
- **THEN** the watermark used for the tail bound MUST be the one fetched atomically with the rollup aggregates, so no row's contribution is counted twice or lost

#### Scenario: Planner demand bins preserve deletion semantics

- **WHEN** demand bins are read from the quarter-hour rollup
- **THEN** the folded segment MUST filter `is_deleted = false`, reproducing the raw `deleted_at IS NULL` filter
- **AND** the returned bin shape and the planner call sites MUST be unchanged

#### Scenario: Empty rollup degrades to legacy behavior

- **GIVEN** `hourly_folded_through` at epoch (before the first fold or after an escape-hatch reset)
- **WHEN** any switched read runs
- **THEN** results and performance characteristics MUST be equivalent to the pre-change raw implementation

### Requirement: Time-axis rollups mirror request-log history rewrites transactionally

Every code path that mutates request-log rows below the hourly watermark MUST take the fold-state row lock and mirror the mutation into the three time-axis rollup tables in the same transaction. Account soft deletion (which reattributes the account's request logs to NULL/deleted) MUST merge the account's rollup rows into the corresponding `(account_id = '', is_deleted = true)` keys and delete the source rows; account hard deletion (`delete_history`) MUST delete the account's rollup rows; duplicate-account consolidation MUST merge the duplicate's rollup rows into the canonical account bucket-wise and delete the duplicate's rows. This discipline SHALL be documented as a standing constraint for future history-rewriting code.

#### Scenario: Soft deletion moves folded usage under the deleted dimension

- **GIVEN** an account with folded hourly and quarter-hour usage
- **WHEN** the account is deleted without `delete_history`
- **THEN** its rollup contributions MUST move to the empty-account deleted dimension in the same transaction
- **AND** dimension-blind totals (activity, bucket aggregation) MUST be unchanged
- **AND** deletion-filtered reads (planner demand) MUST stop counting it, matching the raw reattribution

#### Scenario: Hard deletion removes folded usage

- **GIVEN** an account with folded usage
- **WHEN** the account is deleted with `delete_history`
- **THEN** its rows in all three rollup tables MUST be deleted in the same transaction

#### Scenario: Lifecycle mirror serializes with the fold

- **GIVEN** a fold slice in progress in another session
- **WHEN** an account deletion or consolidation runs
- **THEN** both MUST serialize on the fold-state row lock so the mirror can never operate on a snapshot the fold is concurrently overwriting

#### Scenario: Escape hatch is atomic

- **WHEN** operators rebuild the time-axis rollups after an incident
- **THEN** deleting all rows of the three rollup tables and resetting `hourly_folded_through` to epoch MUST occur in one transaction
- **AND** performing only one of the two operations is a documented forbidden state

## ADDED Requirements

### Requirement: Request-log retention is apply-only by default

The service SHALL NOT delete raw `request_logs` rows unless an operator invokes
an apply-mode retention command.

#### Scenario: Default deployment preserves raw logs

- **WHEN** the service starts with default configuration
- **THEN** no background task deletes rows from `request_logs`
- **AND** no CLI dry-run command deletes rows from `request_logs`

### Requirement: Retention keeps a raw safety window

Request-log retention SHALL default to and enforce a minimum raw retention
window of 7 days. Operators MAY configure a longer raw retention window. The
service MUST reject or refuse to apply a retention window below the minimum.

#### Scenario: Too-short retention is rejected

- **WHEN** an operator requests request-log pruning with a raw retention window below 7 days
- **THEN** the command fails before deleting raw rows

#### Scenario: Recent rows remain available

- **GIVEN** raw request logs exist inside the configured raw retention window
- **WHEN** request-log retention runs in apply mode
- **THEN** those recent raw rows remain in `request_logs`

### Requirement: Old raw logs are aggregated before deletion

Before deleting raw `request_logs` rows outside the raw retention window, the
service SHALL aggregate eligible rows into durable daily request-log aggregate
rows. The aggregate key MUST include the UTC day, API key id, account id,
model, status, error code, request kind, service tier fields, transport fields,
source, user-agent group, plan type, and deleted-state bucket. Aggregate values
MUST include request count, token totals, cost total, and latency count/sum
fields needed to preserve historical totals. The rollup MUST also preserve
effective output tokens using the raw API-key/account fallback semantics, exact
per-row microdollar cost sums, and a latest-row account projection that
preserves exact duplicate suppression.

#### Scenario: Apply mode preserves old totals

- **GIVEN** raw request logs older than the configured raw retention window
- **WHEN** retention runs in apply mode
- **THEN** matching daily aggregate rows are upserted before raw rows are deleted
- **AND** summed aggregate totals match the raw rows that were deleted

#### Scenario: Apply mode fails closed on row-count mismatch

- **GIVEN** apply mode grouped a set of eligible raw request logs
- **WHEN** the raw-row deletion count differs from the grouped request count
- **THEN** the retention transaction fails before commit
- **AND** neither aggregate mutations nor raw-row deletions are persisted

#### Scenario: Lifetime projections survive pruning edge cases

- **GIVEN** eligible raw rows include a null output-token row with reasoning tokens
- **AND** eligible raw rows include exact duplicate account request identities
- **WHEN** retention runs in apply mode
- **THEN** API-key lifetime totals retain the reasoning-token fallback
- **AND** account lifetime totals retain latest-row duplicate suppression
- **AND** report totals continue to include every raw request-log row

#### Scenario: Dry-run mode reports without mutation

- **GIVEN** raw request logs older than the configured raw retention window
- **WHEN** retention runs in dry-run mode
- **THEN** the command reports eligible row counts and aggregate groups
- **AND** no aggregate rows are written
- **AND** no raw rows are deleted

### Requirement: Continuity lookups are not served from aggregates

The service SHALL continue to resolve `previous_response_id` ownership from raw
`request_logs` rows only. Aggregate rows MUST NOT be used to route hard
continuity requests.

#### Scenario: Old aggregate does not authorize owner routing

- **GIVEN** a raw request log has been pruned after being rolled into a daily aggregate
- **WHEN** a later request tries to resolve that pruned `previous_response_id`
- **THEN** the continuity lookup does not use the aggregate row as owner proof

### Requirement: Reports read aggregate history after pruning

Report summary, daily, model, account, active-account, and comparison coverage
queries SHALL combine recent raw `request_logs` rows with older
`request_log_daily_aggregates` rows so pruning raw history does not remove
historical report totals.

#### Scenario: Pruned report history remains visible

- **GIVEN** old raw request logs have been rolled into daily aggregate rows and deleted
- **AND** newer raw request logs remain in `request_logs`
- **WHEN** an operator views reports over a range spanning both periods
- **THEN** report totals include both the aggregate rows and the raw rows
- **AND** model and account breakdowns include the aggregate rows
- **AND** comparison coverage can use aggregate history as evidence of prior activity

#### Scenario: User-agent report filters include aggregate history

- **GIVEN** old raw request logs have been rolled up with user-agent groups
- **WHEN** an operator filters any report aggregate by user-agent group
- **THEN** matching raw and aggregate rows are combined
- **AND** non-matching aggregate rows are excluded

### Requirement: Dashboard activity reads aggregate history after pruning

Dashboard activity summaries, daily trend buckets, error summaries,
earliest-activity coverage, and prior-window comparisons SHALL combine raw
request logs with complete UTC-day aggregate buckets when the selected
dashboard window extends beyond raw retention.

#### Scenario: Thirty-day dashboard spans seven-day raw retention

- **GIVEN** raw request logs retain seven days and older complete UTC days are aggregated
- **WHEN** an operator views the thirty-day dashboard overview
- **THEN** daily trends and activity totals include both recent raw rows and older rollups
- **AND** the earliest-activity and top-error calculations include rollup history

### Requirement: API-key limit backfill reads aggregate history conservatively

API-key usage reconstruction SHALL combine raw rows with aggregate rows for
successful, non-warmup traffic. Aggregate cost usage MUST use the stored sum of
per-request floored microdollars. Because daily rollups cannot reconstruct
partial UTC days, any rollup day that overlaps the requested limit interval
SHALL be included conservatively.

#### Scenario: Long-window limit spans pruned history

- **GIVEN** an API-key limit window starts before the raw retention boundary
- **WHEN** usage is reconstructed for admission control
- **THEN** matching successful raw and aggregate usage is combined
- **AND** effective output-token fallback and per-request microdollar semantics are preserved
- **AND** a partial boundary day is counted as a whole rollup day rather than undercounted

### Requirement: Dashboard lifetime usage reads aggregate history after pruning

Dashboard lifetime usage summaries that intentionally span all retained history
SHALL combine recent raw `request_logs` rows with older
`request_log_daily_aggregates` rows. API-key lifetime usage summaries MUST
include aggregate rows for the same API key. Account request-usage summaries
MUST include aggregate rows for the same account only when the aggregate row is
not marked deleted.

#### Scenario: API-key lifetime usage includes aggregate history

- **GIVEN** old API-key request logs have been rolled into daily aggregate rows and deleted
- **AND** newer raw request logs remain for the same API key
- **WHEN** an operator views API-key lifetime usage
- **THEN** the request, token, cached-token, and cost totals include both the aggregate rows and the raw rows
- **AND** warmup aggregate rows are excluded from the usage totals

#### Scenario: Account request usage includes non-deleted aggregate history

- **GIVEN** old account request logs have been rolled into daily aggregate rows and deleted
- **AND** newer raw request logs remain for the same account
- **WHEN** an operator views account request usage
- **THEN** the request, token, cached-token, and cost totals include both the non-deleted aggregate rows and the raw rows
- **AND** aggregate rows marked deleted or warmup are excluded from account request usage

### Requirement: Account deletion applies to aggregate history

Account deletion SHALL apply the same history semantics to
`request_log_daily_aggregates` rows as it applies to raw `request_logs` rows.
When account history is preserved, aggregate rows for the deleted account MUST
be disassociated from the account and marked deleted. When account history is
hard-deleted, aggregate rows for that account MUST be deleted.

#### Scenario: Soft delete preserves aggregate totals without account attribution

- **GIVEN** aggregate rows exist for an account
- **WHEN** the account is deleted without deleting history
- **THEN** those aggregate rows remain
- **AND** their account id is cleared
- **AND** they are marked deleted

#### Scenario: Hard delete removes aggregate rows

- **GIVEN** aggregate rows exist for an account
- **WHEN** the account is deleted with history deletion enabled
- **THEN** those aggregate rows are deleted

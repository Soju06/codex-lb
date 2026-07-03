## ADDED Requirements

### Requirement: Request-log retention is apply-only by default

The service SHALL NOT delete raw `request_logs` rows unless an operator invokes
an apply-mode retention command.

#### Scenario: Default deployment preserves raw logs

- **WHEN** the service starts with default configuration
- **THEN** no background task deletes rows from `request_logs`
- **AND** no CLI dry-run command deletes rows from `request_logs`

### Requirement: Retention keeps a raw safety window

Request-log retention SHALL enforce a minimum raw retention window of 14 days.
Operators MAY configure a longer raw retention window. The service MUST reject
or refuse to apply a retention window below the minimum.

#### Scenario: Too-short retention is rejected

- **WHEN** an operator requests request-log pruning with a raw retention window below 14 days
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
fields needed to preserve historical totals.

#### Scenario: Apply mode preserves old totals

- **GIVEN** raw request logs older than the configured raw retention window
- **WHEN** retention runs in apply mode
- **THEN** matching daily aggregate rows are upserted before raw rows are deleted
- **AND** summed aggregate totals match the raw rows that were deleted

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

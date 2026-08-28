## MODIFIED Requirements

### Requirement: Request-log pruning never deletes unfolded rows

Request-log pruning MUST gate on every usage-rollup watermark — the lifetime `folded_through`, the time-axis `hourly_folded_through`, and the conversation satellite's `conversation_folded_through` — combined as their minimum. Pruning MUST run only while the combined fold is current (the minimum watermark within two fold lags of now) and MUST delete only rows with `requested_at` older than the retention cutoff AND at least one fold lag below the minimum watermark, so concurrent summary readers holding a slightly older watermark can never lose rows from a just-folded window and no rollup is ever robbed of raw it has not folded. When no rollup watermark exists, or any fold is catching up (initial backfill, stalled scheduler), request-log pruning MUST be skipped.

#### Scenario: Unfolded rows survive pruning

- **GIVEN** a request-log row older than the retention cutoff whose `requested_at` is above any fold watermark
- **WHEN** the retention job runs
- **THEN** the row MUST NOT be deleted

#### Scenario: Stalled fold suspends pruning

- **GIVEN** any fold watermark older than two fold lags
- **WHEN** the retention job runs with request-log retention enabled
- **THEN** no `request_logs` rows are deleted

#### Scenario: Conversation backfill suspends pruning

- **GIVEN** a deployment upgraded with existing history, where the lifetime and hourly watermarks are current but `conversation_folded_through` is still at or near the epoch
- **WHEN** the retention job runs with request-log retention enabled
- **THEN** no `request_logs` rows are deleted until the conversation backfill watermark becomes current

#### Scenario: Lifetime totals are unchanged by pruning

- **GIVEN** folded request-log rows older than the retention cutoff
- **WHEN** the retention job deletes them and account usage summaries are read afterwards
- **THEN** per-account lifetime totals MUST equal their pre-pruning values

#### Scenario: Conversation statistics are unchanged by pruning

- **GIVEN** request-log rows folded into the conversation presence satellite and older than the retention cutoff
- **WHEN** the retention job deletes them
- **THEN** the switched distinct-conversation reads over the pruned period MUST equal their pre-pruning values

#### Scenario: Pruning is skipped before the first fold

- **GIVEN** no `account_usage_rollup_state` row exists
- **WHEN** the retention job runs with request-log retention enabled
- **THEN** no `request_logs` rows are deleted

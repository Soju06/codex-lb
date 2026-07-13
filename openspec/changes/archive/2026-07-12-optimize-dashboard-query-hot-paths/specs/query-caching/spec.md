## MODIFIED Requirements

### Requirement: Hot-path quota and dashboard aggregate reads avoid window-ranking scans
Selector and dashboard hot-path reads MUST avoid unbounded SQL window-ranking over `additional_usage_history` and `request_logs`; they MUST preserve existing result semantics while using grouped latest-id or `DISTINCT ON` shapes plus supporting indexes.

#### Scenario: Additional quota latest lookup avoids window ranking
- **GIVEN** multiple additional quota rows exist for each account under the same quota key and window
- **WHEN** gated-model selection loads the latest additional quota rows for candidate accounts
- **THEN** the query MUST NOT use `row_number()` or another full partition window-ranking expression
- **AND** the hot-path lookup MUST constrain by canonical `quota_key`, `window`, and candidate account ids so the latest-row index remains usable
- **AND** the selected row per account MUST remain the newest `recorded_at`, then highest `used_percent`, then highest `id`

#### Scenario: Account request usage summary avoids request-log window ranking
- **GIVEN** dashboard account summaries aggregate request log usage per account
- **WHEN** account request usage summaries are loaded
- **THEN** the query MUST NOT rank the full `request_logs` set with `row_number()`
- **AND** duplicate request-log rows for the same account, request id, and requested timestamp MUST still collapse to the latest row id before aggregation

#### Scenario: Hot-path indexes are idempotent
- **GIVEN** a production database may already have manually-created hot-path indexes
- **WHEN** the schema migration for dashboard query hot paths is applied
- **THEN** the migration MUST complete without duplicate-index failure

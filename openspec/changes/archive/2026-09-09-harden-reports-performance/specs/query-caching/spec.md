## MODIFIED Requirements

### Requirement: Distinct-conversation reads combine the presence rollup with a raw live tail in one statement

The dashboard conversation activity metrics (`conversation_count`, `conversation_request_count`) and hour-multiple conversation trend buckets MUST serve folded history from the conversation presence satellite and the remainder from raw `request_logs` in one statement. The watermark and both UNION branches MUST share a database snapshot, and `COUNT(DISTINCT ...)` MUST deduplicate across the fold boundary. Missing or epoch watermarks MUST degrade to raw reads. Non-hour-multiple dashboard buckets MUST keep the full-raw path.

Reports summary and daily conversation counts, including account, API-key, model and User-Agent filtered reads, MUST use the permanent report history source specified by `report-aggregation`. Their distinct counts and daily row membership MUST survive raw retention after the report fold has covered the period, subject to the documented partial-hour edge limitation.

#### Scenario: Switched conversation reads equal legacy reads while raw exists

- **GIVEN** a corpus with conversations spanning hours, blank and NULL conversation ids, warmup kinds, and soft-deleted rows
- **WHEN** each switched conversation read runs with the conversation watermark at epoch, mid-history on an hour boundary, and at the fold target — including states where the hourly and conversation watermarks differ
- **THEN** every result equals the legacy raw-only implementation exactly

#### Scenario: Conversation statistics survive raw pruning

- **GIVEN** folded conversation presence whose source raw rows have been pruned by retention
- **WHEN** the dashboard conversation activity metrics or hour-multiple conversation trend buckets are read over that period
- **THEN** the distinct-conversation values equal those reported before the pruning (modulo the documented sub-bucket window edges)

#### Scenario: Filtered reports reads stay raw-bound

- **GIVEN** a filtered report and a missing or epoch report-fold watermark
- **WHEN** the read executes
- **THEN** it uses only raw rows until the report fold has covered history

#### Scenario: Filtered reports preserve folded history

- **GIVEN** a report filtered by account, API key, model or User-Agent and a completed report fold
- **WHEN** source raw rows are pruned
- **THEN** report totals, distinct conversations and daily buckets remain available from the report history source

#### Scenario: Non-hour-multiple conversation buckets degrade to full raw

- **GIVEN** a conversation trend request with a display bucket that is not a whole multiple of the rollup hour
- **WHEN** the aggregate is calculated
- **THEN** the legacy full-raw query is used unchanged

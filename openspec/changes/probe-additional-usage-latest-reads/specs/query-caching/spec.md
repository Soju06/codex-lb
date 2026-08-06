# query-caching Delta

## MODIFIED Requirements

### Requirement: Hot-path quota and dashboard aggregate reads avoid window-ranking scans
Selector and dashboard hot-path reads MUST avoid unbounded SQL window-ranking over `additional_usage_history` and `request_logs`; they MUST preserve existing result semantics. On PostgreSQL the additional-usage latest-per-account read MUST be served by correlated per-account top-1 index probes whose cost scales with the candidate account count and registry match values, not with the number of history rows stored under the quota key; grouped latest-id shapes remain acceptable for other reads and backends.

#### Scenario: Additional quota latest lookup avoids window ranking
- **GIVEN** multiple additional quota rows exist for each account under the same quota key and window
- **WHEN** gated-model selection loads the latest additional quota rows for candidate accounts
- **THEN** the query MUST NOT use `row_number()` or another full partition window-ranking expression
- **AND** on PostgreSQL the lookup MUST resolve each account through a top-1 probe ordered by `recorded_at DESC, used_percent DESC, id DESC` under an equality prefix on the match value, `window`, and account id
- **AND** the selected row per account MUST remain the newest `recorded_at`, then highest `used_percent`, then highest `id`

#### Scenario: Alias matches merge without a second history scan
- **GIVEN** the additional-quota registry declares `limit_name` or `metered_feature` aliases for a canonical quota key
- **AND** history rows exist that match only through an alias
- **WHEN** the latest additional quota rows are loaded for candidate accounts on PostgreSQL
- **THEN** alias matches MUST be resolved through per-account top-1 probes over expression indexes on the lowercased alias columns
- **AND** the merged winner per account MUST equal the newest row across canonical and alias matches under the `recorded_at DESC, used_percent DESC, id DESC` ordering

#### Scenario: Account request usage summary avoids request-log window ranking
- **GIVEN** dashboard account summaries aggregate request log usage per account
- **WHEN** account request usage summaries are loaded
- **THEN** the query MUST NOT rank the full `request_logs` set with `row_number()`
- **AND** duplicate request-log rows for the same account, request id, and requested timestamp MUST still collapse to the latest row id before aggregation

#### Scenario: Unfiltered distinct label listing avoids a full history pass
- **GIVEN** additional-usage history holds many rows spread over a small set of distinct `(quota_key, limit_name, metered_feature)` labels
- **WHEN** the distinct label listing is requested on PostgreSQL without a recency bound
- **THEN** the read MUST iterate distinct `(account_id, quota_key, limit_name, metered_feature)` tuples via ordered index probes instead of scanning every history row
- **AND** the canonicalized result set MUST equal the plain `DISTINCT` read

#### Scenario: Hot-path indexes are idempotent
- **GIVEN** a production database may already have manually-created hot-path indexes
- **WHEN** the schema migration for dashboard query hot paths is applied
- **THEN** the migration MUST complete without duplicate-index failure

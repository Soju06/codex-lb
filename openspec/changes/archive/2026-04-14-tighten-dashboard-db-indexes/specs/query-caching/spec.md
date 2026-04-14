## MODIFIED Requirements

### Requirement: latest_by_account 쿼리 효율화
`usage_history` latest-row lookups MUST remain DB-index friendly on supported backends instead of falling back to planner-hostile full scans and external sorts.

#### Scenario: PostgreSQL latest-row query uses the normalized latest index
- **WHEN** `latest_by_account("primary")` or `latest_by_account("secondary")` runs on PostgreSQL
- **THEN** the query uses a backend-specific latest-row shape that preserves the existing result contract (SHALL)
- **AND** it remains compatible with the normalized composite latest index on `coalesce(window, 'primary'), account_id, recorded_at DESC, id DESC` (SHALL)

### Requirement: Dashboard request-log aggregations have matching composite indexes
Dashboard request-log aggregation and filter-option queries MUST have composite indexes that match their dominant time-range and facet dimensions.

#### Scenario: Recent request-log aggregation has a matching composite index
- **WHEN** dashboard overview aggregates recent `request_logs` by bucket, model, and service tier
- **THEN** the schema provides a composite index covering the recent-time predicate plus `model` and `service_tier` dimensions (SHALL)

#### Scenario: Request-log facet queries have matching composite indexes
- **WHEN** dashboard request-log filters load recent model/reasoning-effort or status/error-code options
- **THEN** the schema provides composite indexes that match those facet dimensions with latest-first ordering (SHALL)

### Requirement: Usage history time-range reads have a normalized window composite index
Dashboard and usage-history time-range reads MUST have a normalized window composite index so primary-window `NULL` rows and explicit `"primary"` rows remain queryable through the same indexed predicate.

#### Scenario: Bulk history reads use normalized window + account + time index
- **WHEN** usage history is fetched for multiple accounts since a cutoff under a logical window
- **THEN** the schema provides a composite index on normalized window, account, and recorded time that is compatible with that predicate (SHALL)

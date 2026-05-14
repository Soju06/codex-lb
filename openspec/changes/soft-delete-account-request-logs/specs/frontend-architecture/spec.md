## ADDED Requirements

### Requirement: Deleted-account request logs are hidden from the request-log table
When an account is deleted, request-log rows that were associated with that account MUST be preserved as soft-deleted request logs. The dashboard request-log list API and its filter options MUST exclude soft-deleted request logs.

#### Scenario: Deleted account rows do not appear in recent requests
- **WHEN** an account with existing request logs is deleted
- **THEN** those request-log rows remain persisted and marked as deleted
- **AND** `GET /api/request-logs` does not include those rows
- **AND** `GET /api/request-logs/options` does not include facets derived only from those rows

### Requirement: Deleted-account request logs keep contributing to dashboard metrics
Soft-deleted request-log rows MUST continue to contribute to dashboard metrics, trends, costs, error rates, and aggregate usage totals.

#### Scenario: Metrics include deleted-account request history
- **WHEN** an account with request logs is deleted
- **THEN** dashboard metric calculations for the same time range still include those request logs

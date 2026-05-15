### Requirement: Deleted-account request logs are soft-hidden from dashboard lists
When an account is deleted, request-log rows that were associated with that account MUST be preserved as soft-deleted request logs. The dashboard request-log list API and its filter options MUST exclude soft-deleted request logs.

#### Scenario: Deleted account request logs stay persisted but hidden from list
- **WHEN** an account with existing request logs is deleted
- **THEN** those request-log rows remain persisted and marked as deleted
- **AND** `GET /api/request-logs` does not return those rows
- **AND** `GET /api/request-logs/options` does not include those rows in account, model, API key, or status facets

### Requirement: Metrics continue to include deleted-account request history
Soft-deleted request-log rows MUST continue to contribute to dashboard metrics, trends, costs, error rates, and aggregate usage totals.

#### Scenario: Metrics include deleted-account request history
- **WHEN** an account with request logs is deleted
- **THEN** dashboard metrics and request aggregates still include that request history

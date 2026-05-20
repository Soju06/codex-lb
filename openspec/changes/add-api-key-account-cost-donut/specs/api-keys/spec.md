## ADDED Requirements

### Requirement: API key 7-day usage includes account cost breakdown

`GET /api/api-keys/{key_id}/usage-7d` SHALL return `accountCosts[]` in addition to the existing 7-day totals for the selected API key. Each `accountCosts[]` item SHALL include `accountId`, `email`, `costUsd`, and `isDeleted`.

The system MUST aggregate `accountCosts[]` from request-log rows whose `api_key_id` matches the selected key and whose `requested_at` falls inside the rolling 7-day window used by the endpoint totals.

#### Scenario: Account costs are sorted by descending cost
- **WHEN** a client loads `GET /api/api-keys/{key_id}/usage-7d`
- **AND** multiple grouped account-cost buckets exist in the 7-day window
- **THEN** `accountCosts[]` is ordered by `costUsd` descending

#### Scenario: Unknown account usage remains separate
- **WHEN** request-log rows in the 7-day window have `account_id = NULL`
- **AND** those rows are not soft-deleted
- **THEN** the response includes an `accountCosts[]` item with `accountId: null`, `email: null`, and `isDeleted: false`

#### Scenario: Deleted account usage is grouped into one bucket
- **WHEN** request-log rows in the 7-day window are marked deleted
- **THEN** the response groups their cost into a synthetic `accountCosts[]` item with `accountId: null`, `email: null`, and `isDeleted: true`

#### Scenario: Deleted and unknown account usage stay distinct
- **WHEN** the same API key has both soft-deleted request-log cost and unknown non-deleted request-log cost inside the 7-day window
- **THEN** the response returns separate `accountCosts[]` items for the deleted and non-deleted buckets

### Requirement: API key 7-day account-cost queries use a composite request-log index

The database SHALL provide an index that supports filtering request logs by API key and 7-day requested-at range before grouping by account for the API-key account-cost breakdown.

#### Scenario: Composite account-cost index exists after migration
- **WHEN** database migrations are applied
- **THEN** the `request_logs` table includes an index covering `api_key_id`, descending `requested_at`, and `account_id`

## MODIFIED Requirements

### Requirement: API key 7-day account-cost queries use a composite request-log index

The database SHALL provide an index that supports filtering request logs by API key and 7-day requested-at range for the API-key account-cost breakdown. The filter columns (`api_key_id`, `requested_at`) MUST be the leading key columns of a maintained index; the `account_id` grouping column MAY be fetched from the heap, since the per-key 7-day window bounds the row count and production plan evidence showed the wider `(api_key_id, requested_at, account_id)` variant was never selected by the planner (`pg_stat_user_indexes.idx_scan = 0`).

#### Scenario: Account-cost filter is index-supported after migration

- **WHEN** database migrations are applied
- **THEN** the `request_logs` table includes an index whose leading key columns are `api_key_id` and descending `requested_at`
- **AND** the 7-day account-cost breakdown query for an API key is satisfiable by that index for its filter phase

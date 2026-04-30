## MODIFIED Requirements

### Requirement: API Key update
The system SHALL allow updating key properties via `PATCH /api/api-keys/{id}`. Updatable fields: `name`, `allowedModels`, `weeklyTokenLimit`, `expiresAt`, `isActive`. The key hash and prefix MUST NOT be modifiable. The system MUST accept timezone-aware ISO 8601 datetimes for `expiresAt` and normalize them to UTC naive before persistence.

When a submitted API key limit rule does not match an existing rule by `limit_type`, `limit_window`, and `model_filter`, the system MUST initialize the new rule's `current_value` from the API key's successful existing request-log usage in that rule's current window. If `resetUsage` is true, the system MUST initialize submitted limits with `current_value: 0`.

#### Scenario: Update key with timezone-aware expiration
- **WHEN** admin submits `PATCH /api/api-keys/{id}` with `{ "expiresAt": "2025-12-31T00:00:00Z" }`
- **THEN** the system persists the expiration successfully without PostgreSQL datetime binding errors
- **AND** the response returns `expiresAt` representing the same UTC instant

#### Scenario: Update non-existent key

- **WHEN** admin submits `PATCH /api/api-keys/{id}` with an unknown ID
- **THEN** the system returns 404

#### Scenario: Add token limit after current-window usage exists

- **WHEN** an API key has successful request-log token usage in the active daily window
- **AND** the API key has error or incomplete request-log token usage in the same window
- **AND** admin submits `PATCH /api/api-keys/{id}` adding a daily `total_tokens` limit without `resetUsage`
- **THEN** the new limit's `current_value` includes only the successful current-window token usage

#### Scenario: Reset usage when adding a limit

- **WHEN** an API key has request-log usage in the active window
- **AND** admin submits `PATCH /api/api-keys/{id}` adding a limit with `resetUsage: true`
- **THEN** the new limit's `current_value` is `0`

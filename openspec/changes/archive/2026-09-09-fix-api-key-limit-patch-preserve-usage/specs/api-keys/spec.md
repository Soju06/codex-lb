## MODIFIED Requirements

### Requirement: API Key update
The system SHALL allow updating key properties via `PATCH /api/api-keys/{id}`. Updatable fields: `name`, `allowedModels`, `weeklyTokenLimit`, `expiresAt`, `isActive`, `usageSections`, `transportPolicyOverride`. The key hash and prefix MUST NOT be modifiable. The system MUST accept timezone-aware ISO 8601 datetimes for `expiresAt` and normalize them to UTC naive before persistence. The `transportPolicyOverride` field MUST accept `null` (follow the global policy) or one of `"smart"`, `"always_http"`, `"always_websocket"`; any other value MUST be rejected with HTTP 400.

When a submitted API key limit rule matches an existing rule by `limit_type`, `limit_window`, and `model_filter`, updating the rule's maximum MUST preserve the latest committed `current_value` and `reset_at` unless `resetUsage` is true. A transient SQLite lock or snapshot conflict during the update MUST roll back and retry the complete read/build/write transaction, including rereading existing limits, before returning an error.

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

#### Scenario: Add cost limit after current-window usage exists

- **WHEN** an API key has successful request-log costs in the active daily window
- **AND** admin submits `PATCH /api/api-keys/{id}` adding a daily `cost_usd` limit without `resetUsage`
- **THEN** the new limit's `current_value` is the sum of each successful request log's `cost_usd` converted to truncated integer microdollars

#### Scenario: Reset usage when adding a limit

- **WHEN** an API key has request-log usage in the active window
- **AND** admin submits `PATCH /api/api-keys/{id}` adding a limit with `resetUsage: true`
- **THEN** the new limit's `current_value` is `0`

#### Scenario: Update key transport policy override

- **WHEN** admin submits `PATCH /api/api-keys/{id}` with `{ "transportPolicyOverride": "always_http" }`
- **THEN** the system persists the override and returns `transportPolicyOverride = "always_http"`

#### Scenario: Clear key transport policy override

- **WHEN** admin submits `PATCH /api/api-keys/{id}` with `{ "transportPolicyOverride": null }`
- **THEN** the system clears the override and the key follows the global `http_downstream_transport_policy`

#### Scenario: Reject invalid transport policy override

- **WHEN** admin submits `PATCH /api/api-keys/{id}` with `{ "transportPolicyOverride": "carrier-pigeon" }`
- **THEN** the system returns 400 and does not modify the key

#### Scenario: Preserve matched limit usage during PATCH

- **GIVEN** an API key has a matched limit with committed `current_value` and `reset_at`
- **WHEN** admin submits a PATCH that changes the matched limit's maximum without `resetUsage`
- **THEN** the limit maximum is updated
- **AND** the latest committed `current_value` and `reset_at` remain unchanged

#### Scenario: Retry API-key PATCH after a transient SQLite snapshot conflict

- **GIVEN** a concurrent reservation or lazy reset commits after the PATCH's initial read
- **WHEN** the PATCH write encounters a transient SQLite lock or snapshot conflict
- **THEN** the transaction is rolled back
- **AND** the PATCH rereads current state and retries the complete update
- **AND** the PATCH succeeds without clearing the concurrent usage state

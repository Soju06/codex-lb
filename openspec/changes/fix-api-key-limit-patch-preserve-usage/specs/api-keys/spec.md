## MODIFIED Requirements

### Requirement: API Key update

The system SHALL allow updating key properties via `PATCH /api/api-keys/{id}`. Updatable fields: `name`, `allowedModels`, `weeklyTokenLimit`, `expiresAt`, `isActive`, `usageSections`, `transportPolicyOverride`; `resetUsage` is a supported non-persisted control option for submitted limits. The key hash and prefix MUST NOT be modifiable. The system MUST accept timezone-aware ISO 8601 datetimes for `expiresAt` and normalize them to UTC naive before persistence. The `transportPolicyOverride` field MUST accept `null` (follow the global policy) or one of `"smart"`, `"always_http"`, `"always_websocket"`; any other value MUST be rejected with HTTP 400. When `resetUsage` is true, submitted limits MUST be initialized with `current_value: 0`.

When a submitted API key limit rule matches an existing rule by `limit_type`, `limit_window`, and `model_filter`, updating the rule's maximum MUST preserve the latest committed `current_value` and `reset_at` unless `resetUsage` is true. A transient SQLite lock or snapshot conflict during the update MUST roll back and retry the complete read/build/write transaction, including rereading existing limits, before returning an error.

When a submitted API key limit rule does not match an existing rule by `limit_type`, `limit_window`, and `model_filter`, the system MUST initialize the new rule's `current_value` from the API key's successful existing request-log usage in that rule's current window. If `resetUsage` is true, the system MUST initialize submitted limits with `current_value: 0`.

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

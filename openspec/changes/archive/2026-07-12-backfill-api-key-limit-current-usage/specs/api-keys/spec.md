## MODIFIED Requirements

### Requirement: Limit update with usage state preservation
When updating API key limits, the system SHALL preserve existing usage state (`current_value`, `reset_at`) for unchanged limit rules. Limit comparison key is `(limit_type, limit_window, model_filter)`.

- Matching existing rule: `current_value` and `reset_at` SHALL be preserved; only `max_value` is updated
- New rule (no match) without `resetUsage`: `current_value` SHALL be initialized from the API key's successful existing request-log usage in the new rule's current window, with a fresh `reset_at`
- New rule (no match) with `resetUsage`: `current_value=0` and fresh `reset_at`
- Removed rule (in existing but not in update): row is deleted

Usage reset SHALL only occur via an explicit action (`resetUsage` field or dedicated endpoint), never as a side-effect of metadata or policy edits.

#### Scenario: Metadata-only edit preserves usage state

- **WHEN** an API key PATCH updates only name or is_active
- **AND** `limits` field is not included in the payload
- **THEN** existing `current_value` and `reset_at` are unchanged

#### Scenario: Same policy re-submission preserves usage state

- **WHEN** an API key PATCH includes `limits` with identical rules (same type/window/filter/max_value)
- **THEN** existing `current_value` and `reset_at` are unchanged

#### Scenario: max_value adjustment preserves counters

- **WHEN** an API key PATCH changes only `max_value` for an existing matched limit rule
- **THEN** that rule's existing `current_value` and `reset_at` are unchanged

#### Scenario: Adding a new limit backfills current-window usage

- **WHEN** an API key has successful request-log usage in the active window
- **AND** an API key PATCH adds a limit rule that does not match any existing rule
- **AND** `resetUsage` is not true
- **THEN** the new rule's `current_value` reflects successful existing request-log usage for that rule's current window
- **AND** the new rule receives a fresh `reset_at`

#### Scenario: resetUsage keeps new limits at zero

- **WHEN** an API key has request-log usage in the active window
- **AND** an API key PATCH adds a limit rule that does not match any existing rule
- **AND** `resetUsage` is true
- **THEN** the new rule's `current_value` is `0`
- **AND** the new rule receives a fresh `reset_at`

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

#### Scenario: Add cost limit after current-window usage exists

- **WHEN** an API key has successful request-log costs in the active daily window
- **AND** admin submits `PATCH /api/api-keys/{id}` adding a daily `cost_usd` limit without `resetUsage`
- **THEN** the new limit's `current_value` is the sum of each successful request log's `cost_usd` converted to truncated integer microdollars

#### Scenario: Reset usage when adding a limit

- **WHEN** an API key has request-log usage in the active window
- **AND** admin submits `PATCH /api/api-keys/{id}` adding a limit with `resetUsage: true`
- **THEN** the new limit's `current_value` is `0`

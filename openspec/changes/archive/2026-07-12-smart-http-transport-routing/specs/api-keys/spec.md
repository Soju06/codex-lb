# api-keys — Per-key transport policy override (delta)

## MODIFIED Requirements

### Requirement: API Key update
The system SHALL allow updating key properties via `PATCH /api/api-keys/{id}`. Updatable fields: `name`, `allowedModels`, `weeklyTokenLimit`, `expiresAt`, `isActive`, `transportPolicyOverride`. The key hash and prefix MUST NOT be modifiable. The system MUST accept timezone-aware ISO 8601 datetimes for `expiresAt` and normalize them to UTC naive before persistence. The `transportPolicyOverride` field MUST accept `null` (follow the global policy) or one of `"smart"`, `"always_http"`, `"always_websocket"`; any other value MUST be rejected with HTTP 400.

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

## ADDED Requirements

### Requirement: Per-key transport policy override

Each API key record MUST carry an optional `transport_policy_override`
field (nullable, default `null`). When non-null, its value MUST be one of
`"smart"`, `"always_http"`, or `"always_websocket"`, and it MUST be used
as the effective downstream-HTTP transport routing policy for requests
authenticated by that key, taking precedence over the global
`http_downstream_transport_policy`. When `null`, requests authenticated
by the key MUST follow the global policy.

The field MUST be settable on creation (`POST /api/api-keys`, optional)
and on update (`PATCH /api/api-keys/{id}`), MUST be returned on key reads
as `transportPolicyOverride`, and MUST be persisted via an additive
nullable column. Existing rows MUST default to `null` (follow global) so
the migration is backward compatible with no behavior change for keys
that never set an override.

#### Scenario: Create key with transport policy override

- **WHEN** admin submits `POST /api/api-keys` with `{ "name": "graphiti", "transportPolicyOverride": "always_http" }`
- **THEN** the created key returns `transportPolicyOverride = "always_http"`

#### Scenario: Create key without override defaults to null

- **WHEN** admin submits `POST /api/api-keys` without `transportPolicyOverride`
- **THEN** the created key returns `transportPolicyOverride = null`
- **AND** the key follows the global `http_downstream_transport_policy`

#### Scenario: Existing keys migrate to null override

- **GIVEN** API key rows created before this change
- **WHEN** the additive migration runs
- **THEN** every existing row has `transport_policy_override = null`
- **AND** those keys follow the global policy with no behavior change

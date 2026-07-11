## ADDED Requirements

### Requirement: API key usage_sections controls visible /v1/usage detail sections

The system SHALL accept an optional `usage_sections` field in `POST /api/api-keys` and `PATCH /api/api-keys/{id}`. The field SHALL be a comma-separated string of section names. Supported values SHALL be `upstream_limits` and `account_pool_usage`. When `usage_sections` is omitted during creation, the system SHALL default it to `"upstream_limits,account_pool_usage"`.

The `ApiKeyResponse` SHALL include `usage_sections` as a string.

#### Scenario: Create key with explicit usage_sections

- **WHEN** admin submits `POST /api/api-keys` with `{ "name": "dev-key", "usageSections": "upstream_limits" }`
- **THEN** the created key returns `usageSections: "upstream_limits"`

#### Scenario: Create key without usage_sections defaults to all

- **WHEN** admin submits `POST /api/api-keys` without `usageSections`
- **THEN** the created key returns `usageSections: "upstream_limits,account_pool_usage"`

#### Scenario: Update key usage_sections

- **WHEN** admin submits `PATCH /api/api-keys/{id}` with `{ "usageSections": "account_pool_usage" }`
- **THEN** the key returns `usageSections: "account_pool_usage"`

#### Scenario: Reject unknown usage_sections values

- **WHEN** admin submits `POST /api/api-keys` with `usageSections` containing an unsupported value
- **THEN** the system returns 400

## MODIFIED Requirements

### Requirement: API Key creation

The system SHALL allow the admin to create API keys via `POST /api/api-keys` with a `name` (required), `allowed_models` (optional list), `weekly_token_limit` (optional integer), `expires_at` (optional ISO 8601 datetime), `assigned_account_ids` (optional list), and `usage_sections` (optional comma-separated string, defaults to `"upstream_limits,account_pool_usage"`). The system MUST generate a key in the format `sk-clb-{48 hex chars}`, store only the `sha256` hash in the database, and return the plain key exactly once in the creation response. The system MUST accept timezone-aware ISO 8601 datetimes for `expiresAt`, normalize them to UTC naive for persistence, and return the expiration as UTC in API responses.

When `assigned_account_ids` is omitted or empty, the created key SHALL remain unscoped and apply to all accounts. When `assigned_account_ids` is provided with one or more valid account IDs, the created key SHALL enable account-assignment scope and persist those assignments.

#### Scenario: Create unscoped key without assigned accounts

- **WHEN** admin submits `POST /api/api-keys` without `assignedAccountIds`
- **THEN** the created key returns `accountAssignmentScopeEnabled = false`
- **AND** `assignedAccountIds = []`

#### Scenario: Create scoped key with assigned accounts

- **WHEN** admin submits `POST /api/api-keys` with `assignedAccountIds` containing valid account IDs
- **THEN** the created key returns `accountAssignmentScopeEnabled = true`
- **AND** `assignedAccountIds` matches the supplied accounts

#### Scenario: Reject unknown assigned account IDs on create

- **WHEN** admin submits `POST /api/api-keys` with an unknown account ID in `assignedAccountIds`
- **THEN** the system returns 400

#### Scenario: Create key and show plain key

- **WHEN** admin submits `POST /api/api-keys` with a valid payload
- **THEN** the response contains the full plain key exactly once and the system never returns the plain key on subsequent reads

#### Scenario: Create key with timezone-aware expiration

- **WHEN** admin submits `POST /api/api-keys` with `{ "name": "dev-key", "expiresAt": "2025-12-31T00:00:00Z" }`
- **THEN** the system persists the expiration successfully without PostgreSQL datetime binding errors
- **AND** the response returns `expiresAt` representing the same UTC instant

### Requirement: API Key update
The system SHALL allow updating key properties via `PATCH /api/api-keys/{id}`. Updatable fields: `name`, `allowedModels`, `weeklyTokenLimit`, `expiresAt`, `isActive`, `usageSections`. The key hash and prefix MUST NOT be modifiable. The system MUST accept timezone-aware ISO 8601 datetimes for `expiresAt` and normalize them to UTC naive before persistence.

#### Scenario: Update key with timezone-aware expiration
- **WHEN** admin submits `PATCH /api/api-keys/{id}` with `{ "expiresAt": "2025-12-31T00:00:00Z" }`
- **THEN** the system persists the expiration successfully without PostgreSQL datetime binding errors
- **AND** the response returns `expiresAt` representing the same UTC instant

### Requirement: Frontend API Key management

The SPA settings page SHALL include an API Key management section with: a toggle for `apiKeyAuthEnabled`, a key list table showing prefix/name/models/limit/usage/expiry/status, a create dialog (name, model selection, assigned-account selection, usage sections multi-select, weekly limit, expiry date), and key actions (edit, delete, regenerate). On key creation, the SPA MUST display the plain key in a copy-able dialog with a warning that it will not be shown again.

#### Scenario: Create key with optional account scoping

- **WHEN** an admin opens the create API key dialog
- **THEN** the dialog shows the Assigned accounts picker
- **AND** leaving the picker at `All accounts` creates an unscoped key
- **AND** selecting one or more accounts creates a scoped key for only those accounts

#### Scenario: Create key with usage sections multi-select

- **WHEN** an admin opens the create API key dialog
- **THEN** the dialog shows a "Usage sections shown to client" multi-select dropdown below the Assigned accounts picker
- **AND** the dropdown includes "Upstream limits" and "Account pool usage" options
- **AND** by default both options are selected

#### Scenario: Create key and show plain key

- **WHEN** admin creates a key via the UI
- **THEN** a dialog shows the full plain key with a copy button and a warning message

### Requirement: API keys can read their own /v1/usage

The system SHALL expose `GET /v1/usage` for self-service usage lookup by API-key clients. The route MUST require a valid API key in the `Authorization` header using the Bearer authentication scheme even when `api_key_auth_enabled` is false globally. The response MUST include only data for the authenticated key and MUST return:

- `request_count`
- `total_tokens`
- `cached_input_tokens`
- `total_cost_usd`
- `limits[]` containing only limits configured on the authenticated API key, with `limit_type`, `limit_window`, `max_value`, `current_value`, `remaining_value`, `model_filter`, `reset_at`, and `source`
- `upstream_limits[]` containing aggregate upstream Codex credit windows when available, with the same fields and `source: "aggregate"`, subject to the key's `usage_sections` containing `upstream_limits`
- `account_pool_usage` containing `primary` and `secondary` float remaining percentages, subject to the key's `usage_sections` containing `account_pool_usage`

Validation failures MUST use the existing OpenAI error envelope used by `/v1/*` routes.

#### Scenario: Missing API key is rejected

- **WHEN** a client calls `GET /v1/usage` without a Bearer token
- **THEN** the system returns 401 in the OpenAI error format

#### Scenario: Invalid API key is rejected

- **WHEN** a client calls `GET /v1/usage` with an unknown, expired, or inactive Bearer key
- **THEN** the system returns 401 in the OpenAI error format

#### Scenario: Key with no usage returns zero totals

- **WHEN** a valid API key with no request-log usage calls `GET /v1/usage`
- **THEN** the system returns `request_count: 0`, `total_tokens: 0`, `cached_input_tokens: 0`, `total_cost_usd: 0.0`

#### Scenario: Usage is scoped to the authenticated key

- **WHEN** multiple API keys have request-log history and one of them calls `GET /v1/usage`
- **THEN** the response includes only the usage totals and limits for that authenticated key

#### Scenario: Upstream limits are separate from API-key limits

- **WHEN** an API key with its own limit calls `GET /v1/usage`
- **AND** upstream Codex aggregate usage data exists
- **THEN** `limits[]` contains the API-key limit values
- **AND** `upstream_limits[]` contains the aggregate Codex credit windows

#### Scenario: Self-usage works while global proxy auth is disabled

- **WHEN** `api_key_auth_enabled` is false and a client calls `GET /v1/usage` with a valid Bearer key
- **THEN** the system still authenticates that key and returns the self-usage payload

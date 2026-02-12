## ADDED Requirements

### Requirement: API Key creation

The system SHALL allow the admin to create API keys via `POST /api/api-keys` with a `name` (required), `allowed_models` (optional list), `weekly_token_limit` (optional integer), and `expires_at` (optional ISO 8601 datetime). The system MUST generate a key in the format `sk-clb-{48 hex chars}`, store only the `sha256` hash in the database, and return the plain key exactly once in the creation response.

#### Scenario: Create key with all options

- **WHEN** admin submits `POST /api/api-keys` with `{ "name": "dev-key", "allowedModels": ["o3-pro"], "weeklyTokenLimit": 1000000, "expiresAt": "2025-12-31T00:00:00Z" }`
- **THEN** the system returns `{ "id": "<uuid>", "name": "dev-key", "key": "sk-clb-...", "keyPrefix": "sk-clb-a1b2c3d4", "allowedModels": ["o3-pro"], "weeklyTokenLimit": 1000000, "expiresAt": "2025-12-31T00:00:00Z", "createdAt": "..." }` with the plain key visible only in this response

#### Scenario: Create key with defaults

- **WHEN** admin submits `POST /api/api-keys` with `{ "name": "open-key" }` and no optional fields
- **THEN** the system creates a key with `allowedModels: null` (all models), `weeklyTokenLimit: null` (unlimited), `expiresAt: null` (no expiration)

#### Scenario: Create key with duplicate name

- **WHEN** admin submits a key with a `name` that already exists
- **THEN** the system creates the key (names are labels, not unique constraints)

### Requirement: API Key listing

The system SHALL expose `GET /api/api-keys` returning all API keys with their metadata. The response MUST NOT include the key hash or plain key. Each key MUST include `id`, `name`, `keyPrefix`, `allowedModels`, `weeklyTokenLimit`, `weeklyTokensUsed`, `weeklyResetAt`, `expiresAt`, `isActive`, `createdAt`, and `lastUsedAt`.

#### Scenario: List keys

- **WHEN** admin calls `GET /api/api-keys`
- **THEN** the system returns an array of key objects ordered by `createdAt` descending, without `key` or `keyHash` fields

#### Scenario: No keys exist

- **WHEN** no API keys have been created
- **THEN** the system returns an empty array `[]`

### Requirement: API Key update

The system SHALL allow updating key properties via `PATCH /api/api-keys/{id}`. Updatable fields: `name`, `allowedModels`, `weeklyTokenLimit`, `expiresAt`, `isActive`. The key hash and prefix MUST NOT be modifiable.

#### Scenario: Update allowed models

- **WHEN** admin submits `PATCH /api/api-keys/{id}` with `{ "allowedModels": ["o3-pro", "gpt-4.1"] }`
- **THEN** the system updates the allowed models list and returns the updated key

#### Scenario: Deactivate key

- **WHEN** admin submits `PATCH /api/api-keys/{id}` with `{ "isActive": false }`
- **THEN** the key is deactivated; subsequent Bearer requests using this key SHALL be rejected with 401

#### Scenario: Update non-existent key

- **WHEN** admin submits `PATCH /api/api-keys/{id}` with an unknown ID
- **THEN** the system returns 404

### Requirement: API Key deletion

The system SHALL allow deleting an API key via `DELETE /api/api-keys/{id}`. Deletion MUST be permanent and the key MUST immediately stop authenticating.

#### Scenario: Delete existing key

- **WHEN** admin calls `DELETE /api/api-keys/{id}` for an existing key
- **THEN** the key is permanently removed from the database and returns 204

#### Scenario: Delete non-existent key

- **WHEN** admin calls `DELETE /api/api-keys/{id}` with an unknown ID
- **THEN** the system returns 404

### Requirement: API Key regeneration

The system SHALL allow regenerating an API key via `POST /api/api-keys/{id}/regenerate`. This MUST generate a new key value (new hash, new prefix) while preserving all other properties (name, models, limits, expiration). The new plain key MUST be returned exactly once.

#### Scenario: Regenerate key

- **WHEN** admin calls `POST /api/api-keys/{id}/regenerate`
- **THEN** the system returns the updated key object with a new `key` and `keyPrefix`; the old key immediately stops authenticating

### Requirement: API Key authentication global switch

The system SHALL provide an `api_key_auth_enabled` boolean in `DashboardSettings`. When false (default), all proxy endpoints allow unauthenticated access. When true, all proxy endpoints require a valid API key via `Authorization: Bearer <key>`.

#### Scenario: Enable API key auth

- **WHEN** admin submits `PUT /api/settings` with `{ "apiKeyAuthEnabled": true }`
- **THEN** subsequent proxy requests without a valid Bearer token are rejected with 401

#### Scenario: Disable API key auth

- **WHEN** admin submits `PUT /api/settings` with `{ "apiKeyAuthEnabled": false }`
- **THEN** proxy requests are allowed without authentication

#### Scenario: Enable without any keys created

- **WHEN** admin enables API key auth but no keys exist
- **THEN** all proxy requests are rejected with 401 (the system SHALL NOT prevent enabling even if no keys exist)

### Requirement: API Key Bearer authentication middleware

The system SHALL validate API keys on proxy routes (`/v1/*`, `/backend-api/codex/*`, `/api/codex/*`) when `api_key_auth_enabled` is true. Validation MUST compute `sha256` of the Bearer token and look up the hash in the `api_keys` table.

#### Scenario: Valid active key

- **WHEN** a proxy request includes `Authorization: Bearer sk-clb-...` matching an active, non-expired key within its weekly limit
- **THEN** the middleware stores the key record in `request.state.api_key` and allows the request

#### Scenario: Missing Authorization header

- **WHEN** `api_key_auth_enabled` is true and a proxy request has no `Authorization` header
- **THEN** the middleware returns 401 with OpenAI-format error `{ "error": { "code": "invalid_api_key", "message": "Missing API key in Authorization header" } }`

#### Scenario: Invalid key

- **WHEN** a proxy request includes a Bearer token whose sha256 hash does not match any key in the database
- **THEN** the middleware returns 401 with error code `invalid_api_key`

#### Scenario: Inactive key

- **WHEN** a proxy request uses a key with `is_active = false`
- **THEN** the middleware returns 401 with error code `invalid_api_key`

#### Scenario: Expired key

- **WHEN** a proxy request uses a key where `expires_at < now()`
- **THEN** the middleware returns 401 with error code `invalid_api_key` and message indicating the key has expired

#### Scenario: Weekly token limit exceeded

- **WHEN** a proxy request uses a key where `weekly_tokens_used >= weekly_token_limit`
- **THEN** the middleware returns 429 with error code `rate_limit_exceeded` and a message including the `weekly_reset_at` time

### Requirement: Model restriction enforcement

The system SHALL enforce per-key model restrictions in the proxy service layer (not middleware). When `allowed_models` is set (non-null, non-empty) and the requested model is not in the list, the system MUST reject the request. The `/v1/models` endpoint MUST filter the model list based on the authenticated key's `allowed_models`.

#### Scenario: Requested model not allowed

- **WHEN** a key has `allowed_models: ["o3-pro"]` and a request is made for model `gpt-4.1`
- **THEN** the proxy returns 403 with OpenAI-format error `{ "error": { "code": "model_not_allowed", "message": "This API key does not have access to model 'gpt-4.1'" } }`

#### Scenario: All models allowed

- **WHEN** a key has `allowed_models: null`
- **THEN** any model is permitted

#### Scenario: Model list filtered

- **WHEN** a key with `allowed_models: ["o3-pro"]` calls `GET /v1/models`
- **THEN** the response contains only models matching the allowed list

#### Scenario: No API key auth (disabled)

- **WHEN** `api_key_auth_enabled` is false and a request is made to `/v1/models`
- **THEN** the full model catalog is returned

### Requirement: Weekly token usage tracking

The system SHALL atomically increment `weekly_tokens_used` on the API key record when a proxy request completes with token usage data. The token count MUST be `input_tokens + output_tokens`. If token usage is unavailable (error response), the counter MUST NOT be incremented.

#### Scenario: Successful request with usage

- **WHEN** a proxy request completes with `input_tokens: 100, output_tokens: 50` for an authenticated key
- **THEN** `weekly_tokens_used` is atomically incremented by 150

#### Scenario: Request with no usage data

- **WHEN** a proxy request fails with an error and no usage data is returned
- **THEN** `weekly_tokens_used` is not incremented

#### Scenario: Request without API key auth

- **WHEN** `api_key_auth_enabled` is false and a proxy request completes
- **THEN** no API key usage tracking occurs

### Requirement: Weekly token usage reset

The system SHALL reset `weekly_tokens_used` to 0 using a lazy on-read strategy. When validating an API key, if `weekly_reset_at < now()`, the system MUST reset the counter and advance `weekly_reset_at` by 7-day intervals until it is in the future.

#### Scenario: Weekly reset triggered on validation

- **WHEN** an API key is validated and `weekly_reset_at` is 2 weeks in the past
- **THEN** `weekly_tokens_used` is set to 0 and `weekly_reset_at` is advanced by 14 days (2 × 7 days) to a future date

#### Scenario: No reset needed

- **WHEN** an API key is validated and `weekly_reset_at` is in the future
- **THEN** no reset occurs; `weekly_tokens_used` retains its current value

### Requirement: RequestLog API key reference

The system SHALL record the `api_key_id` in the `request_logs` table for proxy requests authenticated with an API key. The field MUST be NULL when API key auth is disabled or the request is unauthenticated.

#### Scenario: Authenticated request logged

- **WHEN** a proxy request is authenticated with API key `key-123` and completes
- **THEN** the `request_logs` entry has `api_key_id = "key-123"`

#### Scenario: Unauthenticated request logged

- **WHEN** API key auth is disabled and a proxy request completes
- **THEN** the `request_logs` entry has `api_key_id = NULL`

### Requirement: Frontend API Key management

The SPA settings page SHALL include an API Key management section with: a toggle for `apiKeyAuthEnabled`, a key list table showing prefix/name/models/limit/usage/expiry/status, a create dialog (name, model selection, weekly limit, expiry date), and key actions (edit, delete, regenerate). On key creation, the SPA MUST display the plain key in a copy-able dialog with a warning that it will not be shown again.

#### Scenario: Create key and show plain key

- **WHEN** admin creates a key via the UI
- **THEN** a dialog shows the full plain key with a copy button and a warning message

#### Scenario: Toggle API key auth

- **WHEN** admin toggles `apiKeyAuthEnabled` in settings
- **THEN** the system calls `PUT /api/settings` and reflects the new state

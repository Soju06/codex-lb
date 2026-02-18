# api-keys Specification

## Purpose
TBD - created by archiving change admin-auth-and-api-keys. Update Purpose after archive.
## Requirements
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

The system SHALL validate API keys on proxy routes (`/v1/*`, `/backend-api/codex/*`) when `api_key_auth_enabled` is true. Validation MUST compute `sha256` of the Bearer token and look up the hash in the `api_keys` table.

`/api/codex/usage` SHALL NOT be covered by API key middleware scope.

#### Scenario: API key middleware route scope

- **WHEN** `api_key_auth_enabled` is true and a request is made to `/v1/responses` or `/backend-api/codex/responses`
- **THEN** API key middleware validation is applied

#### Scenario: Codex usage excluded from API key middleware scope

- **WHEN** `api_key_auth_enabled` is true and a request is made to `/api/codex/usage`
- **THEN** API key middleware validation is not applied

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

### Requirement: Model-scoped limit enforcement

The system SHALL separate authentication validation from quota enforcement. `validate_key()` in the middleware SHALL only verify key validity (existence, active status, expiry, basic reset). Quota enforcement SHALL occur at a point where the request model is known.

Limit applicability rules:
- `limit.model_filter is None` → always applies (global limit)
- `limit.model_filter == request_model` → applies (model-scoped limit)
- otherwise → does not apply for this request

For model-less requests (e.g., `/v1/models`), only global limits SHALL be evaluated.

The service contract SHALL be typed explicitly: `enforce_limits_for_request(key_id: str, *, request_model: str | None) -> None`.

#### Scenario: Model-scoped limit does not block other models

- **WHEN** `model_filter="gpt-5.1"` limit is exhausted
- **AND** request model is `gpt-4o-mini`
- **THEN** the request is allowed

#### Scenario: Model-scoped limit blocks matching model

- **WHEN** `model_filter="gpt-5.1"` limit is exhausted
- **AND** request model is `gpt-5.1`
- **THEN** the request returns 429

#### Scenario: Model-scoped limit does not block model-less endpoints

- **WHEN** `model_filter="gpt-5.1"` limit is exhausted
- **AND** request is to `/v1/models` (no model context)
- **THEN** the request is allowed

#### Scenario: Global limit blocks all proxy requests

- **WHEN** a global limit (no `model_filter`) is exhausted
- **THEN** all proxy requests return 429

### Requirement: Limit update with usage state preservation

When updating API key limits, the system SHALL preserve existing usage state (`current_value`, `reset_at`) for unchanged limit rules. Limit comparison key is `(limit_type, limit_window, model_filter)`.

- Matching existing rule: `current_value` and `reset_at` SHALL be preserved; only `max_value` is updated
- New rule (no match): `current_value=0` and fresh `reset_at`
- Removed rule (in existing but not in update): row is deleted

Usage reset SHALL only occur via an explicit action (`reset_usage` field or dedicated endpoint), never as a side-effect of metadata or policy edits.

#### Scenario: Metadata-only edit preserves usage state

- **WHEN** an API key PATCH updates only name or is_active
- **AND** `limits` field is not included in the payload
- **THEN** existing `current_value` and `reset_at` are unchanged

#### Scenario: Same policy re-submission preserves usage state

- **WHEN** an API key PATCH includes `limits` with identical rules (same type/window/filter/max_value)
- **THEN** existing `current_value` and `reset_at` are unchanged

#### Scenario: max_value adjustment preserves counters

- **WHEN** an API key PATCH includes `limits` with a changed `max_value` for an existing rule
- **THEN** `current_value` and `reset_at` are preserved; only the threshold changes

#### Scenario: Explicit reset action resets usage

- **WHEN** an explicit usage reset action is invoked
- **THEN** `current_value` is set to 0 and `reset_at` is refreshed

### Requirement: API key edit payload — conditional limits transmission

The frontend API key edit dialog SHALL transmit `limits` in the PATCH payload only when limit values have actually changed. The system SHALL normalize and compare initial and current limit values to detect changes.

- Metadata-only changes (name, is_active): `limits` field MUST be omitted from the payload
- Identical rule sets with different ordering: MUST be treated as unchanged (`limits` omitted)

Backend contract:
- `limits` absent in payload: limit policy unchanged (usage/reset state preserved)
- `limits` present in payload: policy replacement (state-preserving upsert applied)

#### Scenario: Name-only edit omits limits from payload

- **WHEN** only the API key name is modified in the edit dialog
- **THEN** the PATCH payload does not include the `limits` field

#### Scenario: Reordered identical rules treated as unchanged

- **WHEN** the same limit rules are submitted in a different order
- **THEN** the system treats this as unchanged and omits `limits` from the payload

### Requirement: Public model list filtering

All model list endpoints SHALL filter models using a single predicate that requires both conditions:
1. `model.supported_in_api` is true
2. If `allowed_models` is configured, the model is in the allowed set

This predicate SHALL be applied consistently across `/api/models`, `/v1/models`, and `/backend-api/codex/models`.

#### Scenario: Unsupported model excluded from /v1/models

- **WHEN** a model snapshot contains a model with `supported_in_api=false`
- **THEN** that model is not included in the `/v1/models` response

#### Scenario: Unsupported model excluded from /backend-api/codex/models

- **WHEN** a model snapshot contains a model with `supported_in_api=false`
- **THEN** that model is not included in the `/backend-api/codex/models` response

#### Scenario: Allowed but unsupported model excluded

- **WHEN** a model is in the `allowed_models` set but has `supported_in_api=false`
- **THEN** that model is not exposed in any model list endpoint

#### Scenario: Consistent model set across endpoints

- **GIVEN** any model registry state
- **THEN** `/api/models`, `/v1/models`, and `/backend-api/codex/models` expose the same set of models


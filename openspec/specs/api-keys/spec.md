# api-keys Specification

## Purpose

Define API key lifecycle, enforcement, accounting, and dashboard management contracts for downstream clients.
## Requirements
### Requirement: API Key creation

The system SHALL allow the admin to create API keys via `POST /api/api-keys` with a `name` (required), `allowed_models` (optional list), `weekly_token_limit` (optional integer), `expires_at` (optional ISO 8601 datetime), and `assigned_account_ids` (optional list). The system MUST generate a key in the format `sk-clb-{48 hex chars}`, store only the `sha256` hash in the database, and return the plain key exactly once in the creation response. The system MUST accept timezone-aware ISO 8601 datetimes for `expiresAt`, normalize them to UTC naive for persistence, and return the expiration as UTC in API responses.

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
The system SHALL allow updating key properties via `PATCH /api/api-keys/{id}`. Updatable fields: `name`, `allowedModels`, `weeklyTokenLimit`, `expiresAt`, `isActive`. The key hash and prefix MUST NOT be modifiable. The system MUST accept timezone-aware ISO 8601 datetimes for `expiresAt` and normalize them to UTC naive before persistence.

#### Scenario: Update key with timezone-aware expiration
- **WHEN** admin submits `PATCH /api/api-keys/{id}` with `{ "expiresAt": "2025-12-31T00:00:00Z" }`
- **THEN** the system persists the expiration successfully without PostgreSQL datetime binding errors
- **AND** the response returns `expiresAt` representing the same UTC instant

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
The system SHALL provide an `api_key_auth_enabled` boolean in `DashboardSettings`. When false (default), local requests to protected proxy routes MAY proceed without an API key. Operators MAY additionally opt specific non-local proxy clients into unauthenticated access by configuring `proxy_unauthenticated_client_cidrs`. Requests that are neither local nor explicitly allowlisted MUST be rejected until proxy authentication is configured. When true, protected proxy routes require a valid API key in the `Authorization` header using the Bearer authentication scheme.

#### Scenario: Enable API key auth

- **WHEN** admin submits `PUT /api/settings` with `{ "apiKeyAuthEnabled": true }`
- **THEN** subsequent proxy requests without a valid Bearer token are rejected with 401

#### Scenario: Disable API key auth for a local request

- **WHEN** admin submits `PUT /api/settings` with `{ "apiKeyAuthEnabled": false }`
- **AND** a local client calls a protected proxy route
- **THEN** the request is allowed without API key authentication

#### Scenario: Disable API key auth for a non-local request

- **WHEN** admin submits `PUT /api/settings` with `{ "apiKeyAuthEnabled": false }`
- **AND** a non-local client calls a protected proxy route
- **THEN** the request is rejected with 401 until proxy authentication is configured

#### Scenario: Disable API key auth for an explicitly allowlisted proxy client
- **WHEN** admin submits `PUT /api/settings` with `{ "apiKeyAuthEnabled": false }`
- **AND** the request socket peer IP belongs to configured `proxy_unauthenticated_client_cidrs`
- **THEN** the protected proxy route proceeds without API key authentication

#### Scenario: Disable API key auth for a non-local request outside the explicit allowlist
- **WHEN** admin submits `PUT /api/settings` with `{ "apiKeyAuthEnabled": false }`
- **AND** a non-local client calls a protected proxy route
- **AND** the request socket peer IP is outside configured `proxy_unauthenticated_client_cidrs`
- **THEN** the request is rejected with 401 until proxy authentication is configured

#### Scenario: Enable without any keys created

- **WHEN** admin enables API key auth but no keys exist
- **THEN** all proxy requests are rejected with 401 (the system SHALL NOT prevent enabling even if no keys exist)

#### Scenario: Toggle API key auth

- **WHEN** admin toggles `apiKeyAuthEnabled` in settings
- **THEN** the system calls `PUT /api/settings` and reflects the new state

### Requirement: API Key Bearer authentication guard
The system SHALL validate API keys on protected proxy routes (`/v1/*`, `/backend-api/codex/*`, `/backend-api/transcribe`) when `api_key_auth_enabled` is true. Validation MUST be implemented as a router-level `Security` dependency, not ASGI middleware. The dependency MUST compute `sha256` of the Bearer token and look up the hash in the `api_keys` table.

The dependency SHALL return a typed `ApiKeyData` value directly to the route handler. Route handlers MUST NOT access API key data via `request.state`.

`/api/codex/usage` SHALL NOT be covered by the API key auth guard scope.

The dependency SHALL raise a domain exception on validation failure. The exception handler SHALL format the response using the OpenAI error envelope.

#### Scenario: Disabled auth allowlist uses raw socket peer only
- **WHEN** `api_key_auth_enabled` is false
- **AND** forwarded headers claim a different client IP
- **AND** the request socket peer IP is outside configured `proxy_unauthenticated_client_cidrs`
- **THEN** the dependency rejects the request with 401
- **AND** forwarded headers do not satisfy the explicit allowlist

#### Scenario: API key guard route scope

- **WHEN** `api_key_auth_enabled` is true and a request is made to `/v1/responses`, `/backend-api/codex/responses`, `/v1/audio/transcriptions`, or `/backend-api/transcribe`
- **THEN** the API key guard validation is applied

#### Scenario: Codex usage excluded from API key guard scope

- **WHEN** `api_key_auth_enabled` is true and a request is made to `/api/codex/usage`
- **THEN** API key guard validation is not applied

#### Scenario: Valid API key injected into handler

- **WHEN** `api_key_auth_enabled` is true and a valid Bearer token is provided
- **THEN** the route handler receives a typed `ApiKeyData` parameter (not `request.state`)

#### Scenario: API key auth disabled returns None for local requests

- **WHEN** `api_key_auth_enabled` is false
- **AND** the request is classified as local
- **THEN** the dependency returns `None` and the request proceeds without authentication

#### Scenario: API key auth disabled rejects non-local requests

- **WHEN** `api_key_auth_enabled` is false
- **AND** the request is classified as non-local
- **AND** the request socket peer IP is outside configured `proxy_unauthenticated_client_cidrs`
- **THEN** the dependency rejects the request with 401

### Requirement: Model restriction enforcement

The system SHALL enforce per-key model restrictions in the proxy service layer (not middleware). When `allowed_models` is set (non-null, non-empty) and the requested model is not in the list, the system MUST reject the request. The `/v1/models` endpoint MUST filter the model list based on the authenticated key's `allowed_models`.

For fixed-model endpoints such as `/v1/audio/transcriptions` and `/backend-api/transcribe`, the service MUST evaluate restrictions against fixed effective model `gpt-4o-transcribe`.

`/backend-api/codex/models` SHALL keep the existing allowlist filtering behavior by default. When an authenticated API key has `apply_to_codex_model = true` and `allowed_models` is non-empty, `/backend-api/codex/models` SHALL return the full catalog and rewrite each model entry visibility so allowlisted models use `visibility: "list"` and every other model uses `visibility: "hide"`. When `apply_to_codex_model = true` but `allowed_models` is null or empty, `/backend-api/codex/models` SHALL preserve the original behavior because there is no allowlist to apply.

#### Scenario: Requested model not allowed

- **WHEN** a key has `allowed_models: ["o3-pro"]` and a request is made for model `gpt-4.1`
- **THEN** the proxy returns 403 with OpenAI-format error `{ "error": { "code": "model_not_allowed", "message": "This API key does not have access to model 'gpt-4.1'" } }`

#### Scenario: Cursor alias allowed model permits canonical request

- **WHEN** a key has `allowed_models: ["gpt-5.4-mini-high"]`
- **AND** a request is made for model `gpt-5.4-mini`
- **THEN** the proxy permits the request because the allowed alias resolves to the requested canonical model

#### Scenario: All models allowed

- **WHEN** a key has `allowed_models: null`
- **THEN** any model is permitted

#### Scenario: Model list filtered

- **WHEN** a key with `allowed_models: ["o3-pro"]` calls `GET /v1/models`
- **THEN** the response contains only models matching the allowed list

#### Scenario: Model list canonicalizes Cursor aliases

- **WHEN** a key with `allowed_models: ["gpt-5.4-mini-high"]` and `enforced_model: "gpt-5.4-mini-high"` calls `GET /v1/models`
- **THEN** the response contains the canonical model `gpt-5.4-mini`
- **AND** the response does not expose a synthetic `gpt-5.4-mini-high` model id

#### Scenario: Codex model list visibility canonicalizes Cursor aliases

- **WHEN** a key with `allowed_models: ["gpt-5.4-mini-high"]`, `enforced_model: "gpt-5.4-mini-high"`, and `apply_to_codex_model=true` calls `GET /backend-api/codex/models`
- **THEN** the canonical `gpt-5.4-mini` entry is visible with `visibility: "list"`
- **AND** other entries are hidden according to the API key allowlist policy

#### Scenario: No API key auth (disabled)

- **WHEN** `api_key_auth_enabled` is false and a request is made to `/v1/models`
- **THEN** the full model catalog is returned

#### Scenario: Fixed transcription model not allowed

- **WHEN** a key has `allowed_models: ["gpt-5.1"]` and a request is made to `/v1/audio/transcriptions` or `/backend-api/transcribe`
- **THEN** the proxy returns 403 with OpenAI-format error code `model_not_allowed` for model `gpt-4o-transcribe`

#### Scenario: Codex models keep filtered behavior by default
- **WHEN** a key has `allowed_models: ["o3-pro"]` and `apply_to_codex_model: false`
- **AND** the key calls `GET /backend-api/codex/models`
- **THEN** the response contains only models matching the allowed list

#### Scenario: Codex models rewrite visibility when opted in
- **WHEN** a key has `allowed_models: ["o3-pro"]` and `apply_to_codex_model: true`
- **AND** the key calls `GET /backend-api/codex/models`
- **THEN** the response contains the full catalog
- **AND** the `o3-pro` entry has `visibility: "list"`
- **AND** every model not in `allowed_models` has `visibility: "hide"`

#### Scenario: Codex models preserve original behavior without an allowlist
- **WHEN** a key has `allowed_models: null` and `apply_to_codex_model: true`
- **AND** the key calls `GET /backend-api/codex/models`
- **THEN** the response preserves the original `/backend-api/codex/models` behavior because there is no allowlist to apply
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

The system SHALL keep the existing lazy on-read reset strategy for API key usage limits. When validating an API key, if a limit `reset_at < now()`, the system MUST reset the counter and advance `reset_at` by whole window intervals until it is in the future. The system MUST also run an hourly background fallback sweep that repairs expired API key limit usage even when no validation request arrives.

#### Scenario: Weekly reset triggered on validation

- **WHEN** an API key is validated and `weekly_reset_at` is 2 weeks in the past
- **THEN** `weekly_tokens_used` is set to 0 and `weekly_reset_at` is advanced by 14 days (2 × 7 days) to a future date

#### Scenario: No reset needed

- **WHEN** an API key is validated and `weekly_reset_at` is in the future
- **THEN** no reset occurs; `weekly_tokens_used` retains its current value

#### Scenario: Hourly fallback resets expired usage without a read

- **WHEN** an API key usage limit is expired and no validation request occurs
- **THEN** the hourly background fallback resets `current_value` to 0 and advances `reset_at` into the future

### Requirement: RequestLog API key reference

The system SHALL record the `api_key_id` in the `request_logs` table for proxy requests authenticated with an API key. The field MUST be NULL when API key auth is disabled or the request is unauthenticated.

#### Scenario: Authenticated request logged

- **WHEN** a proxy request is authenticated with API key `key-123` and completes
- **THEN** the `request_logs` entry has `api_key_id = "key-123"`

#### Scenario: Unauthenticated request logged

- **WHEN** API key auth is disabled and a proxy request completes
- **THEN** the `request_logs` entry has `api_key_id = NULL`

### Requirement: Frontend API Key management

The SPA settings page SHALL include an API Key management section with: a toggle for `apiKeyAuthEnabled`, a key list table showing prefix/name/models/limit/usage/expiry/status, a create dialog (name, model selection, assigned-account selection, weekly limit, expiry date), and key actions (edit, delete, regenerate). On key creation, the SPA MUST display the plain key in a copy-able dialog with a warning that it will not be shown again, and the copy action MUST remain functional in secure and non-secure contexts.

The create and edit dialogs SHALL expose an `Apply to codex /model` checkbox directly below `Allowed models`. The checkbox SHALL default to unchecked for new keys and SHALL edit the stored API key value for existing keys.

#### Scenario: Create key with optional account scoping

- **WHEN** an admin opens the create API key dialog
- **THEN** the dialog shows the Assigned accounts picker
- **AND** leaving the picker at `All accounts` creates an unscoped key
- **AND** selecting one or more accounts creates a scoped key for only those accounts

#### Scenario: Create key and show plain key

- **WHEN** admin creates a key via the UI
- **THEN** a dialog shows the full plain key with a copy button and a warning message

#### Scenario: API key dialog copy fallback

- **WHEN** a user clicks Copy for the created API key inside the dialog
- **THEN** the copy operation succeeds using secure Clipboard API when available
- **AND** falls back to dialog-scoped `execCommand("copy")` when secure Clipboard API is unavailable

#### Scenario: Create key with codex model visibility option
- **WHEN** an admin opens the create API key dialog
- **THEN** the `Apply to codex /model` checkbox appears directly below `Allowed models`
- **AND** it is unchecked by default

#### Scenario: Edit key with stored codex model visibility option
- **WHEN** an admin opens the edit API key dialog for a key with `apply_to_codex_model: true`
- **THEN** the `Apply to codex /model` checkbox is shown as checked
### Requirement: Cost accounting uses model and service-tier pricing
When computing API key `cost_usd` usage, the system MUST price requests using the resolved model pricing and the authoritative `service_tier` reported by the upstream response when available, falling back to the forwarded request `service_tier` only when the response omits it. Requests sent with non-standard service tiers MUST use the published pricing for the tier actually used instead of falling back to standard-tier pricing.

#### Scenario: Priority-tier request increments cost limit
- **WHEN** an authenticated request for a priced model is finalized with `service_tier: "priority"`
- **THEN** the system computes `cost_usd` using the priority-tier rate for that model

#### Scenario: Flex-tier request increments cost limit
- **WHEN** an authenticated request for a priced model is finalized with `service_tier: "flex"`
- **THEN** the system computes `cost_usd` using the flex-tier rate for that model

#### Scenario: Standard-tier request keeps standard pricing
- **WHEN** an authenticated request for the same model is finalized without `service_tier`
- **THEN** the system computes `cost_usd` using the standard-tier rate

### Requirement: gpt-5.4 pricing is recognized
The system MUST recognize `gpt-5.4` pricing when computing request costs. For standard-tier requests with more than 272K input tokens, the system MUST apply the published higher long-context rates.

#### Scenario: gpt-5.4 request priced at standard tier
- **WHEN** a request for `gpt-5.4` completes with standard service tier
- **THEN** the system computes non-zero cost using the configured `gpt-5.4` standard rates

#### Scenario: gpt-5.4 long-context request priced at long-context rates
- **WHEN** a standard-tier `gpt-5.4` request completes with more than 272K input tokens
- **THEN** the system computes cost using the configured long-context `gpt-5.4` rates

### Requirement: Model-scoped limit enforcement

The system SHALL separate authentication validation from quota enforcement. `validate_key()` in the auth guard SHALL only verify key validity (existence, active status, expiry, basic reset). Quota enforcement SHALL occur at a point where the request model is known.

Limit applicability rules:
- `limit.model_filter is None` → always applies (global limit)
- `limit.model_filter == request_model` → applies (model-scoped limit)
- otherwise → does not apply for this request

For model-less requests (e.g., `/v1/models`), only global limits SHALL be evaluated.

The service contract SHALL be typed explicitly: `enforce_limits_for_request(key_id: str, *, request_model: str | None, request_service_tier: str | None = None) -> None`.

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

### Requirement: Reservation 정산 exactly-once 보장

Usage reservation의 최종 정산(finalize 또는 release)은 요청 단위에서 정확히 1회 수행되어야 한다. 재시도 가능한 중간 attempt에서는 정산을 defer하고, 요청 종료 시점에서 단일 지점이 정산 책임을 갖는다. 시스템은 이 동작을 SHALL 보장해야 한다.

#### Scenario: 스트림 401 → refresh retry 성공 시 finalize 1회

- **WHEN** 첫 `_stream_once()` attempt에서 401을 수신하고 계정 refresh 후 재시도가 성공하면
- **THEN** 첫 attempt에서는 reservation 정산이 수행되지 않아야 한다 (SHALL)
- **AND** 최종 성공 시점에서 `finalize_usage_reservation()`이 정확히 1회 호출되어야 한다 (SHALL)
- **AND** 실제 token 사용량이 quota에 반영되어야 한다 (SHALL)

#### Scenario: 스트림 401 → retry 소진 실패 시 release 1회

- **WHEN** 401 후 재시도를 모두 소진하여 요청이 최종 실패하면
- **THEN** `release_usage_reservation()`이 정확히 1회 호출되어야 한다 (SHALL)
- **AND** 예약된 quota가 원복되어야 한다 (SHALL)

#### Scenario: 스트림 성공 시 finalize 1회

- **WHEN** `_stream_once()`가 retry 없이 첫 attempt에서 성공하면
- **THEN** `finalize_usage_reservation()`이 정확히 1회 호출되어야 한다 (SHALL)

### Requirement: 조기 종료 경로에서 reservation release 보장

Reservation 생성 후 upstream API 호출에 진입하지 않고 종료되는 모든 경로에서 reservation이 release되어야 한다. `reserved` 상태로 남는 reservation이 존재하면 안 된다. 시스템은 이 동작을 SHALL 보장해야 한다.

#### Scenario: no_accounts 즉시 종료 시 release

- **WHEN** reservation 생성 후 `_stream_with_retry()`가 사용 가능한 계정 없음(`no_accounts`)으로 즉시 종료되면
- **THEN** `release_usage_reservation()`이 호출되어 reservation이 `released` 상태로 전이되어야 한다 (SHALL)
- **AND** pre-reserved quota가 원복되어야 한다 (SHALL)

#### Scenario: 재시도 소진 후 no_accounts 종료 시 release

- **WHEN** 재시도 루프가 모든 attempt를 소진한 후 `no_accounts`로 종료되면
- **THEN** `release_usage_reservation()`이 호출되어야 한다 (SHALL)

#### Scenario: reservation 미생성 시 정산 스킵

- **WHEN** API key auth가 비활성이거나 reservation이 생성되지 않은 상태에서 요청이 종료되면
- **THEN** 정산 로직이 안전하게 스킵되어야 하며 에러가 발생하지 않아야 한다 (SHALL)

### Requirement: Compact 경로 예외 무관 reservation cleanup

`_compact_responses()` 경로에서 reservation이 존재할 때, 어떤 예외 타입이 발생하더라도 reservation이 정리되어야 한다. 특정 예외 타입에만 의존하는 cleanup은 허용되지 않는다. 시스템은 이 동작을 SHALL 보장해야 한다.

#### Scenario: ProxyResponseError 발생 시 release

- **WHEN** `compact_responses()`에서 `ProxyResponseError`가 발생하면
- **THEN** reservation이 release되어야 한다 (SHALL)

#### Scenario: 예상 외 런타임 예외 발생 시 release

- **WHEN** `compact_responses()`에서 `ProxyResponseError` 외의 예외(`Exception`)가 발생하면
- **THEN** reservation이 동일하게 release되어야 한다 (SHALL)

#### Scenario: compact 성공 시 finalize

- **WHEN** `compact_responses()`가 정상 완료되면
- **THEN** `finalize_usage_reservation()`이 호출되어야 한다 (SHALL)

### Requirement: Finalize / Release 멱등성

`finalize_usage_reservation()`과 `release_usage_reservation()`은 이미 정산된(finalized 또는 released) reservation에 대해 안전하게 no-op 처리되어야 한다. 이중 호출이 quota를 이중 반영하거나 에러를 발생시키면 안 된다. 시스템은 이 동작을 SHALL 보장해야 한다.

#### Scenario: finalize 후 release 호출 시 no-op

- **WHEN** reservation이 이미 `finalized` 상태에서 `release_usage_reservation()`이 호출되면
- **THEN** 아무 동작 없이 반환되어야 한다 (SHALL)
- **AND** quota 값이 변경되지 않아야 한다 (SHALL)

#### Scenario: release 후 finalize 호출 시 no-op

- **WHEN** reservation이 이미 `released` 상태에서 `finalize_usage_reservation()`이 호출되면
- **THEN** 아무 동작 없이 반환되어야 한다 (SHALL)
- **AND** quota 값이 변경되지 않아야 한다 (SHALL)

#### Scenario: 동일 finalize 이중 호출 시 1회만 반영

- **WHEN** 동일 `reservation_id`로 `finalize_usage_reservation()`이 2회 호출되면
- **THEN** 사용량은 정확히 1회만 반영되어야 한다 (SHALL)

### Requirement: gpt-5.4-mini pricing is recognized

The system MUST recognize `gpt-5.4-mini` pricing when computing request costs. Snapshot aliases for the same model family MUST resolve to the canonical `gpt-5.4-mini` price table entry.

#### Scenario: gpt-5.4-mini request priced at standard tier

- **WHEN** a request for `gpt-5.4-mini` completes with standard service tier
- **THEN** the system computes non-zero cost using the configured `gpt-5.4-mini` standard rates

#### Scenario: gpt-5.4-mini snapshot request priced at canonical rates

- **WHEN** a request for `gpt-5.4-mini-2026-03-17` completes
- **THEN** the system resolves the snapshot alias to `gpt-5.4-mini`
- **AND** the system applies the same standard rates

### Requirement: API keys can read their own `/v1/usage`

The system SHALL expose `GET /v1/usage` for self-service usage lookup by API-key clients. The route MUST require a valid API key in the `Authorization` header using the Bearer authentication scheme even when `api_key_auth_enabled` is false globally. The response MUST include only data for the authenticated key and MUST return:

- `request_count`
- `total_tokens`
- `cached_input_tokens`
- `total_cost_usd`
- `limits[]` containing only limits configured on the authenticated API key, with `limit_type`, `limit_window`, `max_value`, `current_value`, `remaining_value`, `model_filter`, `reset_at`, and `source`
- `upstream_limits[]` containing aggregate upstream Codex credit windows when available, with the same fields and `source: "aggregate"`

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

### Requirement: API key cost accounting uses the billable service tier
API key cost accounting MUST continue to use the effective billable `service_tier` chosen for the request log and MUST NOT derive pricing from the operator-requested tier when the upstream reports a different actual tier.

#### Scenario: Requested and actual tiers differ
- **WHEN** a priced request is sent with `requested_service_tier: "priority"`
- **AND** the upstream reports `actual_service_tier: "default"`
- **THEN** the persisted billable `service_tier` is `default`
- **AND** API key cost accounting uses the `default` tier rate for that request

### Requirement: API keys can enforce a service tier

The dashboard API key CRUD surface MUST allow callers to persist an optional enforced service tier. The service MUST normalize `fast` to the canonical upstream value `priority` before persistence and before returning the API key payload.

#### Scenario: Create API key with fast service tier alias

- **WHEN** a dashboard client creates an API key with `enforcedServiceTier: "fast"`
- **THEN** the request is accepted
- **AND** the persisted API key stores the canonical value `priority`
- **AND** the response returns `enforcedServiceTier: "priority"`

#### Scenario: Update API key with canonical service tier

- **WHEN** a dashboard client updates an API key with `enforcedServiceTier: "flex"`
- **THEN** the persisted API key stores `flex`
- **AND** subsequent reads return `flex`

### Requirement: API key list includes pooled credit data

The `GET /api/api-keys/` list endpoint SHALL include per-key pooled credit data computed by aggregating upstream usage across the selectable accounts assigned to each key. When a key has no assigned accounts, the system SHALL pool across all selectable accounts.

Selectable accounts exclude accounts whose status is `paused` or `deactivated`, matching load-balancer routing eligibility.

The response SHALL include `pooled_remaining_percent_primary` (float or null), `pooled_remaining_percent_secondary` (float or null), and `pooled_capacity_credits_primary` (float, default 0.0) on each key object.

When `pooled_capacity_credits_primary` is 0.0 (e.g., all assigned accounts are free-tier), `pooled_remaining_percent_primary` SHALL be null.

#### Scenario: Scoped key pools assigned accounts only

- **WHEN** an API key has `assignedAccountIds` containing two accounts
- **AND** those accounts have usage data
- **THEN** `pooled_remaining_percent_primary` and `pooled_remaining_percent_secondary` reflect only those two accounts

#### Scenario: Unscoped key pools all accounts

- **WHEN** an API key has `assignedAccountIds` = []
- **THEN** pooled credit fields reflect all accounts in the system

#### Scenario: Free-tier accounts hide primary bar

- **WHEN** all assigned accounts have plan_type "free" (primary capacity = 0)
- **THEN** `pooled_capacity_credits_primary` = 0.0
- **AND** `pooled_remaining_percent_primary` = null

#### Scenario: Paused and deactivated accounts are excluded

- **WHEN** an API key has assigned accounts with active and paused statuses
- **THEN** pooled credit fields reflect only the active selectable accounts

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

### Requirement: Request-aware API-key usage reservations

API-key usage reservation admission MUST reserve a bounded request-aware budget instead of an unconditional fixed 8192 input-token plus 8192 output-token pre-charge for every request. The reservation budget MUST be used only for admission and in-flight accounting; final usage accounting MUST continue to settle to the authoritative completed request usage and service-tier pricing.

For token limits, admission MUST reserve from the request input and output token budgets. The input budget MAY be estimated from self-contained request payloads, while opaque upstream context MUST fall back to a conservative input budget. The output budget MUST use a bounded system default unless codex-lb can verify that a client-provided output cap is actually enforced upstream. For `cost_usd` limits, admission MUST compute the reservation cost from the same input and output token budgets and the effective request service tier. Reservation finalization MUST adjust every applicable reserved value to actual completed usage exactly once, including limits whose admission reservation was zero.

#### Scenario: Concurrent priority lanes do not require 8 × 8192 output-token headroom

- **WHEN** an API key has a `cost_usd` limit with enough remaining value for the bounded request-aware reservations
- **AND** eight `gpt-5.5` requests using `service_tier = "priority"` are admitted concurrently
- **THEN** the proxy allows all eight reservations instead of rejecting a lane solely because the old 8192-output-token pre-charge would exceed the limit

#### Scenario: Opaque input uses conservative input fallback

- **WHEN** a request references input that the proxy cannot size locally, such as `previous_response_id`, `conversation`, `input_file`, or `input_image`
- **THEN** API-key admission uses the conservative default input-token reservation budget for input tokens
- **AND** final accounting still settles to actual completed usage

#### Scenario: Zero-reservation limits still settle actual usage

- **WHEN** API-key admission records a zero-delta reservation item for an applicable limit
- **AND** the request completes with non-zero actual usage for that limit
- **THEN** reservation finalization increments the limit by the actual usage instead of skipping the limit

### Requirement: Map `auto`/`default` enforced service tier to outbound omission
When a request is enforced under an API key whose `enforced_service_tier` is `auto` or `default`, the proxy MUST forward the request with `service_tier` absent (`None`) rather than as the literal string. Enforcement of `priority` and `flex` MUST continue to forward the literal value unchanged. codex-lb accepts `auto`, `default`, `priority`, and `flex` (plus the `fast` alias for `priority`) at the API-key `enforced_service_tier` surface; the ChatGPT/Codex backend rejects `auto` and `default` as literal values, since both already mean "let upstream pick".

#### Scenario: Enforced service tier is `default`
- **WHEN** a request is processed under an API key with `enforced_service_tier = "default"`
- **THEN** the outbound `service_tier` field is absent

#### Scenario: Enforced service tier is `auto`
- **WHEN** a request is processed under an API key with `enforced_service_tier = "auto"`
- **THEN** the outbound `service_tier` field is absent

#### Scenario: Enforced service tier is a real upstream tier
- **WHEN** a request is processed under an API key with `enforced_service_tier = "priority"` or `"flex"`
- **THEN** the outbound `service_tier` field equals the enforced value

### Requirement: API key allowlist allows Cursor aliases

The model allowlist check MUST treat supported Cursor-style GPT-5 aliases as equivalent to their
canonical GPT model when deciding access. A request for the canonical model must be allowed when the key
stores a compatible alias in `allowed_models`.

#### Scenario: Cursor alias allowed model permits canonical request

- **WHEN** a key has `allowed_models: ["gpt-5.4-mini-high"]`
- **AND** a request is made for model `gpt-5.4-mini`
- **THEN** the proxy permits the request because the allowed alias resolves to the requested canonical model

### Requirement: Model catalogs must expose canonical models for alias allowlists

When API-key model allowlists include Cursor-style aliases, the visible model lists MUST expose canonical model IDs and
omit alias-only synthetic IDs so clients see stable model names.

#### Scenario: Model list canonicalizes Cursor aliases

- **WHEN** a key with `allowed_models: ["gpt-5.4-mini-high"]` and `enforced_model: "gpt-5.4-mini-high"` calls `GET /v1/models`
- **THEN** the response contains the canonical model `gpt-5.4-mini`
- **AND** the response does not expose a synthetic `gpt-5.4-mini-high` model id

#### Scenario: Codex model list visibility canonicalizes Cursor aliases

- **WHEN** a key with `allowed_models: ["gpt-5.4-mini-high"]`, `enforced_model: "gpt-5.4-mini-high"`, and `apply_to_codex_model=true` calls `GET /backend-api/codex/models`
- **THEN** the canonical `gpt-5.4-mini` entry is visible with `visibility: "list"`
- **AND** other entries are hidden according to the API key allowlist policy

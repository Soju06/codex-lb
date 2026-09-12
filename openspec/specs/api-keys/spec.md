# api-keys Specification

## Purpose

Define API key lifecycle, enforcement, accounting, and dashboard management contracts for downstream clients.
## Requirements
### Requirement: API Key creation

The system SHALL allow the admin to create API keys via `POST /api/api-keys` with a `name` (required), `allowedModels` (optional list), `weeklyTokenLimit` (optional integer), `expiresAt` (optional ISO 8601 datetime), `assignedAccountIds` (optional list), and `usageSections` (optional comma-separated string, defaults to `"upstream_limits,account_pool_usage"`). The system MUST generate a key in the format `sk-clb-{48 hex chars}`, store only the `sha256` hash in the database, and return the plain key exactly once in the creation response. The system MUST accept timezone-aware ISO 8601 datetimes for `expiresAt`, normalize them to UTC naive for persistence, and return the expiration as UTC in API responses.

When `assignedAccountIds` is omitted or empty, the created key SHALL remain unscoped and apply to all accounts. When `assignedAccountIds` is provided with one or more valid account IDs, the created key SHALL enable account-assignment scope and persist those assignments.

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
- **THEN** the response contains a key matching `sk-clb-[0-9a-f]{48}`
- **AND** the full plain key is returned exactly once
- **AND** the system never returns the plain key on subsequent reads

#### Scenario: Create key with timezone-aware expiration

- **WHEN** admin submits `POST /api/api-keys` with `{ "name": "dev-key", "expiresAt": "2025-12-31T00:00:00Z" }`
- **THEN** the system persists the expiration successfully without PostgreSQL datetime binding errors
- **AND** the response returns `expiresAt` representing the same UTC instant

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

### Requirement: API Key deletion

The system SHALL allow deleting an API key via `DELETE /api/api-keys/{id}`. Deletion MUST be permanent and the key MUST immediately stop authenticating.

#### Scenario: Delete existing key

- **WHEN** admin calls `DELETE /api/api-keys/{id}` for an existing key
- **THEN** the key is permanently removed from the database and returns 204

#### Scenario: Delete non-existent key

- **WHEN** admin calls `DELETE /api/api-keys/{id}` with an unknown ID
- **THEN** the system returns 404

### Requirement: API Key regeneration

The system SHALL allow regenerating an API key via `POST /api/api-keys/{id}/regenerate`. This MUST generate a new key matching `sk-clb-[0-9a-f]{48}` with a new hash and prefix while preserving all other properties (name, models, limits, expiration). The new plain key MUST be returned exactly once.

#### Scenario: Regenerate key

- **WHEN** admin calls `POST /api/api-keys/{id}/regenerate`
- **THEN** the system returns the updated key object with a new `key` and `keyPrefix`
- **AND** the new key matches `sk-clb-[0-9a-f]{48}`
- **AND** the old key immediately stops authenticating

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

### Requirement: Proxy identity cannot bypass disabled API-key auth

When API-key authentication is disabled, protected HTTP and WebSocket routes MUST use raw-peer-backed locality consensus. Any `proxy_unauthenticated_client_cidrs` exception MUST evaluate only the launcher-preserved raw socket peer and MUST fail closed when that peer is unavailable. A projected client identity MUST NOT satisfy the socket allowlist.

#### Scenario: Agreeing identities retain local access

- **WHEN** a trusted raw peer sends populated identity families that all resolve to loopback
- **AND** the request otherwise satisfies local-request rules
- **THEN** `/v1/models` and `/v1/responses` may proceed without an API key

#### Scenario: Conflict cannot authorize HTTP or WebSocket

- **WHEN** populated identity families resolve differently or one cannot be resolved
- **AND** the raw peer is outside `proxy_unauthenticated_client_cidrs`
- **THEN** `/v1/models` is rejected with HTTP 401
- **AND** `/v1/responses` WebSocket is rejected with HTTP 401 before upgrade

#### Scenario: Projected allowlist identity is rejected

- **WHEN** the projected client belongs to `proxy_unauthenticated_client_cidrs` but the preserved raw peer does not
- **THEN** the protected route is rejected with HTTP 401

### Requirement: Model restriction enforcement

The system SHALL enforce per-key model restrictions in the proxy service layer (not middleware). When `allowed_models` is set (non-null, non-empty) and the requested model is not in the list, the system MUST reject the request. When reading stored `allowed_models`, JSON `null`, blank strings, and non-string array entries MUST be ignored and MUST NOT become model names. The `/v1/models` endpoint MUST filter the model list based on the authenticated key's `allowed_models`.

#### Scenario: Stored null allowed-model entries are ignored

- **GIVEN** an API key row stores `allowed_models` as `[null, "gpt-5.2", 42, ""]`
- **WHEN** the key policy is loaded
- **THEN** the effective allowed model list is `["gpt-5.2"]`
- **AND** `null` is not converted to `"None"`

### Requirement: Weekly token usage tracking
The system SHALL atomically increment `weekly_tokens_used` on the API key record when a non-warmup proxy request completes with token usage data. The token count MUST be `input_tokens + output_tokens`. If token usage is unavailable (error response), the counter MUST NOT be incremented.

#### Scenario: Successful request with usage

- **WHEN** a non-warmup proxy request completes with `input_tokens: 100, output_tokens: 50` for an authenticated key
- **THEN** `weekly_tokens_used` is atomically incremented by 150

#### Scenario: Request with no usage data

- **WHEN** a non-warmup proxy request fails with an error and no usage data is returned
- **THEN** `weekly_tokens_used` is not incremented

#### Scenario: Request without API key auth

- **WHEN** `api_key_auth_enabled` is false and a non-warmup proxy request completes
- **THEN** no API key usage tracking occurs

#### Scenario: Warmup request is excluded from weekly usage tracking

- **WHEN** an authenticated `POST /v1/warmup` execution writes request log rows
- **THEN** those warmup rows are excluded from API key weekly token usage increments

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

The system SHALL record the `api_key_id` in the `request_logs` table for proxy
requests authenticated with an API key. The field MUST be NULL when API key
auth is disabled or the request is unauthenticated. This applies to error rows
as well as successes: when a shared upstream session (e.g. an HTTP-bridge
session multiplexing requests from multiple API keys) fails its pending
requests, each request's log row MUST be attributed to that request's own
authenticated key.

#### Scenario: Authenticated request logged

- **WHEN** a proxy request is authenticated with API key `key-123` and completes
- **THEN** the `request_logs` entry has `api_key_id = "key-123"`

#### Scenario: Unauthenticated request logged

- **WHEN** API key auth is disabled and a proxy request completes
- **THEN** the `request_logs` entry has `api_key_id = NULL`

#### Scenario: Bridge failure fan-out preserves per-request key attribution

- **GIVEN** an HTTP-bridge session holds a pending request authenticated with
  API key `key-123`
- **WHEN** the session fails its pending requests (upstream close, send
  failure, request timeout, or local terminal error)
- **THEN** the request's `request_logs` error entry has
  `api_key_id = "key-123"` even though the session-level failure path has no
  single key of its own

### Requirement: Frontend API Key management

The SPA settings page SHALL include an API Key management section with: a toggle for `apiKeyAuthEnabled`, a key list table showing prefix/name/models/limit/usage/expiry/status, a create dialog (name, model selection, assigned-account selection, usage sections multi-select, weekly limit, expiry date), and key actions (edit, delete, regenerate). On key creation, the SPA MUST display the plain key in a copy-able dialog with a warning that it will not be shown again, and the copy action MUST remain functional in secure and non-secure contexts.

The create and edit dialogs SHALL expose an `Apply to codex /model` checkbox directly below `Allowed models`. The checkbox SHALL default to unchecked for new keys and SHALL edit the stored API key value for existing keys.

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

### Requirement: Public OpenAI-compatible model list filtering

OpenAI-compatible model list endpoints SHALL filter models using a single predicate that requires both conditions:
1. `model.supported_in_api` is true
2. If `allowed_models` is configured, the model is in the allowed set

This predicate SHALL be applied consistently across `/api/models`, `/v1/models`, and the OpenAI-style `data` alias in `/backend-api/codex/models`. The Codex-native `models` catalog in `/backend-api/codex/models` SHALL also expose unsupported upstream models only when the model is a Codex shell-command model (`shell_type="shell_command"`); unsupported non-shell models SHALL remain hidden.

#### Scenario: Unsupported model excluded from /v1/models

- **WHEN** a model snapshot contains a model with `supported_in_api=false`
- **THEN** that model is not included in the `/v1/models` response

#### Scenario: Unsupported non-shell model excluded from /backend-api/codex/models

- **WHEN** a model snapshot contains a model with `supported_in_api=false`
- **AND** the model is not a Codex shell-command model
- **THEN** that model is not included in the `/backend-api/codex/models` response

#### Scenario: Unsupported Codex shell model included only in Codex-native catalog

- **WHEN** a model snapshot contains a model with `supported_in_api=false`
- **AND** the model has `shell_type="shell_command"`
- **THEN** that model is included in `/backend-api/codex/models.models`
- **AND** that model is not included in `/backend-api/codex/models.data`
- **AND** that model is not included in `/api/models` or `/v1/models`

#### Scenario: Allowed but unsupported model excluded

- **WHEN** a model is in the `allowed_models` set but has `supported_in_api=false`
- **AND** the model is not a Codex shell-command model
- **THEN** that model is not exposed in any model list endpoint

#### Scenario: gpt-5.3-codex aliases share availability gate consistently

- **WHEN** `gpt-5.3-codex` has `supported_in_api=false`
- **AND** `gpt-5.3-codex-spark` has `supported_in_api=true`
- **THEN** `/api/models`, `/v1/models`, and `/backend-api/codex/models.data`
      expose `gpt-5.3-codex-spark` but do not expose `gpt-5.3-codex`

#### Scenario: Consistent model set across endpoints

- **GIVEN** any model registry state
- **THEN** `/api/models`, `/v1/models`, and `/backend-api/codex/models.data` expose the same OpenAI-compatible set of models

### Requirement: Reservation 정산 exactly-once 보장

Usage reservation의 최종 정산(finalize 또는 release)은 요청 단위에서 정확히 1회 수행되어야 한다. 재시도 가능한 중간 attempt에서는 정산을 defer하고, 요청 종료 시점에서 단일 지점이 정산 책임을 갖는다. 시스템은 이 동작을 SHALL 보장해야 한다.

When an Images route owns a limited API-key reservation and cancellation
interrupts the first upstream SSE read, the system MUST close the upstream
iterator, MUST finish the route-owned release attempt despite active
cancellation, and MUST then propagate the original `CancelledError`. A failed
close or release MUST be logged and MUST NOT replace the original cancellation.
Stale reclamation MUST remain an exceptional backstop and MUST NOT substitute
for normal request-owned cleanup.

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

#### Scenario: Cancelled Images priming releases its route-owned reservation

- **GIVEN** a limited API key has created an Images route-owned reservation for
  `/v1/images/generations` or `/v1/images/edits`
- **AND** the internal Responses stream has no API-key reservation owner
- **WHEN** request cancellation interrupts the first upstream SSE read before
  any event is yielded
- **THEN** the upstream iterator is closed
- **AND** the Images route finishes releasing its reservation exactly once
  despite active cancellation
- **AND** the reservation reaches `released` state and its reserved quota is
  restored
- **AND** the original `CancelledError` propagates after cleanup completes
- **AND** stale-reservation reclamation is not required for that request

#### Scenario: Failed upstream close does not prevent reservation release

- **GIVEN** cancellation interrupts Images stream priming before the first
  upstream SSE event
- **WHEN** closing the upstream iterator fails but the route-owned reservation
  release succeeds
- **THEN** the proxy logs the close failure
- **AND** the Images route releases its reservation exactly once
- **AND** the reservation reaches `released` state and its reserved quota is
  restored
- **AND** the original `CancelledError` propagates unchanged
- **AND** stale-reservation reclamation is not required for that request

#### Scenario: Failed reservation release preserves the original cancellation

- **GIVEN** cancellation interrupts Images stream priming before the first
  upstream SSE event
- **WHEN** releasing the route-owned reservation fails
- **THEN** the proxy logs the release failure
- **AND** the original `CancelledError` propagates unchanged
- **AND** the still-reserved reservation remains eligible for stale reclamation

### Requirement: 조기 종료 경로에서 reservation release 보장

Reservation 생성 후 upstream API 호출에 진입하지 않고 종료되는 모든 경로에서 reservation이 release되어야 한다. `reserved` 상태로 남는 reservation이 존재하면 안 된다. 시스템은 이 동작을 SHALL 보장해야 한다.

After admission commits an owned reservation, rate-limit response-header
calculation before upstream work remains part of the early-exit cleanup window.
If that calculation fails, the system MUST attempt to release the owned
reservation exactly once before propagating the original header failure.

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

#### Scenario: Rate-limit header preparation fails after admission

- **GIVEN** a limited API key has committed an owned reservation for a
  streaming Responses, collected Responses, compact Responses, or audio
  transcription request
- **WHEN** rate-limit response-header calculation fails before upstream work
  begins
- **THEN** the reservation is released exactly once
- **AND** its reserved quota is restored
- **AND** the header failure propagates without starting upstream work

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

The system SHALL expose `GET /v1/usage` for self-service usage lookup by API-key clients. The route MUST require a valid API key in the `Authorization` header using the Bearer authentication scheme even when `api_key_auth_enabled` is false globally. The response MUST include only data for the authenticated key and explicitly visible aggregate upstream quota sections, and MUST return:

- `request_count`
- `total_tokens`
- `cached_input_tokens`
- `total_cost_usd`
- `limits[]` containing limits configured on the authenticated API key, with `limit_type`, `limit_window`, `max_value`, `current_value`, `remaining_value`, `model_filter`, `reset_at`, and `source`. When no API-key limits are configured and aggregate upstream quota details are visible to the caller, `limits[]` MAY mirror those aggregate upstream credit windows for legacy client compatibility.
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

#### Scenario: Upstream limits are mirrored for legacy clients without API-key limits

- **WHEN** an API key without its own limits calls `GET /v1/usage`
- **AND** upstream Codex aggregate usage data is visible to the key
- **THEN** `upstream_limits[]` contains the aggregate Codex credit windows
- **AND** `limits[]` contains the same aggregate Codex credit windows for legacy client compatibility

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

When `pooled_capacity_credits_primary` is 0.0 (e.g., all assigned accounts are free-tier), `pooled_remaining_percent_primary` SHALL be null. Primary samples whose `reset_at` has elapsed SHALL be pooled as reset windows rather than frozen values, and when no live (unexpired) primary sample exists across the pooled accounts, `pooled_remaining_percent_primary` SHALL be null instead of an optimistic value.

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

#### Scenario: Absent primary windows hide the pooled primary bar

- **WHEN** upstream stopped reporting the primary window and every pooled account's primary sample has an elapsed `reset_at` or no primary sample at all
- **THEN** `pooled_remaining_percent_primary` = null

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

### Requirement: API Keys Declare Traffic Class

API keys SHALL have a `traffic_class` value. The default SHALL be `foreground`. The system SHALL also accept `opportunistic` for clients that may only use burnable quota.

#### Scenario: Create opportunistic key
- **WHEN** admin creates an API key with `trafficClass: "opportunistic"`
- **THEN** the key is persisted and returned with `trafficClass: "opportunistic"`

#### Scenario: Omitted traffic class defaults to foreground
- **WHEN** admin creates an API key without `trafficClass`
- **THEN** the key is persisted and returned with `trafficClass: "foreground"`

### Requirement: Assigned-account quota badges reflect monthly-only free accounts

The API key create and edit dialogs SHALL display assigned-account quota badges according to the normalized quota model of each account.

#### Scenario: Free account shows monthly badge only
- **WHEN** assigned-account selection renders a free account whose normalized quota model is monthly-only
- **THEN** the dialog shows a `Monthly <percent>% left` badge for that account
- **AND** it does not show a weekly-left badge for that account

#### Scenario: Paid account retains 5h and 7d badges
- **WHEN** assigned-account selection renders an account with normalized 5h and 7d quota windows
- **THEN** the dialog shows `5h <percent>% left` and `7d <percent>% left` badges for that account

### Requirement: API keys can enforce extended reasoning efforts

The dashboard API key CRUD surface MUST allow callers to persist optional
enforced reasoning efforts advertised by the model catalog, including extended
GPT-5.6 efforts `max` and `ultra`.

#### Scenario: API key accepts extended enforced reasoning effort on create

- **WHEN** a dashboard client creates an API key with `enforcedReasoningEffort: "ultra"`
- **THEN** the request is accepted
- **AND** the response returns `enforcedReasoningEffort: "ultra"`

#### Scenario: API key accepts extended enforced reasoning effort on update

- **WHEN** a dashboard client updates an API key with `enforcedReasoningEffort: "max"`
- **THEN** the request is accepted
- **AND** the response returns `enforcedReasoningEffort: "max"`

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

### Requirement: API-key quota privacy toggle
The system SHALL provide a `hide_upstream_quota_from_api_keys` boolean in `DashboardSettings`, defaulting to `false`. The dashboard settings API SHALL accept and return this field.

#### Scenario: Default preserves current behavior

- **WHEN** the setting is not enabled
- **THEN** API-key-authenticated requests continue to receive upstream quota details exactly as they do today

#### Scenario: API-key usage response hides upstream limits

- **GIVEN** `hide_upstream_quota_from_api_keys` is `true`
- **WHEN** an API-key-authenticated client calls `GET /v1/usage`
- **THEN** the response SHALL omit upstream quota entries
- **AND** the response SHALL still include the API key's own quota data

#### Scenario: API-key usage response hides account pool usage

- **GIVEN** `hide_upstream_quota_from_api_keys` is `true`
- **AND** the API key's `usage_sections` includes `account_pool_usage`
- **WHEN** an API-key-authenticated client calls `GET /v1/usage`
- **THEN** the response SHALL set `account_pool_usage` to `null`
- **AND** the privacy toggle SHALL take precedence over the API key's `usage_sections`

#### Scenario: Proxy responses hide upstream quota headers

- **GIVEN** `hide_upstream_quota_from_api_keys` is `true`
- **WHEN** an API-key-authenticated client calls a protected proxy route that emits quota headers
- **THEN** the response SHALL NOT include `x-codex-primary-*`, `x-codex-secondary-*`, or `x-codex-credits-*` headers
- **AND** internal routing headers such as `x-codex-turn-state` SHALL remain unchanged

#### Scenario: Dashboard views stay visible

- **GIVEN** `hide_upstream_quota_from_api_keys` is `true`
- **WHEN** an owner views dashboard settings or owner-facing usage data without API-key authentication
- **THEN** upstream quota details SHALL remain visible

### Requirement: API keys can inspect and redeem reset credits within their account pool

The system SHALL expose `GET /v1/reset-credit` and `POST /v1/reset-credit` for API-key-authenticated self-service reset-credit access. Both routes MUST require a valid `Authorization: Bearer sk-clb-...` header even when `api_key_auth_enabled` is false globally. Validation failures MUST use the existing OpenAI error envelope used by `/v1/*` routes.

The target account pool SHALL be derived from the authenticated API key. If `account_assignment_scope_enabled=true`, only `assigned_account_ids` SHALL be eligible. If account scope is not enabled, all selectable accounts SHALL be eligible.

`GET /v1/reset-credit` SHALL return only credits for the authenticated key's eligible account pool. `POST /v1/reset-credit` SHALL reject requests whose `account_id` is outside that pool.

Before `POST /v1/reset-credit` decrypts and forwards the bearer token for the upstream consume call, the system SHALL refresh the target account with the normal account-token freshness rules and use the refreshed account credentials for the consume request.

If that self-service credential refresh fails, `POST /v1/reset-credit` SHALL stop before the upstream consume call, return a client-actionable conflict response, and keep using the existing `/v1/*` OpenAI error envelope.

After acquiring the cross-replica redeem claim, `POST /v1/reset-credit` SHALL re-validate the requested `redeem_id` against a live upstream reset-credits fetch performed inside the serialized redeem section using the refreshed account credentials, regardless of whether the replica-local snapshot lists the credit as available. It MUST NOT consume a credit solely on the basis of the replica-local snapshot, because a peer replica may have redeemed that credit while this request waited for the claim and the local snapshot can remain stale until its invalidation poll fires. The upstream fetch response is authoritative: if the credit is available upstream the redemption proceeds; otherwise the endpoint returns 409 and replaces the cached snapshot for that account with the fresh upstream snapshot.

On a successful `POST /v1/reset-credit` redemption, the system SHALL invalidate the redeemed account's cached reset-credit snapshot, force a usage refresh for that account, and invalidate account-selection cache state when that usage refresh writes updated usage. A failed or empty post-redeem usage refresh SHALL NOT roll back the successful credit redemption response.

#### Scenario: Missing API key is rejected

- **WHEN** a client calls `GET /v1/reset-credit` or `POST /v1/reset-credit` without a Bearer token
- **THEN** the system returns 401 in the OpenAI error format

#### Scenario: Invalid API key is rejected

- **WHEN** a client calls `GET /v1/reset-credit` or `POST /v1/reset-credit` with an unknown, expired, or inactive Bearer key
- **THEN** the system returns 401 in the OpenAI error format

#### Scenario: Scoped API key sees only assigned accounts

- **WHEN** an API key has account scope enabled with assigned accounts
- **AND** the client calls `GET /v1/reset-credit`
- **THEN** the response includes reset-credit entries only for those assigned accounts

#### Scenario: Unscoped API key can read the full selectable pool

- **WHEN** an API key has account scope disabled
- **AND** the client calls `GET /v1/reset-credit`
- **THEN** the response may include reset-credit entries for any selectable account that currently has an available cached credit

#### Scenario: Out-of-pool account is rejected on redeem

- **WHEN** a client calls `POST /v1/reset-credit` with an `account_id` outside the authenticated API key's eligible pool
- **THEN** the system returns 403 without redeeming any credit

#### Scenario: Self-service reset-credit works while global proxy auth is disabled

- **WHEN** `api_key_auth_enabled` is false and a client calls `GET /v1/reset-credit` or `POST /v1/reset-credit` with a valid Bearer key
- **THEN** the system still authenticates that key and applies the same account-pool rules

#### Scenario: Self-service redemption refreshes stale account credentials before consume

- **GIVEN** an eligible account has a redeemable reset credit
- **AND** the persisted access token for that account is stale but refreshable
- **WHEN** a client successfully calls `POST /v1/reset-credit` for that account
- **THEN** codex-lb refreshes the account before decrypting the consume bearer token
- **AND** the upstream reset-credit consume call uses the refreshed account credentials

#### Scenario: Self-service redemption surfaces refresh failures as conflicts

- **GIVEN** an eligible account has a redeemable reset credit
- **AND** that account's credential refresh fails before the upstream consume call
- **WHEN** a client calls `POST /v1/reset-credit` for that account
- **THEN** codex-lb returns a conflict response in the standard `/v1/*` OpenAI error envelope
- **AND** codex-lb does not call upstream reset-credit consume for that request

#### Scenario: Fresh replica redeems a credit missing from its local snapshot

- **GIVEN** a freshly started replica whose reset-credit snapshot store is empty for the target account
- **AND** upstream reports the requested `redeem_id` as available
- **WHEN** a client calls `POST /v1/reset-credit` for that account and `redeem_id`
- **THEN** the replica fetches the account's reset credits from upstream inside the serialized redeem section
- **AND** the redemption proceeds and succeeds instead of returning a false 409

#### Scenario: Credit already redeemed elsewhere returns 409 with a fresh snapshot

- **GIVEN** the replica-local snapshot does not list the requested `redeem_id` as available
- **AND** the authoritative upstream fetch reports that credit as unavailable
- **WHEN** a client calls `POST /v1/reset-credit` for that account and `redeem_id`
- **THEN** the endpoint returns 409 without calling upstream consume
- **AND** the fresh upstream snapshot replaces the replica's cached snapshot for that account

#### Scenario: Stale cached credit is re-validated after winning the claim

- **GIVEN** two replicas both cached the same reset credit as available
- **AND** replica A redeemed it while replica B waited on the cross-replica redeem claim
- **AND** replica B's cached snapshot still lists that `redeem_id` as available
- **WHEN** replica B wins the claim and processes `POST /v1/reset-credit` for that `redeem_id`
- **THEN** replica B performs the authoritative upstream fetch instead of consuming from its stale cache
- **AND** because upstream reports the credit unavailable, replica B returns 409 without sending a second upstream consume
- **AND** replica B replaces its cached snapshot with the fresh upstream snapshot

#### Scenario: Successful self-service redemption refreshes usage for immediate follow-up traffic

- **GIVEN** an eligible account has a redeemable reset credit and persisted usage/account state that still reflects a blocked window
- **WHEN** a client successfully calls `POST /v1/reset-credit` for that account
- **THEN** the redeemed account's cached reset-credit snapshot is invalidated
- **AND** codex-lb forces a usage refresh for that account before returning
- **AND** any account-selection cache entry derived from the stale usage state is invalidated when the refresh writes updated usage
- **AND** the response still returns the upstream `{code, windows_reset, redeemed_at}` success payload

### Requirement: API keys may be scoped to model sources

The system SHALL allow API keys to be scoped to zero or more model-source ids in
addition to existing account assignments and model allowlists. Source scoping
MUST be represented separately from account assignment scoping and MUST expose a
source-assignment-scope-enabled state in API-key read contracts. When an API key
has source assignment scope disabled, it MAY use any enabled source subject to
model allowlists and route eligibility. When source assignment scope is enabled,
source-routed requests and model listing MUST be restricted to the assigned
source ids.

#### Scenario: Key without source assignments can see enabled source models

- **GIVEN** an API key has no assigned source ids
- **AND** source assignment scope is disabled
- **AND** its model allowlist permits `local-coder`
- **WHEN** the key calls `GET /v1/models`
- **THEN** enabled `local-coder` source entries are eligible for listing

#### Scenario: Key with source assignments is restricted

- **GIVEN** an API key is assigned to source `src_a`
- **AND** source `src_b` also exposes model `local-coder`
- **WHEN** the key calls `GET /v1/models`
- **THEN** only entries from `src_a` are eligible

#### Scenario: Deleted assigned source does not broaden access

- **GIVEN** an API key is assigned to source `src_a`
- **AND** source `src_b` also exposes model `local-coder`
- **WHEN** `src_a` is deleted
- **THEN** the API key remains source-assignment scoped with no assigned source ids
- **AND** source `src_b` is not eligible for model listing or routing

### Requirement: Source-routed usage uses API-key reservations

The system MUST reserve API-key usage before forwarding an OpenAI-compatible
source-routed request authenticated by an API key, and MUST finalize the
reservation from the upstream OpenAI-compatible `usage` payload when the
request completes.
The finalized input, output, cached-input, and cost values MUST update the same
API-key limit and usage-reporting paths used by subscription-backed requests.
When cancellation interrupts source-routed `/v1/embeddings` upstream
forwarding after reservation creation, the request owner MUST finish releasing
that reservation exactly once despite active cancellation, and MUST then
propagate the original cancellation. Stale-reservation reclamation MUST remain
only a backstop and MUST NOT substitute for request-owned cancellation cleanup.

When cancellation interrupts source-routed `/v1/audio/transcriptions` upstream
forwarding after reservation creation, the request owner MUST finish a
cancellation-deferring release attempt before propagating the original
cancellation. If release persistence succeeds, the reservation MUST reach
`released` state exactly once and its reserved quota MUST be restored. If the
release attempt still fails after the existing bounded persistence retries,
the system MUST emit cancellation-neutral cleanup diagnostics, MUST propagate
the original cancellation, and MUST leave the reservation eligible for
stale-reservation reclamation to eventually release it and restore its quota.
Stale reclamation MUST remain only an exceptional backstop and MUST NOT
substitute for normal request-owned cancellation cleanup.

#### Scenario: Source-routed response finalizes token usage

- **WHEN** an API key calls a source-routed model and the upstream response
  includes `usage.prompt_tokens=100` and `usage.completion_tokens=20`
- **THEN** the API-key reservation is finalized with 100 input tokens and 20
  output tokens
- **AND** `/v1/usage` for that key reflects the completed usage

#### Scenario: Missing usage fails closed for limited keys

- **GIVEN** an API key has a token or cost limit
- **WHEN** a source-routed response succeeds but lacks usable OpenAI `usage`
  fields
- **THEN** the system does not silently finalize zero usage
- **AND** the request fails or is marked failed according to the source-routing
  error contract

#### Scenario: Cancelled source embeddings forwarding releases its reservation

- **GIVEN** a limited API key has created an owned reservation for a
  source-routed `/v1/embeddings` request
- **WHEN** cancellation interrupts the request while upstream embeddings
  forwarding is in flight
- **THEN** the request owner finishes releasing the reservation exactly once
  despite active cancellation
- **AND** the original cancellation propagates after cleanup completes
- **AND** stale-reservation reclamation is not required for that request

#### Scenario: Cancelled source audio forwarding releases its reservation

- **GIVEN** a limited API key has created an owned reservation for a
  source-routed `/v1/audio/transcriptions` request
- **WHEN** cancellation interrupts the request while upstream audio forwarding
  is in flight
- **THEN** the request owner finishes releasing the reservation exactly once
  despite active cancellation
- **AND** the reservation reaches `released` state and its reserved quota is
  restored
- **AND** the original cancellation propagates after cleanup completes
- **AND** stale-reservation reclamation is not required for that request

#### Scenario: Failed cancellation release remains recoverable

- **GIVEN** cancellation interrupts a limited source-routed audio request after
  its reservation is created
- **AND** the immediate release attempt exhausts the existing bounded
  persistence retries
- **WHEN** release persistence reports failure
- **THEN** the proxy emits cancellation-neutral cleanup diagnostics
- **AND** the original cancellation propagates
- **AND** stale-reservation reclamation eventually releases the reservation and
  restores its reserved quota

### Requirement: Stream reservation settlement is detached from the response path

Settling a stream API-key reservation MUST NOT block the response/stream close,
with deliberate ordering exceptions for account-health writes. When a keyed
websocket stream terminates with an account-health error, when a keyed HTTP SSE
failure is rewritten to `previous_response_owner_unavailable`, or when a keyed
HTTP SSE terminal queue drains empty before terminal health or success is
recorded, the finalizer MUST wait for settlement to commit before the
load-balancer health write (the settlement-ordering invariant). If the primary
settlement fails, the finalizer MUST wait for fallback release to commit before
recording account health. If neither operation confirms settlement, the
account-health write MUST remain unapplied. Tracked persistence ownership MUST
remain registered through an ordering-sensitive fallback release, including
cancellation before the primary coroutine starts or during that release, so
graceful shutdown drains both phases. When the existing stream-retry path
deliberately defers an
account-health penalty until the same ordering-sensitive settlement, it MUST
likewise apply neither that penalty nor an immediately following terminal health
write unless settlement is confirmed, and it MUST NOT start a second settlement
for the transferred reservation. The HTTP bridge's pre-created retry handling
(model-capacity wait, owner-pinned quota, and generic retryable pre-created
failures) MUST likewise defer a keyed request's classified account-health
write until that request's reservation settles or its fallback release
commits, MUST leave the deferred write unapplied when neither confirms, and
MUST keep the immediate write for unkeyed requests. After a committed
settlement or fallback release, deferred account-backoff writes and deferred
stream-health writes MUST drain on independent lanes so a failure in one
cannot orphan the other, and a deferred health write that itself fails MUST be
logged and dropped without aborting the remaining terminal finalization. In
all other cases the settlement MUST run as
a tracked background task; when it fails or is cancelled, the reservation MUST
still be released by the tracking fallback, and the request's finalization path
MUST NOT double-release a transferred settlement. If the tracking fallback
itself encounters a persistence failure, it MUST remain tracked and retry the
idempotent release; no more than four retry-enabled detached fallback repository
attempts may run concurrently per proxy service instance, waiting fallbacks MUST
NOT open repository sessions until admitted, and persistence drain MUST NOT
report completion while any retry remains unfinished. Reservations MUST continue
to count toward key limits until finalized or released, so deferred settlement
can never admit usage a synchronous settlement would have rejected.

#### Scenario: Response close precedes settlement completion

- **GIVEN** a keyed stream whose settlement transaction is still running
- **WHEN** the stream closes
- **THEN** the close does not wait for the settlement
- **AND** the settlement finalizes the reservation exactly once in the background

#### Scenario: Failed detached settlement still releases the reservation

- **GIVEN** a detached settlement whose finalize raises
- **WHEN** the settlement task completes
- **THEN** the tracking fallback releases the reservation

#### Scenario: Failed fallback release remains tracked

- **GIVEN** a detached settlement whose finalize raises
- **AND** the first tracking-fallback release attempt also raises
- **WHEN** persistence recovers before the drain deadline
- **THEN** the tracked fallback retries and releases the reservation exactly once
- **AND** persistence drain does not report completion before that release

#### Scenario: Concurrent fallback release retries are bounded to four

- **GIVEN** five failed detached settlements in one proxy service instance
- **WHEN** their tracking fallbacks attempt repository persistence concurrently
- **THEN** no more than four release attempts open repository sessions
- **AND** the waiting fallbacks remain tracked until they can retry

#### Scenario: Websocket health-error settlement precedes the health write

- **GIVEN** a keyed websocket stream that terminates with an account-health error
- **WHEN** the finalizer settles the reservation
- **THEN** it waits for the settlement to commit before recording the account-health error

#### Scenario: Websocket health waits for fallback settlement

- **GIVEN** a keyed websocket stream that terminates with an account-health error
- **AND** its primary settlement fails
- **WHEN** fallback release remains in progress
- **THEN** the finalizer does not record the account-health error
- **AND** it records the error only after fallback release commits

#### Scenario: Unconfirmed websocket settlement leaves health unapplied

- **GIVEN** a keyed websocket stream that terminates with an account-health error
- **WHEN** both primary settlement and fallback release fail
- **THEN** the finalizer does not record the account-health error
- **AND** the upstream connection is still scheduled for reconnect and retirement

#### Scenario: Owner-unavailable rewrite settles before health

- **GIVEN** a keyed HTTP SSE stream rewrites an upstream failure to
  `previous_response_owner_unavailable`
- **WHEN** the stream records account-health recovery for the original failure
- **THEN** reservation settlement or fail-safe release confirms first

#### Scenario: Empty terminal queue settles before health or success

- **GIVEN** a keyed HTTP SSE bridge queue drains empty on a terminal path
- **WHEN** the stream records terminal account health or success
- **THEN** reservation settlement or fail-safe release confirms first

#### Scenario: Unconfirmed HTTP stream settlement leaves health unapplied

- **GIVEN** a keyed HTTP SSE stream reaches an ordering-sensitive terminal path
- **WHEN** both primary settlement and fallback release fail
- **THEN** the stream does not record account health

#### Scenario: Unconfirmed retry settlement drops deferred health

- **GIVEN** a keyed stream retry has deferred an account-health penalty until replacement selection
- **WHEN** neither primary settlement nor fallback release confirms settlement
- **THEN** the deferred penalty and any immediately following terminal health write remain unapplied
- **AND** the retry path does not start a second settlement for the transferred reservation

#### Scenario: Keyed pre-created retry defers the health write

- **GIVEN** a keyed HTTP-bridge request whose reservation is unsettled
- **WHEN** a retryable pre-created failure (model capacity, owner-pinned quota, or another retryable error) is handled
- **THEN** no load-balancer health write occurs before settlement
- **AND** the classified penalty is queued on the request state
- **AND** an equivalent unkeyed request keeps the immediate health write

#### Scenario: Deferred pre-created penalty applies after settlement commits

- **GIVEN** a keyed HTTP-bridge request with a queued pre-created health penalty
- **WHEN** its reservation settlement or fallback release commits
- **THEN** the queued penalty is applied after the commit
- **AND** each queued entry is applied exactly once

#### Scenario: Failed deferred health write does not abort finalization

- **GIVEN** a committed settlement with a queued pre-created health penalty
- **WHEN** the deferred health write fails
- **THEN** the failure is logged and the penalty is dropped
- **AND** the remaining terminal finalization continues
- **AND** a deferred account-backoff failure does not prevent the deferred health drain

#### Scenario: Shutdown drains pending settlements

- **WHEN** the service shuts down gracefully with settlements in flight
- **THEN** shutdown waits for them up to the configured drain timeout
- **AND** a pending ordering-sensitive fallback release remains part of that drain despite cancellation before primary startup or during fallback
- **AND** reports an incomplete drain if a tracked settlement or release remains unfinished at that timeout

### Requirement: Untrusted forwarded headers do not grant unauthenticated proxy locality

When API-key authentication is disabled and proxy-header trust is enabled, forwarded client-IP headers from a socket peer outside every configured trusted-proxy CIDR MUST NOT cause a protected proxy request to be classified as local. Such a request MUST remain blocked unless its raw socket peer independently matches `proxy_unauthenticated_client_cidrs`.

#### Scenario: Untrusted loopback proxy remains blocked

- **WHEN** API-key authentication is disabled
- **AND** proxy-header trust is enabled
- **AND** the raw loopback socket peer is outside every configured trusted-proxy CIDR
- **AND** a forwarded client-IP header is present
- **AND** the raw socket peer is outside `proxy_unauthenticated_client_cidrs`
- **THEN** the protected proxy request is rejected with HTTP 401

#### Scenario: Explicit raw-socket allowlist remains authoritative

- **WHEN** the raw socket peer belongs to `proxy_unauthenticated_client_cidrs`
- **THEN** the protected proxy request may proceed without API-key authentication
- **AND** forwarded header contents do not determine that allowlist match

### Requirement: Direct-local proxy access inspects every forwarded client hint field

When API-key authentication and proxy-header trust are disabled, a loopback socket peer MUST qualify for direct-local protected proxy access only when no non-empty forwarded client-IP field value is present. The system MUST inspect every repeated field value; a later non-empty value MUST keep the request blocked unless the raw socket peer independently matches `proxy_unauthenticated_client_cidrs`.

#### Scenario: Later duplicate forwarded hint remains unauthorized

- **WHEN** API-key authentication and proxy-header trust are disabled
- **AND** a loopback request contains an empty `X-Forwarded-For` field followed by a non-empty `X-Forwarded-For` field
- **AND** the raw socket peer is outside `proxy_unauthenticated_client_cidrs`
- **THEN** the protected proxy request is rejected with HTTP 401

### Requirement: GPT-5.6 usage cost pricing matches the current published rates

When computing new API-key usage, request-log, reservation, or aggregate cost for canonical GPT-5.6 models, the system MUST use the validated active pricing catalog and offline fallbacks specified by the upstream-metadata capability. Existing non-NULL historical request costs MUST remain unchanged when the active price catalog changes.

The `priority` and `fast` service-tier aliases MUST use the catalog's Priority rates. Long-context rates MUST apply only above the catalog's explicit input-token threshold. Explicit tier-specific long-context prices MUST take precedence over legacy Flex multipliers. Model aliases with a version or snapshot suffix MUST resolve to the corresponding canonical price entry; bare `gpt-5.6` MUST resolve to Sol.

Batch rates and cache-write rates MUST NOT be used without corresponding proxy request and usage fields.

#### Scenario: Sol uses a refreshed rate
- **GIVEN** the active catalog specifies Sol standard input/cached/output rates of `4 / 0.4 / 20` USD per million tokens
- **WHEN** a standard-tier `gpt-5.6-sol` request uses 200,000 input and 1,000,000 output tokens without cached input
- **THEN** its token cost is `$20.80`

#### Scenario: Terra standard usage uses the current rate
- **GIVEN** the active catalog specifies Terra standard input/output rates of `2 / 12` USD per million tokens
- **WHEN** a standard-tier `gpt-5.6-terra` request has 200,000 input tokens and 1,000,000 output tokens without cached input
- **THEN** the token cost is `$12.40`

#### Scenario: Luna Fast and Flex usage use their tier rates
- **GIVEN** the active catalog specifies Luna Priority input/cached/output rates of `0.4 / 0.04 / 2.4` and Flex rates of `0.1 / 0.01 / 0.6` USD per million tokens
- **WHEN** a `gpt-5.6-luna` request has 200,000 input tokens, 100,000 cached input tokens, and 1,000,000 output tokens
- **AND** the request uses `priority` or `fast`
- **THEN** the token cost is `$2.444`
- **WHEN** the same usage uses `flex`
- **THEN** the token cost is `$0.611`

#### Scenario: Terra standard long-context usage uses the current long-context rate
- **GIVEN** the active catalog specifies Terra long-context input/cached/output rates of `4 / 0.4 / 18` USD per million tokens above 272,000 input tokens
- **WHEN** a standard-tier `gpt-5.6-terra` request has 300,000 input tokens, 50,000 cached input tokens, and 100,000 output tokens
- **THEN** the token cost is `$2.82`

#### Scenario: Versioned aliases use canonical GPT-5.6 pricing
- **WHEN** the requested model is `gpt-5.6-luna-2026-07-13`
- **THEN** cost accounting resolves it to the `gpt-5.6-luna` price entry

### Requirement: API key last-used tracking is write-behind and coalesced

The system SHALL track `api_keys.last_used_at` through a process-local write-behind coalescer instead of writing the column inside each reservation-settlement transaction. Settlement paths MUST record the key's used-at timestamp in memory (keyed by API key id, keeping the per-key maximum), and a replica-local periodic flusher (constant 30-second interval, not leader-gated) MUST fold all pending touches into the database in a single transaction per flush. Every flushed write MUST apply monotonic greatest-wins semantics — the stored `last_used_at` is only advanced, never regressed, even when multiple replicas flush out of order (`GREATEST(coalesce(last_used_at, epoch), :new)` semantics; the dialect-portable guarded UPDATE `WHERE last_used_at IS NULL OR last_used_at < :new` is an acceptable implementation on both PostgreSQL and SQLite). Graceful shutdown MUST flush every recorded touch: the flusher's stop sequence MUST switch the coalescer to shutdown write-through mode before performing the final flush, so a touch recorded after (or concurrently with) the final flush — for example by a settlement task that outlived the shutdown drain of persistence tasks — is flushed immediately by the recording path itself instead of being parked in a pending map that no longer has a flusher. Shutdown-path flushes (the final flush and write-through flushes after it) MUST retry transient failures a bounded number of times (3 attempts with a short constant backoff); if every attempt fails, the pending touches (API key ids and their timestamps) MUST be logged at WARNING so operators can reconstruct the lost values, and the failure MUST NOT propagate to the caller. On process crash, losing at most one flush interval (~30 seconds) of `last_used_at` freshness is accepted: the column's only consumer is the dashboard API response field (`lastUsedAt`), which no routing, ordering, or enforcement logic reads, so observed staleness of up to the flush interval is a display-only effect. A failed periodic flush MUST retain the pending touches for a later flush rather than dropping them.

#### Scenario: Many settlements within one interval flush as one write per key

- **GIVEN** an API key that settles many requests within one flush interval
- **WHEN** the periodic flush runs
- **THEN** the key receives exactly one `last_used_at` write carrying the latest recorded used-at timestamp
- **AND** none of the individual settlement transactions wrote `last_used_at`

#### Scenario: Flush never moves last_used_at backwards

- **GIVEN** a stored `last_used_at` newer than a pending recorded timestamp (for example another replica already flushed a later touch)
- **WHEN** the flush applies the pending timestamp
- **THEN** the stored `last_used_at` keeps the newer value

#### Scenario: Graceful shutdown flushes pending touches

- **GIVEN** recorded touches that have not yet been flushed
- **WHEN** the application shuts down gracefully
- **THEN** the pending touches are flushed to the database before the process exits

#### Scenario: Failed flush retains pending touches

- **GIVEN** a flush attempt that fails (for example a transient database error)
- **WHEN** the next flush tick runs
- **THEN** the previously pending touches are flushed, merged with any touches recorded in between (per-key maximum wins)

#### Scenario: Shutdown final flush retries a transient failure

- **GIVEN** pending touches and a database that fails the first final-flush attempt with a transient error
- **WHEN** the application shuts down gracefully
- **THEN** the final flush is retried after a short backoff and the touches are persisted before the process exits

#### Scenario: Shutdown final flush exhausts its retries

- **GIVEN** pending touches and a database that fails every final-flush attempt
- **WHEN** the bounded retries are exhausted
- **THEN** a WARNING is logged containing the pending API key ids and their timestamps
- **AND** shutdown proceeds without raising

#### Scenario: Touch recorded after the shutdown flush writes through

- **GIVEN** a settlement task that outlived the shutdown drain of persistence tasks
- **WHEN** it records a touch after the flusher has stopped and performed its final flush
- **THEN** the touch is flushed to the database immediately by the recording path rather than being lost at process exit

### Requirement: GPT-5.6 personality pricing is recognized

The system MUST recognize `gpt-5.6`, `gpt-5.6-sol`, `gpt-5.6-terra`, and `gpt-5.6-luna` when computing request costs. The bare `gpt-5.6` alias MUST resolve to Sol, and suffixed aliases for each personality model MUST resolve to the matching canonical pricing entry. Standard, Flex, Priority, and requests with more than 272K input tokens MUST use the published rates applicable to the model and tier.

#### Scenario: Canonical GPT-5.6 models use personality-specific pricing

- **WHEN** a standard-tier request completes for `gpt-5.6-sol`, `gpt-5.6-terra`, or `gpt-5.6-luna`
- **THEN** the system computes cost using that model's standard input, cached-input, and output rates

#### Scenario: Bare GPT-5.6 alias resolves to Sol pricing

- **WHEN** a request completes for `gpt-5.6`
- **THEN** the system resolves it to the canonical Sol pricing entry
- **AND** the system does not use the generic `gpt-5` pricing entry

#### Scenario: Suffixed GPT-5.6 model resolves to its personality price

- **WHEN** a request completes for a suffixed GPT-5.6 personality model ID
- **THEN** the system resolves it to the matching canonical Sol, Terra, or Luna pricing entry
- **AND** the system does not use the generic `gpt-5` pricing entry

#### Scenario: GPT-5.6 service tiers use published tier rates

- **WHEN** a GPT-5.6 request completes with `service_tier: "flex"` or `service_tier: "priority"`
- **THEN** the system computes cost using the published rates for that model and service tier

#### Scenario: GPT-5.6 long-context request uses published uplift

- **WHEN** a standard-tier or Flex GPT-5.6 request completes with more than 272K input tokens
- **THEN** the system computes cost using the published long-context input, cached-input, and output rates for that model and tier

### Requirement: API-key limit rule identities are unique

The system SHALL reject an API-key create or update payload when it contains
more than one limit rule with the same `(limit_type, limit_window,
model_filter)` identity. Rejection MUST use the typed API-key validation error
and MUST occur before a create request persists an API key or limit row.
The validation message MUST identify the duplicate rule identity.

#### Scenario: Duplicate rules are rejected during creation

- **WHEN** an administrator submits `POST /api/api-keys` with two limit rules
  sharing the same type, window, and model filter
- **THEN** the API returns `400` with `invalid_api_key_payload`
- **AND** no API key or limit row is persisted

### Requirement: Stale usage-reservation reclamation enforces a hard age ceiling

Stale usage-reservation reclamation MUST reclaim `reserved` reservations whose
age exceeds a hard ceiling on creation time regardless of how recently their
`updated_at` was refreshed. This is the backstop for orphaned reservation
heartbeats: a leaked heartbeat task keeps touching `updated_at`, which would
otherwise exempt its reservation from the heartbeat-based staleness cutoff
forever. The ceiling MUST be far larger than any legitimate request lifetime
so it can never reclaim an in-flight reservation, and reclamation past the
ceiling MUST restore the reserved quota the same way heartbeat-based
reclamation does.

#### Scenario: Orphaned heartbeat cannot exempt a reservation forever

- **GIVEN** a `reserved` usage reservation created before the hard age ceiling
- **AND** a leaked heartbeat keeps refreshing its `updated_at`
- **WHEN** stale usage-reservation reclamation runs
- **THEN** the reservation is released and its reserved quota is restored

#### Scenario: Fresh reservations are untouched by the ceiling

- **GIVEN** a `reserved` usage reservation created within the hard age ceiling
- **AND** its `updated_at` is current
- **WHEN** stale usage-reservation reclamation runs
- **THEN** the reservation stays `reserved`

### Requirement: API keys can enforce the Ultrafast service tier

The dashboard API key CRUD surface MUST accept and persist `ultrafast` as a canonical enforced service tier. The service MUST return the same canonical value and MUST NOT normalize it to `priority`.

#### Scenario: Create an API key with Ultrafast enforcement

- **WHEN** a dashboard client creates an API key with `enforcedServiceTier: "ultrafast"`
- **THEN** the request is accepted
- **AND** the persisted and returned enforced service tier is `ultrafast`

#### Scenario: Enforce Ultrafast on an advertising model

- **GIVEN** an account model advertises the `ultrafast` service tier
- **WHEN** a request uses an API key whose enforced service tier is `ultrafast`
- **THEN** the upstream request carries `service_tier: "ultrafast"`

### Requirement: Required-capability header authenticates through the existing proxy API-key dependency

Whenever a protected proxy request carries one or more `X-Codex-LB-Required-Capability` values, the existing `validate_proxy_api_key` Security dependency MUST require a valid proxy API key before the handler runs, even when `api_key_auth_enabled` is false and the caller would otherwise qualify as local or CIDR-allowlisted. Headerless requests MUST retain the existing global-switch behavior. The capability header MUST NOT introduce a second FastAPI authentication dependency identity for ordinary proxy routes.

#### Scenario: Capability header requires a key while global auth is disabled

- **WHEN** `api_key_auth_enabled` is false
- **AND** a local or CIDR-allowlisted client sends a protected proxy request with `X-Codex-LB-Required-Capability`
- **THEN** ingress requires a valid proxy API key
- **AND** a missing or invalid key is rejected with the existing `401 invalid_api_key` error

#### Scenario: Headerless requests keep the global authentication switch

- **WHEN** `api_key_auth_enabled` is false
- **AND** a local or CIDR-allowlisted client sends a protected proxy request without `X-Codex-LB-Required-Capability`
- **THEN** the request proceeds without a new per-request API-key requirement

### Requirement: Disconnect cleanup settles source-chat reservations

When a source-chat request is cancelled or its streaming body is closed, the proxy MUST close the upstream iterator, release its API-key reservation, and write or explicitly abort the source request-log row despite repeated cancellation delivery.

#### Scenario: Client disconnects during source stream

- **WHEN** the downstream client disconnects before source-stream completion
- **THEN** the reservation is released and the source request is logged as an aborted/error request.

### Requirement: Limit-free admissions skip the reservation ledger

When API-key admission finds no applicable limit for a request (the key has no configured limits, or none of its limits apply to the request model), the system MUST NOT create a usage reservation row and MUST NOT run the reservation commit for that request. Admission MUST report that no reservation exists, and every downstream reservation consumer (stream and compact settlement, release paths, heartbeat touch, quota-planner warmup finalization) MUST treat the missing reservation as "nothing to settle" and no-op without error. Admission-time validity checks (key active, key not expired, lazy expired-limit reset) MUST still run unchanged. Because settlement — which records the key's last-used touch for reserved requests — never runs without a reservation, admission MUST record the last-used touch itself on the limit-free path so `last_used_at` continues to advance for these keys. Admission MUST also close the read transaction it opened before returning without a reservation, and MUST do so without expiring ORM state tracked by a caller-shared session (callers such as the quota-planner warmup service hold already-loaded rows on the same session and access them after admission). Keys with at least one applicable limit MUST continue to create reservations with per-limit items (including zero-delta items) and full commit durability.

#### Scenario: Key without limits creates no reservation

- **WHEN** admission runs for an API key with no configured limits
- **THEN** no usage reservation row is inserted and no reservation write is committed (the only commit issued closes the read-only admission transaction)
- **AND** the request is admitted without a reservation

#### Scenario: Key whose limits do not apply to the request model creates no reservation

- **WHEN** admission runs for a key whose limits all carry a `model_filter` that does not match the request model
- **THEN** no usage reservation row is inserted
- **AND** the non-matching limits' `current_value` values are unchanged

#### Scenario: Limit-free admissions still advance last-used

- **WHEN** admission runs for a key with no applicable limits
- **THEN** the key's last-used touch is recorded at admission via the write-behind coalescer
- **AND** the dashboard-visible `last_used_at` continues to advance for the key

#### Scenario: Settlement, release, and heartbeat no-op without a reservation

- **WHEN** a request admitted without a reservation finishes (success or failure)
- **THEN** settlement, release, and heartbeat-touch paths skip without error
- **AND** no settlement transaction runs for that request

#### Scenario: Quota-planner warmup probes without a reservation

- **WHEN** the quota-planner warmup executor admits its probe with a key that has no applicable limits
- **THEN** the warmup probe executes
- **AND** no reservation finalization is attempted

#### Scenario: Limit-free admission preserves shared-session ORM state

- **WHEN** a caller that holds already-loaded ORM rows on the same session (the quota-planner warmup service tracks the target account and decision) admits a request with a limit-free key
- **THEN** the admission read transaction is closed before admission returns
- **AND** the caller's tracked rows remain readable afterwards without reload errors, so the warmup probe executes

#### Scenario: Stale-reservation reclamation sees no rows for limit-free admissions

- **WHEN** stale usage-reservation reclamation runs after admissions for keys without applicable limits
- **THEN** those admissions contribute no reservations to reclaim

#### Scenario: Limited keys are unaffected

- **WHEN** admission runs for a key with an applicable limit
- **THEN** a reservation with per-limit items is created and committed exactly as before admission returned reservations unconditionally

### Requirement: API keys can restrict client-selected reasoning efforts

The dashboard API-key create, update, list, and response surfaces SHALL expose
an optional `allowedReasoningEfforts` list. When absent or `null`, the API key
MUST retain unrestricted reasoning-effort behavior. When present, the list
MUST be non-empty and consist only of the supported client-plane efforts
`minimal`, `low`, `medium`, `high`, `xhigh`, `max`, and `ultra`. The service
MUST trim, case-normalize, de-duplicate, and return entries in canonical
catalog order.

`allowedReasoningEfforts` MUST be mutually exclusive with
`enforcedReasoningEffort`. Create and PATCH requests MUST validate the
effective persisted state, including an unchanged counterpart field. Existing
API keys whose persisted allowlist is null MUST remain unrestricted.
The persistence layer MUST reject a row that contains both an allowlist and a
fixed reasoning effort.
If legacy or manually edited storage contains a malformed non-null allowlist,
the service MUST remain fail-closed for explicit efforts and the dashboard MUST
NOT clear that sentinel during an unrelated edit. A concurrent update that
loses the mutual-exclusion constraint race MUST return the normal dashboard
validation error instead of an internal server error.

#### Scenario: Create an effort-selectable key

- **WHEN** an administrator creates an API key with
  `allowedReasoningEfforts: ["XHIGH", "low", "high", "low"]`
- **THEN** the response returns `allowedReasoningEfforts` as
  `["low", "high", "xhigh"]`
- **AND** `enforcedReasoningEffort` is null

#### Scenario: Reject an empty allowlist

- **WHEN** an administrator creates or updates an API key with
  `allowedReasoningEfforts: []`
- **THEN** the dashboard API returns 400
- **AND** the API key is not changed

#### Scenario: Reject conflicting reasoning policies on update

- **GIVEN** an API key has `enforcedReasoningEffort: "low"`
- **WHEN** an administrator updates only `allowedReasoningEfforts` to
  `["low", "medium"]`
- **THEN** the dashboard API returns 400
- **AND** the existing fixed effort remains unchanged

#### Scenario: Existing key remains unrestricted

- **GIVEN** an API key created before `allowedReasoningEfforts` existed
- **WHEN** it is read or used without that field configured
- **THEN** its response contains `allowedReasoningEfforts: null`
- **AND** no reasoning-effort allowlist is applied

#### Scenario: Unrelated edit preserves a malformed fail-closed policy

- **GIVEN** an API key exposes an empty allowlist sentinel for malformed stored
  policy data
- **WHEN** an administrator changes only its name
- **THEN** the dashboard update omits `allowedReasoningEfforts`
- **AND** the malformed persisted policy is not replaced with null

#### Scenario: Concurrent policy conflict returns a validation error

- **GIVEN** concurrent updates try to set a fixed effort and an allowlist on
  the same unrestricted key
- **WHEN** the database mutual-exclusion constraint rejects the losing update
- **THEN** the dashboard API returns its normal invalid API-key payload error

### Requirement: Dashboard manages selectable reasoning efforts

The API-key create and edit dialogs SHALL present the supported reasoning
efforts as an accessible multi-select when no fixed effort is selected. The UI
MUST represent no selected values as `null`, not an empty allowlist. When an
administrator selects a fixed effort, the UI MUST clear and disable the
allowlist; when it selects one or more allowlist values, it MUST clear the
fixed-effort selection.

#### Scenario: Configure all normal efforts without max or ultra

- **WHEN** an administrator selects `minimal`, `low`, `medium`, `high`, and
  `xhigh` in the API-key dialog
- **THEN** the saved key returns exactly those five allowed efforts
- **AND** the dialog does not show `max` or `ultra` as selected

### Requirement: Keyed stream mid-loop failover settles before account-health writes

When an HTTP SSE Responses stream holds an API-key usage reservation, mid-loop failover account-health writes for a failed account MUST NOT run while that reservation remains unsettled. The stream MUST keep the same reservation across the internal failover, MUST defer the failed account's health write until settlement is confirmed, and MUST NOT acquire a second reservation solely for that failover. If primary settlement fails but fail-safe release confirms, the stream MAY flush deferred health after that confirmed release when ordered settle never ran. If neither settlement nor fail-safe release confirms, deferred health MUST stay unapplied. After settlement ownership transfers from the request and both ordered settlement and its immediate fail-safe release fail, tracked persistence cleanup MUST retry reservation release while the request path keeps deferred health unapplied. Cancellation observed while an immediate fail-safe release is still running MUST NOT start a retrying release after that fallback confirms, and cleanup MAY flush deferred health after that confirmed release. After settlement commits, the stream MUST record that settled state before awaiting deferred health flush so a cancellation that arrives during the flush cannot skip retained deferred penalties. Deferred health flush MUST consume one queued entry at a time and MUST retain later entries when one write fails or cancellation interrupts an await. Deferred health flush MUST complete each queued entry under cancellation-deferred ownership so a cancel mid-write cannot replay the same health operation and double-count errors. After settlement or release confirms, a deferred route-backoff failure MUST NOT prevent independent queued stream-health penalties from being attempted. A detached cancel-safe deferred-health flush MUST be tracked as persistence work and graceful shutdown MUST await it within the configured persistence-drain budget. After cancellation, cleanup MUST attempt to settle or release the reservation. Cleanup MUST flush deferred health before it finishes only after settlement or release is confirmed. If neither operation confirms, deferred health MUST remain unapplied.

#### Scenario: Keyed refresh/connect failover defers health until settle

- **GIVEN** a keyed HTTP SSE Responses stream with a held API-key reservation
- **AND** the first account fails a retryable freshness/connect transport error
- **WHEN** a later account completes and settlement runs
- **THEN** `_handle_stream_error` for the failed account runs only after that settlement
- **AND** the request does not acquire another reservation

#### Scenario: Keyed transient exhaustion defers health until settle

- **GIVEN** a keyed HTTP SSE Responses stream with a held API-key reservation
- **AND** the first account exhausts same-account transient stream retries
- **WHEN** a later account completes and settlement runs
- **THEN** `_handle_stream_error` and extra `record_errors` for the failed account run only after that settlement

#### Scenario: Streaming Responses route preserves settle-before-health

- **GIVEN** a keyed request admitted through the streaming `/v1/responses` entry point
- **AND** mid-loop keyed failover queues a deferred account-health penalty
- **WHEN** the replacement account completes
- **THEN** reservation settlement commits before the deferred health write

#### Scenario: Cancel after queued mid-loop penalty still flushes health

- **GIVEN** a keyed stream that queued a deferred mid-loop health penalty
- **WHEN** the request is cancelled before the replacement settles
- **AND** cleanup confirms settlement or fail-safe release
- **THEN** the deferred health write still runs

#### Scenario: Cancel during deferred health flush still applies the penalty

- **GIVEN** a keyed stream whose settlement already committed
- **AND** deferred health flush is awaiting an account-health write
- **WHEN** the request is cancelled during that await
- **THEN** the deferred health write still completes for the failed account

#### Scenario: Cancel mid deferred health write does not double-count

- **GIVEN** a keyed stream whose settlement already committed
- **AND** deferred health flush has applied in-memory health for a queued entry
- **AND** the flush is still awaiting persistence or extra `record_errors`
- **WHEN** the request is cancelled during that await
- **THEN** cleanup MUST NOT replay the same queued entry
- **AND** `_handle_stream_error` and extra `record_errors` for that entry apply exactly once

#### Scenario: Deferred health flush keeps later entries after one failure

- **GIVEN** a keyed stream that deferred health for more than one failed account
- **WHEN** the first deferred health write raises
- **THEN** later deferred health writes are still attempted

#### Scenario: Unconfirmed settlement keeps deferred health unapplied

- **GIVEN** a keyed stream that deferred a mid-loop health penalty
- **WHEN** neither primary settlement nor fail-safe release confirms settlement
- **THEN** the deferred health write does not run

#### Scenario: Failed ordered settlement transfers release retry ownership

- **GIVEN** keyed mid-loop failover transferred reservation settlement ownership from the request
- **WHEN** ordered settlement and its immediate fail-safe release both fail
- **THEN** tracked persistence cleanup retries reservation release
- **AND** the request path does not apply the deferred account-health write

#### Scenario: Cancelled fallback does not retry a confirmed release

- **GIVEN** ordering-sensitive settlement transferred reservation ownership from the request
- **AND** the immediate fail-safe release confirms after the primary attempt is cancelled
- **WHEN** cancellation is observed while that fallback is still running
- **THEN** tracked persistence cleanup MUST NOT start a retrying release
- **AND** deferred health MAY flush after that confirmed release

#### Scenario: Shutdown drains detached deferred health

- **GIVEN** cancellation leaves a post-settlement account-health penalty for cancel-safe background flush
- **WHEN** graceful shutdown drains persistence tasks
- **THEN** the drain waits for that deferred health flush within its configured timeout

#### Scenario: Deferred route-backoff failure preserves queued stream health

- **GIVEN** confirmed settlement or release with a deferred route backoff and an independent queued stream-health penalty
- **WHEN** the deferred route-backoff write fails
- **THEN** cleanup still attempts the queued stream-health penalty

#### Scenario: Retried backoff failure preserves later cancelled-flush entries

- **GIVEN** confirmed settlement with a retained route backoff and multiple queued stream-health penalties
- **AND** cancellation interrupts the flush after its current queued penalty completes
- **WHEN** final cleanup retries the route backoff and that write fails again
- **THEN** cleanup tracks and flushes the later queued stream-health penalties

### Requirement: API-key collection routes preserve trailing-slash behavior

The API-key collection operations MUST serve both `/api/api-keys` and
`/api/api-keys/` directly. Equivalent route forms MUST use the same
authentication, validation, persistence, and response contracts, and the
unslashed form MUST NOT depend on an HTTP redirect or the dashboard SPA
fallback.

#### Scenario: List API keys through either collection URL

- **WHEN** a dashboard client sends `GET /api/api-keys` or
  `GET /api/api-keys/`
- **THEN** both requests return the same API-key collection response directly
- **AND** neither request returns an HTTP redirect

#### Scenario: Create an API key through either collection URL

- **WHEN** a dashboard client sends the same valid creation payload to
  `POST /api/api-keys` or `POST /api/api-keys/`
- **THEN** both requests run the API-key creation operation directly
- **AND** neither request depends on redirect handling to preserve the request
  body

### Requirement: Previously issued API key compatibility

The system MUST continue authenticating an already-issued API key by its stored
SHA-256 hash regardless of whether its plaintext suffix uses the current
48-character hexadecimal format or the earlier 43-character base64url format.

#### Scenario: Authenticate an already-issued base64url key

- **GIVEN** an API key created before the generated-key format correction has a stored hash
- **WHEN** the client authenticates with that unchanged plaintext key
- **THEN** the system authenticates it through the existing hash lookup
- **AND** the system does not require key rotation or data migration

### Requirement: Subscription-backed transcription reservations survive cancellation safely

The system MUST reserve API-key usage before forwarding an authenticated subscription-backed transcription request, and MUST release that owned reservation exactly once when cancellation interrupts upstream forwarding. The cancellation-deferring release MUST finish despite active AnyIO cancellation. If release persistence succeeds, the reservation MUST reach `released` state and its reserved quota MUST be restored before the original cancellation propagates. If release persistence fails after the existing bounded persistence retries, the system MUST emit cancellation-neutral cleanup diagnostics, MUST propagate the original cancellation, and MUST leave the reservation eligible for stale-reservation reclamation.

#### Scenario: Cancelled subscription transcription releases its reservation

- **GIVEN** a limited API key has created an owned reservation for a subscription-backed transcription request
- **WHEN** cancellation interrupts the request while upstream transcription forwarding is in flight
- **THEN** the request owner finishes releasing the reservation exactly once despite active cancellation
- **AND** the reservation reaches `released` state and its reserved quota is restored
- **AND** the original cancellation propagates after cleanup completes
- **AND** stale-reservation reclamation is not required for that request

#### Scenario: Failed cancellation release remains recoverable

- **GIVEN** cancellation interrupts a limited subscription-backed transcription request after its reservation is created
- **AND** the immediate release attempt exhausts the existing bounded persistence retries
- **WHEN** release persistence reports failure
- **THEN** the proxy emits cancellation-neutral cleanup diagnostics
- **AND** the original cancellation propagates
- **AND** stale-reservation reclamation remains eligible to release the reservation and restore its reserved quota

### Requirement: One-time API-key secret responses prevent storage

Every successful response containing a full plain API key MUST include
`Cache-Control: no-store, no-cache, must-revalidate, private`,
`Pragma: no-cache`, and `Expires: 0`. This applies to both create URL forms and
regeneration. The policy MUST NOT alter payload, generation, persistence,
authorization, errors, or logging; plain keys MUST remain absent from logs.

#### Scenario: Create through either collection URL

- **WHEN** an authorized admin creates a key through either URL form
- **THEN** all three directives are present
- **AND** the existing one-time plain-key payload remains

#### Scenario: Regenerate a key

- **WHEN** an authorized admin regenerates a key
- **THEN** all three directives are present
- **AND** the existing regenerated-key payload remains

#### Scenario: Unauthorized write stays rejected

- **WHEN** a read-only principal attempts create or regenerate
- **THEN** existing 403 behavior remains
- **AND** no plain key or secret-response headers are returned

### Requirement: API key 7-day account-cost queries use an index-supported filter phase

The database SHALL provide an index that supports filtering request logs by API key and 7-day requested-at range for the API-key account-cost breakdown. The filter columns (`api_key_id`, `requested_at`) MUST be the leading key columns of a maintained index; the `account_id` grouping column MAY be fetched from the heap, since the per-key 7-day window bounds the row count and production plan evidence showed the wider `(api_key_id, requested_at, account_id)` variant was never selected by the planner (`pg_stat_user_indexes.idx_scan = 0`).

#### Scenario: Account-cost filter is index-supported after migration
- **WHEN** database migrations are applied
- **THEN** the `request_logs` table includes an index whose leading key columns are `api_key_id` and descending `requested_at`
- **AND** the 7-day account-cost breakdown query for an API key is satisfiable by that index for its filter phase


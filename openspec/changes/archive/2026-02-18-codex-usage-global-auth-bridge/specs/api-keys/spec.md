## MODIFIED Requirements

### Requirement: API Key Bearer authentication middleware

The system SHALL validate API keys on proxy routes (`/v1/*`, `/backend-api/codex/*`) when `api_key_auth_enabled` is true. Validation MUST compute `sha256` of the Bearer token and look up the hash in the `api_keys` table.

`/api/codex/usage` SHALL NOT be covered by API key middleware scope.

#### Scenario: API key middleware route scope

- **WHEN** `api_key_auth_enabled` is true and a request is made to `/v1/responses` or `/backend-api/codex/responses`
- **THEN** API key middleware validation is applied

#### Scenario: Codex usage excluded from API key middleware scope

- **WHEN** `api_key_auth_enabled` is true and a request is made to `/api/codex/usage`
- **THEN** API key middleware validation is not applied

## ADDED Requirements

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

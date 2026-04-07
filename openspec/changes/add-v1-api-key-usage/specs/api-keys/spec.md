## ADDED Requirements

### Requirement: API keys can read their own `/v1/usage`

The system SHALL expose `GET /v1/usage` for self-service usage lookup by API-key clients. The route MUST require a valid `Authorization: Bearer sk-clb-...` header even when `api_key_auth_enabled` is false globally. The response MUST include only data for the authenticated key and MUST return:

- `request_count`
- `total_tokens`
- `cached_input_tokens`
- `total_cost_usd`
- `limits[]` containing `limit_type`, `limit_window`, `max_value`, `current_value`, `remaining_value`, `model_filter`, and `reset_at`

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

#### Scenario: Self-usage works while global proxy auth is disabled

- **WHEN** `api_key_auth_enabled` is false and a client calls `GET /v1/usage` with a valid Bearer key
- **THEN** the system still authenticates that key and returns the self-usage payload

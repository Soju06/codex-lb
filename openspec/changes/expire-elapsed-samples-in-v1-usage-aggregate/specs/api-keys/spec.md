## MODIFIED Requirements

### Requirement: API keys can read their own `/v1/usage`

The system SHALL expose `GET /v1/usage` for self-service usage lookup by API-key clients. The route MUST require a valid API key in the `Authorization` header using the Bearer authentication scheme even when `api_key_auth_enabled` is false globally. The response MUST include only data for the authenticated key and explicitly visible aggregate upstream quota sections, and MUST return:

- `request_count`
- `total_tokens`
- `cached_input_tokens`
- `total_cost_usd`
- `limits[]` containing limits configured on the authenticated API key, with `limit_type`, `limit_window`, `max_value`, `current_value`, `remaining_value`, `model_filter`, `reset_at`, and `source`. When no API-key limits are configured and aggregate upstream quota details are visible to the caller, `limits[]` MAY mirror those aggregate upstream credit windows for legacy client compatibility.
- `upstream_limits[]` containing aggregate upstream Codex credit windows when available, with the same fields and `source: "aggregate"`, subject to the key's `usage_sections` containing `upstream_limits`. An account's latest sample whose `reset_at` has elapsed MUST count as a reset window (0% used, no reset time) before aggregation, so an account that stopped reporting a window cannot pin the aggregate `reset_at` or `current_value` to a past window.
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

#### Scenario: Elapsed samples do not pin the aggregate reset

- **GIVEN** account A reports a live 5h sample resetting in four hours
- **AND** account B stopped reporting the 5h window and its last sample reset nineteen days ago at 99% used
- **WHEN** an API key with visible upstream limits calls `GET /v1/usage`
- **THEN** the aggregate 5h `reset_at` is account A's reset
- **AND** account B contributes no used credits to the aggregate 5h `current_value`

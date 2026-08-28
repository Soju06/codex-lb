## ADDED Requirements

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

## ADDED Requirements

### Requirement: Warmup endpoint is exposed on the v1 proxy surface
The system SHALL expose `POST /v1/warmup` on the same authenticated proxy surface as other `/v1/*` routes. The endpoint SHALL accept a JSON body with `mode` and SHALL return a structured JSON summary of submitted, skipped, and failed account warmups.

#### Scenario: Authenticated warmup request succeeds
- **WHEN** a client calls `POST /v1/warmup` with a valid API key and valid mode
- **THEN** the system returns 200 with a per-account warmup result summary

#### Scenario: Invalid mode is rejected
- **WHEN** a client calls `POST /v1/warmup` with an unsupported mode value
- **THEN** the system returns a 400 invalid request error

### Requirement: Warmup target pool is derived from API-key account scope
The warmup target pool SHALL be derived from the authenticated API key. If `account_assignment_scope_enabled=true`, only assigned accounts SHALL be considered. If account scope is not enabled, all active accounts SHALL be considered.

#### Scenario: Scoped API key warms only assigned accounts
- **WHEN** an API key has account scope enabled with assigned accounts
- **THEN** warmup only evaluates and submits requests for those assigned accounts

#### Scenario: Unscoped API key warms all active accounts
- **WHEN** an API key has account scope disabled
- **THEN** warmup evaluates and submits requests against all active accounts

### Requirement: Warmup mode semantics are deterministic
The endpoint SHALL implement three warmup modes with deterministic behavior:
- `default`: submit warmup only for accounts that have a primary (5h) usage row and 100% remaining primary usage.
- `all-or-none`: if any target account fails the same eligibility check, reject the entire request and submit no warmups.
- `force-update`: submit warmup for all target accounts regardless of usage.

An account SHALL be considered eligible for `default` and `all-or-none` only when:
- a primary usage row exists,
- `window_minutes=300`, and
- remaining usage is 100% (used percent is 0).

#### Scenario: Default mode skips ineligible accounts
- **WHEN** a `default` warmup request includes eligible and ineligible accounts
- **THEN** only eligible accounts are submitted and ineligible accounts are returned as skipped

#### Scenario: All-or-none rejects mixed eligibility pool
- **WHEN** an `all-or-none` warmup request includes any ineligible account
- **THEN** the system rejects the request and submits zero warmup upstream requests

#### Scenario: Force-update bypasses usage eligibility
- **WHEN** a `force-update` request is submitted
- **THEN** the system submits warmup requests for every target account regardless of usage state

### Requirement: Warmup sends minimal upstream responses request
For each submitted account, the system SHALL send a minimal upstream Responses API request intended to warm transport/session/model path behavior using the configured warmup model. The configured `warmup_model` setting SHALL be used as the upstream `model` value for that warmup submission, and `warmup_model` SHALL NOT be sent as an upstream field. The warmup request SHALL remain small and deterministic. Submissions SHALL run in parallel with a maximum concurrency of 5 accounts per warmup execution.

#### Scenario: Warmup request uses configured warmup model
- **WHEN** the warmup model setting is configured
- **THEN** submitted warmup upstream requests set upstream `model` to that configured value and do not include a `warmup_model` upstream field

#### Scenario: Warmup fan-out is concurrency bounded
- **WHEN** a warmup execution submits requests for more than five accounts
- **THEN** the service runs warmup submissions in parallel while ensuring no more than five account submissions are in flight at once

### Requirement: Warmup model is operator configurable
The system SHALL store a configurable warmup model in dashboard settings with default value `gpt-5.4-mini`. The value SHALL be readable and updatable through existing settings APIs and dashboard settings UI.

#### Scenario: Warmup model defaults on first settings creation
- **WHEN** dashboard settings row is created for the first time
- **THEN** `warmup_model` is persisted as `gpt-5.4-mini`

#### Scenario: Warmup model update is persisted
- **WHEN** an operator updates warmup model through settings API
- **THEN** subsequent warmup requests use the updated model

### Requirement: Warmup requests are visible in request logs
Warmup executions SHALL be persisted in `request_logs` with a distinct request kind marker so dashboard request log views can identify warmup traffic as warmup.

#### Scenario: Warmup rows appear with warmup marker
- **WHEN** a warmup request is submitted for an account
- **THEN** the resulting request log row is labeled as warmup in request log responses

### Requirement: Warmup traffic is excluded from aggregate accounting
Warmup request rows SHALL be excluded from aggregate dashboard request/error/cost metrics and from API-key usage accounting, including key summaries and key trend queries. Warmup rows SHALL remain queryable in request-log list views.

#### Scenario: Dashboard aggregates ignore warmup rows
- **WHEN** dashboard overview aggregates are computed for a timeframe containing warmup and normal traffic
- **THEN** only non-warmup rows contribute to aggregate request/error/token/cost metrics

#### Scenario: API key usage summaries ignore warmup rows
- **WHEN** API key usage summary/trend endpoints are queried for a key with warmup and normal rows
- **THEN** warmup rows do not contribute to API key request/token/cost usage totals

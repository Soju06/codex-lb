## ADDED Requirements

### Requirement: WebSocket terminal auth failures recover before visible output

When a Codex or OpenAI-compatible Responses WebSocket request receives an upstream terminal `response.failed` or `error` before downstream-visible output with `error.code = "invalid_api_key"` or `error.type = "authentication_error"`, the proxy MUST treat the failure as account-local auth state instead of immediately surfacing the terminal event. The proxy MUST preserve the existing no-replay rule after downstream-visible output or for non-replayable continuation requests.

#### Scenario: Session-ended WebSocket auth failure uses another account

- **GIVEN** at least two accounts are eligible for a WebSocket `response.create` request
- **AND** the selected account returns a pre-visible terminal auth failure whose message says the session ended or asks the user to log in again
- **WHEN** another eligible account can complete the request
- **THEN** the downstream WebSocket response succeeds from the other account
- **AND** the selected account is marked re-authentication-required and excluded from that replay

#### Scenario: Generic WebSocket auth failure refreshes once before failover

- **GIVEN** at least two accounts are eligible for a WebSocket `response.create` request
- **AND** the selected account returns a pre-visible terminal `invalid_api_key` failure
- **WHEN** the forced-refresh replay on the selected account also returns a pre-visible terminal `invalid_api_key` failure
- **THEN** the proxy excludes the selected account and tries another eligible account
- **AND** the downstream WebSocket response succeeds from the other account when it completes

#### Scenario: WebSocket auth failure after visible output is not replayed

- **GIVEN** a WebSocket response has emitted downstream-visible output
- **WHEN** upstream later returns a terminal `invalid_api_key` or `authentication_error`
- **THEN** the proxy MUST surface the terminal error without replaying the request on another account

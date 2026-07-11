## ADDED Requirements

### Requirement: Codex usage exposes reset-credit availability

codex-lb SHALL include upstream reset-credit availability on Codex usage
responses when ChatGPT usage identity validation returns earned usage limit
reset credits, without altering aggregate usage-window semantics.

#### Scenario: Usage response carries reset credits

- **GIVEN** a registered active `chatgpt-account-id`
- **AND** upstream `/wham/usage` returns `rate_limit_reset_credits.available_count`
- **WHEN** the caller requests `/api/codex/usage` with a ChatGPT bearer token
- **THEN** codex-lb returns a successful Codex usage payload
- **AND** the top-level `rate_limit_reset_credits.available_count` equals the upstream value

### Requirement: Codex usage can consume upstream reset credits

codex-lb SHALL expose a Codex-compatible endpoint for consuming one upstream
usage limit reset credit. The endpoint SHALL require ChatGPT caller identity,
forward the caller's bearer token and `chatgpt-account-id`, preserve the
caller-provided `redeem_request_id`, and return the upstream consume outcome.

#### Scenario: Reset credit consume succeeds

- **GIVEN** a registered active `chatgpt-account-id`
- **AND** upstream reset-credit consume returns `code: reset`
- **WHEN** the caller posts to `/api/codex/rate-limit-reset-credits/consume` with `redeem_request_id`
- **THEN** codex-lb returns `code: reset`
- **AND** codex-lb force-refreshes the matching account usage snapshot
- **AND** the force-refresh runs even when background usage refresh scheduling is disabled

#### Scenario: API-key caller cannot consume ChatGPT reset credits

- **GIVEN** a codex-lb API key caller without ChatGPT caller identity
- **WHEN** the caller posts to `/api/codex/rate-limit-reset-credits/consume`
- **THEN** codex-lb rejects the request as unauthenticated for ChatGPT reset credits

#### Scenario: Empty redemption id is rejected

- **GIVEN** a registered active `chatgpt-account-id`
- **WHEN** the caller posts to `/api/codex/rate-limit-reset-credits/consume` with an empty `redeem_request_id`
- **THEN** codex-lb rejects the request without forwarding it upstream

### Requirement: Account details expose reset-credit availability

codex-lb SHALL expose upstream usage limit reset-credit availability for a
selected dashboard account without creating local reset-credit accounting.

#### Scenario: Dashboard account detail shows reset credits

- **GIVEN** a registered active account with a `chatgpt-account-id`
- **AND** upstream `/wham/usage` returns `rate_limit_reset_credits.available_count`
- **WHEN** the dashboard requests the account's reset-credit summary
- **THEN** codex-lb returns the selected account id
- **AND** `rate_limit_reset_credits.available_count` equals the upstream value

### Requirement: Dashboard account details can consume reset credits

codex-lb SHALL expose a dashboard write-authorized endpoint for consuming one
upstream usage limit reset credit for a selected account. The endpoint SHALL use
the selected account's stored ChatGPT access token and `chatgpt-account-id`,
generate a non-empty `redeem_request_id`, return the upstream consume outcome,
and force-refresh the matching account usage snapshot after successful or
idempotently successful consume.

#### Scenario: Dashboard reset credit consume succeeds

- **GIVEN** a registered active dashboard account with a `chatgpt-account-id`
- **AND** upstream reset-credit consume returns `code: reset`
- **WHEN** the dashboard posts to the selected account reset-credit consume endpoint
- **THEN** codex-lb forwards a non-empty `redeem_request_id` upstream
- **AND** codex-lb returns `code: reset`
- **AND** codex-lb force-refreshes the matching account usage snapshot
- **AND** the force-refresh runs even when background usage refresh scheduling is disabled

#### Scenario: Read-only dashboard cannot consume reset credits

- **GIVEN** a read-only dashboard session
- **WHEN** the dashboard posts to the selected account reset-credit consume endpoint
- **THEN** codex-lb rejects the request without consuming a reset credit

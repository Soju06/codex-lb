## MODIFIED Requirements

### Requirement: Pool usage exhaustion is reported as a usage-limit error

The proxy MUST report pool-wide Responses usage exhaustion as a usage-limit
error. When every account eligible for a Responses request is exhausted by known
usage windows, the proxy MUST reject the request with HTTP `429` and an
OpenAI-style error envelope whose `error.code` and `error.type` are both
`usage_limit_reached`. If account selection has an authoritative upstream reset
timestamp for the exhausted pool, the response envelope MUST include that
timestamp as `error.resets_at`; the proxy MUST NOT expose the capped
human-facing retry hint or a synthesized fallback as `error.resets_at`. The
proxy MUST NOT collapse this condition into generic `no_accounts`,
`server_error`, or HTTP `503` semantics. Exhaustion classification MUST be
based on structured account state after the same eligibility filtering as
ordinary selection, and MUST NOT reclassify local capacity or overload codes
(account caps, admission gates, fair-share throttles) as usage exhaustion.

#### Scenario: Public Responses request exhausts the eligible usage pool

- **WHEN** account selection for a public `/v1/responses` or
  `/backend-api/codex/responses` request finds only usage-exhausted eligible
  accounts
- **THEN** the response status is HTTP `429`
- **AND** the response body has `error.code = "usage_limit_reached"`
- **AND** the response body has `error.type = "usage_limit_reached"`
- **AND** any selected pool reset timestamp is surfaced as `error.resets_at`

#### Scenario: Streaming selection failure preserves usage-limit semantics

- **WHEN** a streaming Responses request cannot select an account because every
  eligible account is usage-exhausted before downstream-visible output
- **THEN** the terminal error event uses `usage_limit_reached`
- **AND** clients do not receive a generic no-account/server-unavailable error

#### Scenario: Usage-limit selection failures are terminal, not waitable

- **WHEN** account selection fails with `usage_limit_reached` on a streaming,
  HTTP-bridge, or WebSocket Responses path
- **THEN** the proxy reports the structured usage-limit failure immediately
- **AND** it does not enter an account-capacity recovery wait for the
  remaining request budget before reporting it

#### Scenario: HTTP bridge retry loops do not wait on the usage-limit retry hint

- **GIVEN** HTTP bridge session creation or submission fails with
  `usage_limit_reached` whose message carries the selector's capped retry hint
  (`Rate limit exceeded. Try again in Ns`) because the exhausted pool's
  earliest reset is known
- **WHEN** the bridge retry loop evaluates its account-capacity wait plan
- **THEN** it derives no wait from that hint, keyed on the structured error
  code rather than the message text
- **AND** it emits no `codex.keepalive` with status
  `waiting_for_account_capacity` and consumes none of the bridge request budget
- **AND** it returns the HTTP `429` `usage_limit_reached` envelope immediately
  with `error.resets_at` and without a `Retry-After` header
- **AND** recoverable codes such as upstream `rate_limit_exceeded`, local
  account caps, and `response_create_gate_timeout` keep their bounded
  account-capacity wait

#### Scenario: Local capacity codes keep their rate-limit contract

- **WHEN** account selection fails with a local capacity or overload code such
  as `account_stream_cap` or `account_response_create_cap`
- **THEN** the response keeps HTTP `429` with `error.type = "rate_limit_error"`
  and the stable local error code
- **AND** the response is not reported as `usage_limit_reached`

#### Scenario: Unusable non-exhausted pools keep existing semantics

- **WHEN** every account is paused, deactivated, or requires re-authentication
  and no eligible account is exhausted by a known usage window
- **THEN** the pre-existing `no_accounts` failure semantics are preserved

#### Scenario: Owner-scoped exhaustion preserves continuity semantics

- **WHEN** a request is pinned to a previous-response or file owner account and
  only that owner is usage-exhausted while the wider eligible pool is usable
- **THEN** the proxy keeps the existing continuity-owner failure semantics
- **AND** it does not report pool-wide `usage_limit_reached`

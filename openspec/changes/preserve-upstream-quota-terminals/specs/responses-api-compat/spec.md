## MODIFIED Requirements

### Requirement: Failed precreated HTTP bridge replay retires stale sessions

When an HTTP bridge request is still pending before upstream `response.completed` and the upstream websocket closes or times out before the pending request can be completed, the service MUST fail the pending request terminally and retire the affected bridge session if precreated replay does not reconnect and resend successfully. When the replay was attempted and failed, the terminal the client observes MUST preserve the original upstream failure if upstream had already answered with a status-bearing 429 carrying a quota or rate-limit code, and MUST otherwise fail closed as the synthetic `stream_incomplete` terminal. A preserved terminal MUST carry that upstream error code and its quota metadata, and MUST NOT leave the synthetic status or error overrides in place.
#### Scenario: Precreated replay fails after upstream disconnect

- **WHEN** an HTTP bridge request is pending before `response.completed`
- **AND** the upstream websocket closes before the request completes
- **AND** precreated replay fails to reconnect and resend the request
- **THEN** the pending request is removed from the bridge queue
- **AND** the per-session response-create gate is released
- **AND** the bridge session is closed and removed from local reuse
- **AND** the terminal error preserves the original failure code such as `stream_incomplete` or `upstream_request_timeout`

#### Scenario: Terminal logging failure does not preserve stale bridge ownership

- **WHEN** a failed pending HTTP bridge request is being logged as terminal
- **AND** request-log writing fails
- **THEN** the service still removes the stale bridge session from local reuse
- **AND** the service releases any durable bridge ownership for that stale session

#### Scenario: Concurrent waiter cannot submit on retired stale bridge

- **WHEN** an HTTP bridge request is waiting on a session response-create gate
- **AND** the upstream reader retires that same bridge session after a failed precreated replay
- **THEN** the waiting request or prewarm is rejected before it is appended to pending requests or sent upstream
- **AND** the retired bridge session remains closed and removed from local reuse
- **AND** the post-admission ownership check, pending enqueue, and upstream send are mutually exclusive with stale-session retirement

#### Scenario: Unregistered stale bridge reference cannot submit after admission

- **WHEN** an HTTP bridge request or prewarm holds a stale bridge session reference
- **AND** that bridge session is no longer the registered local owner for its session key
- **THEN** the request is rejected after response-create gate admission and before it is appended or sent upstream
- **AND** response-create gate and admission state acquired by the rejected request is released

#### Scenario: Unregistered closed bridge reference cannot reconnect

- **WHEN** an HTTP bridge request holds a closed stale bridge session reference
- **AND** that bridge session is no longer the registered local owner for its session key
- **THEN** the request is rejected before attempting to reconnect the stale bridge upstream

#### Scenario: Reader crash closes bridge before releasing pending gate

- **WHEN** an HTTP bridge upstream reader crashes while a pending request owns the response-create gate
- **AND** another request or prewarm is waiting on that same gate
- **THEN** the crashed bridge session is marked closed before the pending request gate is released
- **AND** the waiting request or prewarm cannot submit on the crashed bridge
- **AND** the crashed bridge session is removed from local reuse and its upstream resources are closed

#### Scenario: Prewarm cleanup does not consume visible queue slots

- **WHEN** a prewarm request is rejected or interrupted after response-create gate admission
- **AND** a visible HTTP bridge request is still counted in the session queue
- **THEN** prewarm cleanup releases its response-create gate and admission state
- **AND** the visible request queue count is preserved

#### Scenario: A failed replay keeps the status-bearing quota terminal

- **GIVEN** a pre-created bridge request whose upstream terminal is a 429 error
  carrying `rate_limit_exceeded`, `usage_limit_reached`, `insufficient_quota`,
  `usage_not_included`, or `quota_exceeded`
- **WHEN** the bounded pre-created replay is attempted and fails
- **THEN** the client receives the upstream code and error type unchanged
- **AND** the terminal carries the upstream `resets_at` and any other quota
  metadata the upstream envelope provided
- **AND** no synthetic 502 `stream_incomplete` status or code override remains
  on the request state

#### Scenario: A failed replay without a quota terminal stays fail-closed

- **GIVEN** a pre-created bridge request whose upstream terminal carries no
  status-bearing quota answer, such as a capacity or overload code
- **WHEN** the bounded pre-created replay is attempted and fails
- **THEN** the client receives the synthetic `stream_incomplete` terminal
- **AND** the request state keeps its 502 status override

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
(account caps, admission gates, fair-share throttles) as usage exhaustion. A terminal `response.failed` stream event surfaced for a retryable stream error whose envelope carries a reset timestamp MUST carry that timestamp as `error.resets_at`, whichever post-refresh branch renders the terminal.
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

#### Scenario: Post-refresh retry exhaustion preserves the reset hint

- **GIVEN** a streaming Responses request whose post-refresh attempt fails with
  a retryable upstream error envelope carrying `resets_at`
- **AND** same-account retries are exhausted with no other account to try
- **WHEN** the proxy renders the terminal `response.failed` event
- **THEN** the event's `error.resets_at` equals the upstream timestamp

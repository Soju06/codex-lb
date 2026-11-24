## ADDED Requirements

### Requirement: Probe completion is opt-in

`POST /api/accounts/{account_id}/probe` MUST accept an optional `mode` restricted
to `wakeup` and `completion`, defaulting to `wakeup`. Omitted mode and explicit
`wakeup` MUST preserve the existing limiter-wakeup behavior and snapshots.
The optional model string MUST be at most 128 characters.

#### Scenario: Legacy wakeup request
- **WHEN** mode is omitted or is `wakeup`
- **THEN** the endpoint preserves the existing wakeup request and snapshot contract

#### Scenario: Oversized model is refused before dispatch
- **WHEN** either mode is requested with a model longer than 128 characters
- **THEN** the endpoint returns HTTP 422 without invoking the probe service

### Requirement: Completion probes use bounded account-specific execution

Completion mode MUST use the normal `stream_responses` HTTP transport with only
the named account's credentials and resolved proxy route. It MUST NOT select
another account. Its fixed minimal request MUST omit unsupported fields and
reasoning/text overrides. Execution time, SSE event size and total stream size
MUST be bounded. Success MUST require `response.completed` on a successful HTTP
response, and resources MUST be closed on every exit.

#### Scenario: Fragmented successful stream
- **WHEN** a completion-mode stream delivers a `response.completed` event across fragmented reads
- **THEN** the result has outcome `completed`, the resolved model and measured latency
- **AND** the upstream stream is closed before the test returns

#### Scenario: HTTP 200 carries a failure
- **WHEN** upstream returns HTTP 200 and then `response.failed`, `response.incomplete` or `error`
- **THEN** the result retains `probeStatusCode` 200 and reports an unsuccessful outcome
- **AND** advisory settlement does not record probe success

#### Scenario: Stream ends or times out
- **WHEN** the stream closes without a terminal event or exceeds its fixed deadline
- **THEN** the result has outcome `incomplete` with a content-free error description
- **AND** resources are released and advisory success is not recorded

#### Scenario: Account proxy route is unavailable
- **WHEN** the selected account's required proxy route cannot be resolved
- **THEN** no upstream request or other-account failover occurs
- **AND** the result reports failure with HTTP status zero

### Requirement: Completion probes expose sanitized diagnostics

Responses MUST retain usage and account snapshots and actual upstream HTTP
`probeStatusCode` (zero without a response). They MUST add optional, nullable
`model`, `outcome` (`completed`, `failed`, `incomplete`), `latencyMs`, `errorCode`
and `errorMessage` fields. Diagnostic fields MUST NOT expose response content,
credentials, proxy addresses or raw exception text.

#### Scenario: Diagnostic response fields
- **WHEN** a completion test finishes
- **THEN** the response includes the resolved model, outcome and measured latency alongside snapshots
- **AND** terminal error descriptions contain only bounded, sanitized diagnostics

### Requirement: Completion error details preserve safe upstream distinctions

Terminal errors MUST preserve safe upstream error codes and sanitized messages
bounded to 512 characters. Sanitization MUST remove access tokens, Bearer
credentials, API keys and JWTs. Non-success HTTP error reads MUST be capped at
64 KiB and MUST NOT expose unstructured response text.

#### Scenario: HTTP 200 distinguishes auth and quota errors
- **WHEN** an HTTP 200 terminal contains error code `token_expired` or `usage_limit_reached`
- **THEN** the result preserves that code and reports outcome `failed`
- **AND** credential-bearing error messages are redacted before returning

#### Scenario: HTTP errors contain a structured code
- **WHEN** a non-success HTTP response contains a structured error within the fixed read limit
- **THEN** the result preserves the actual status and safe upstream error code
- **AND** oversized, unstructured or unreadable bodies use a fixed diagnostic

### Requirement: Completion probe failures do not settle as successes

Failed or incomplete completion tests MUST NOT be recorded as successful advisory
probes, including when the observed upstream HTTP status is 200.

#### Scenario: Incomplete HTTP 200 does not recover advisory health
- **WHEN** an HTTP 200 completion test fails or stops before completion
- **THEN** the endpoint does not record a successful advisory probe

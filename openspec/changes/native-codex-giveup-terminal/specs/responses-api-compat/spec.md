# responses-api-compat Delta

## MODIFIED Requirements

### Requirement: Native Codex preserves upstream failure lifecycle

For a native Codex HTTP/SSE Responses request, a raw upstream transport timeout
or stream EOF without a terminal Responses event and without an internal
retry-exhaustion marker MUST terminate the downstream stream without
synthesizing `response.failed`, `error`, or `[DONE]`. The proxy MUST still
execute reservation, request-log, account-health, and owned-resource cleanup
before propagating the termination. Non-native and OpenAI-compatible clients
MUST retain the existing stable terminal-error shaping.

When codex-lb has exhausted its own transport retries or account replays and
marks the resulting transport terminal with
`_codex_lb_synthetic_transport_failure`, a native Codex HTTP/SSE Responses
request MUST instead receive exactly one terminal `response.failed` event. Its
`error.code` MUST be `rate_limit_exceeded`, and its message MUST begin with a
bounded `Please try again in <N>s.` hint followed by the original upstream code
and message. The egress code and message MUST NOT replace the original
transport code used by account-health, request-log, reservation, or selection
classification. The marker MUST NOT be exposed to the client, and an upstream
`[DONE]` after the marked terminal MUST NOT be forwarded.
After emitting that terminal, every intermediate async stream wrapper MUST
close its inner stream so the retry generator's `finally` handlers release
account leases and unwind the upstream transport.

#### Scenario: Exhausted visible native stream gets one retryable terminal

- **GIVEN** a native Codex HTTP request has received non-terminal upstream SSE
  events and the retry layer exhausts a transport failure
- **WHEN** the retry layer emits one marked synthetic transport terminal
- **THEN** the client receives one `response.failed` with
  `error.code="rate_limit_exceeded"`
- **AND** the message starts with a parseable `Please try again in <N>s.` hint
- **AND** the original transport code remains the internal settlement/log code
- **AND** no marker or `[DONE]` reaches the client

#### Scenario: Exhausted pre-visible native attempts get the same terminal

- **GIVEN** a native Codex HTTP request has no downstream-visible output
- **AND** all eligible transport attempts are exhausted and produce a marked
  synthetic transport terminal
- **WHEN** the public Responses normalizer runs
- **THEN** it emits exactly one retryable `response.failed` event with the same
  code and delay contract

#### Scenario: Native Codex sees a truncated SSE lifecycle

- **GIVEN** a native Codex HTTP request has received a non-terminal SSE event
- **WHEN** upstream closes without a terminal event or retry-exhaustion marker
- **THEN** downstream closes without a synthetic terminal event or `[DONE]`
- **AND** proxy cleanup and failure accounting still complete

#### Scenario: Native give-up closes the lease-holding stream chain

- **GIVEN** a native Codex HTTP request reaches the marked transport terminal
- **WHEN** the public normalizer emits its retryable `response.failed`
- **THEN** the intermediate stream wrappers close their inner generators
- **AND** the retry layer releases the account lease before the request ends

#### Scenario: Local refusal remains a normal terminal

- **GIVEN** the proxy refuses a request before any upstream frame is sent
- **WHEN** it reports the existing local-pre-dispatch refusal
- **THEN** the client receives the existing public refusal code and message
- **AND** it is not relabeled as native retryable transport exhaustion

#### Scenario: Non-native client keeps the terminal umbrella

- **GIVEN** an OpenAI SDK or other non-native client receives the same upstream
  transport terminal
- **WHEN** codex-lb normalizes the stream
- **THEN** the client receives the existing terminal `response.failed` shape

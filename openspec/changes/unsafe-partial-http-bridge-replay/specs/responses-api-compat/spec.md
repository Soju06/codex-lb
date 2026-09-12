## ADDED Requirements

### Requirement: Unsafe partial replay is explicit and one-shot

The HTTP Responses bridge MUST keep unsafe partial replay disabled unless the
operator explicitly enables `http_responses_session_bridge_unsafe_partial_replay_enabled`.
When enabled, the proxy MAY rebuild at most one bounded, account-neutral root
request from the durable completed transcript and the interrupted request. It
MUST require the durable operation fence, compare-and-set the expected
recovery generation, and consume the retry authorization before dispatching. A
missing, malformed, oversized, or ambiguous transcript MUST remain fail-closed.

#### Scenario: Disabled unsafe replay preserves fail-closed behavior

- **GIVEN** the unsafe partial replay setting is disabled
- **AND** an HTTP bridge response has emitted partial output before transport
  failure
- **WHEN** the bridge handles the failure
- **THEN** it MUST NOT build or dispatch a fresh-root replay
- **AND** existing safe recovery or the existing terminal error is used.

#### Scenario: Enabled replay is consumed after one dispatch

- **GIVEN** the unsafe partial replay setting is enabled
- **AND** the bounded durable transcript is complete
- **AND** no tool call, side effect, malformed output, or duplicate execution
  boundary is present
- **WHEN** the interrupted request is recovered
- **THEN** the operation MUST be atomically rebound and one fresh root MAY be
  dispatched
- **AND** a later close MUST NOT authorize a second unsafe replay.

### Requirement: Partial replay preserves the downstream response identity

If the interrupted upstream attempt already exposed `response.created` or
output to the HTTP client, the replacement stream MUST retain the original
downstream response id. The proxy MUST suppress the replacement
`response.created` event and rewrite replacement response-bearing events to the
retained id. Replacement `sequence_number` values MUST be rewritten so they
are strictly greater than the last sequence number already delivered for the
retained response. A partial replay MUST NOT switch one client response stream
to a second response lifecycle.

#### Scenario: Replacement output keeps the original response id

- **GIVEN** the interrupted attempt has a response id and visible response
  events
- **WHEN** unsafe partial replay starts a replacement upstream request
- **THEN** the downstream replacement events carry the interrupted response id
- **AND** no second `response.created` is emitted for the replacement.

### Requirement: Side-effect ambiguity remains fail-closed

Unsafe partial replay MUST be rejected when tool calls, pending tool outputs,
malformed output-item events, duplicate or suppressed tool calls, or incomplete
transcript materialization could make execution at-least-once for a side effect.
The proxy MUST retain the existing durable owner and operation fencing for all
accepted replays.

#### Scenario: Unsettled tool work blocks unsafe replay

- **GIVEN** the interrupted response contains a pending function or tool call
- **WHEN** the transport closes before completion
- **THEN** the bridge MUST NOT dispatch an unsafe partial replay
- **AND** it MUST fail closed through the existing recovery error path.

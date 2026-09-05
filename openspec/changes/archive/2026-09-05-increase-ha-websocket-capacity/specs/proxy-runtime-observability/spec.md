## MODIFIED Requirements

### Requirement: Native WebSocket receive failures preserve bounded provenance

When a native upstream WebSocket terminates with an error, the service MUST
preserve the low-cardinality native failure phase in the internal relay message
and MUST persist it to the request-log `failure_phase` field for each affected
request, unless a more specific request-state override already exists. The
public downstream error message MUST remain credential-safe and compatible
with the existing incomplete-stream message contract. Local buffer exhaustion MUST
use the account-neutral `proxy_websocket_buffer_exhausted` error code.

#### Scenario: Native transport failure is diagnosable

- **WHEN** the native helper reports a WebSocket transport failure after the
  connection has opened
- **THEN** the request log stores `failure_phase=transport`
- **AND** the downstream response keeps the existing incomplete-stream error

#### Scenario: Native application-message backpressure is diagnosable

- **WHEN** the raw or decoded queue byte budget is exhausted
- **THEN** the request log stores `failure_phase=consumer_backpressure`
- **AND** `failure_detail` contains only numeric connection/helper queue usage, limits and incoming event bytes
- **AND** the service emits one low-cardinality warning for that receive failure

### Requirement: Native WebSocket diagnostics do not alter failure policy

Recording failure provenance and queue-byte diagnostics MUST NOT override native
WebSocket buffer budgets or the documented retry, account-neutral and settlement
policies. Local buffer exhaustion MUST remain account-neutral and terminal for
already-sent requests; diagnostics MUST NOT enable automatic replay.

#### Scenario: Metadata does not stamp a successful replay

- **WHEN** a failed socket has a safe pre-created replay that is dispatched on a
  replacement connection
- **THEN** the replayed successful request log does not inherit metadata from
  the failed socket

#### Scenario: OpenCode parent session takes precedence

- **GIVEN** a request has user-agent `opencode/1.0`,
  `x-parent-session-id: parent`, `x-opencode-session: child`,
  `x-session-id: fallback`, and `x-session-affinity: affinity`
- **WHEN** request-log client metadata is derived
- **THEN** the conversation ID is `parent`

#### Scenario: Prefix and header matching ignore case

- **GIVEN** a request has user-agent ` CODEX/1.2 ` and header `Thread-Id:
  conv-b`
- **WHEN** request-log client metadata is derived
- **THEN** the conversation ID is `conv-b`

#### Scenario: Unsupported harnesses produce null metadata

- **GIVEN** a request has no user-agent or has an unsupported user-agent and
  includes a configured conversation header
- **WHEN** request-log client metadata is derived
- **THEN** the conversation ID is null
- **AND** the request continues through the proxy unchanged


## ADDED Requirements

### Requirement: Native WebSocket buffering is byte bounded and account neutral

Native WebSocket buffering MUST enforce both per-connection and aggregate helper queue-byte limits across raw and decoded events, including object overhead. The default aggregate budget MUST be 256 MiB and the per-connection budget MUST be 128 MiB. More than 64 small messages within these budgets MUST NOT terminate a healthy socket merely because they arrive in a burst. The reader and message delivery MUST share execution fairly. A slow consumer MUST NOT block another connection's delivery or send acknowledgements on the shared helper.

#### Scenario: Burst arrives before the consumer is scheduled

- **WHEN** more than 64 messages arrive together within the byte budgets
- **THEN** the consumer receives every accepted message in order, including response completion
- **AND** queued byte charges are released after consumption

#### Scenario: A slow socket exceeds its budget

- **WHEN** a socket cannot accept another event within its per-connection or aggregate byte budget
- **THEN** only that socket is cancelled, accepted messages remain ordered before the terminal failure, and diagnostics include the byte usage and limit
- **AND** account health is not penalized for this local pressure
- **AND** unrelated sockets and send acknowledgements continue to progress

#### Scenario: Socket and helper cleanup release resources

- **WHEN** a socket closes or its helper shuts down with buffered data or cancellation work pending
- **THEN** queued charges are released on discard/consumption and cancellation tasks are awaited or cancelled
- **AND** memory retained by a closed socket does not consume future socket capacity

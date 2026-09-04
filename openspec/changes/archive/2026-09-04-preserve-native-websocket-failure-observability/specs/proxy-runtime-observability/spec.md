## ADDED Requirements

### Requirement: Native WebSocket receive failures preserve bounded provenance

When a native upstream WebSocket terminates with an error, the service MUST
preserve the low-cardinality native failure phase in the internal relay message
and MUST persist it to the request-log `failure_phase` field for each affected
request, unless a more specific request-state override already exists. The
public downstream error message MUST remain credential-safe and compatible
with the existing `stream_incomplete` contract.

#### Scenario: Native transport failure is diagnosable

- **WHEN** the native helper reports a WebSocket transport failure after the
  connection has opened
- **THEN** the request log stores `failure_phase=transport`
- **AND** the downstream response keeps the existing incomplete-stream error

#### Scenario: Native application-message backpressure is diagnosable

- **WHEN** the bounded application-message queue overflows
- **THEN** the request log stores `failure_phase=consumer_backpressure`
- **AND** `failure_detail` contains only the queue depth and configured limit
- **AND** the service emits one low-cardinality warning for that receive failure

### Requirement: Native WebSocket diagnostics do not alter failure policy

Failure provenance and queue-depth diagnostics MUST NOT change native WebSocket
queue limits, retry decisions, account-neutral classification, account-health
settlement, or downstream error envelopes.

#### Scenario: Metadata does not stamp a successful replay

- **WHEN** a failed socket has a safe pre-created replay that is dispatched on a
  replacement connection
- **THEN** the replayed successful request log does not inherit metadata from
  the failed socket
